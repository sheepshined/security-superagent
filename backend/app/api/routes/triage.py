import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.agents.triage.graph import TriageGraphRunner
from app.api.dependencies import (
    get_job_repository,
    get_job_runtime,
    get_triage_graph_runner,
    require_csrf,
)
from app.domain.enums import JobStatus
from app.domain.models import (
    JobAccepted,
    JobAuditEvent,
    JobView,
    TraceReplay,
    TriageJobRequest,
    TriageResult,
)
from app.runtime.jobs import (
    IdempotencyConflictError,
    JobNotFoundError,
    JobRepository,
    JobRuntime,
    ResultNotReadyError,
)

router = APIRouter(tags=["triage"])
RuntimeDependency = Annotated[JobRuntime, Depends(get_job_runtime)]
RepositoryDependency = Annotated[JobRepository, Depends(get_job_repository)]
GraphDependency = Annotated[TriageGraphRunner, Depends(get_triage_graph_runner)]


@router.post(
    "/triage/jobs",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
async def create_triage_job(
    request: TriageJobRequest,
    runtime: RuntimeDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    await runtime.start()
    try:
        accepted, _ = runtime.submit(request, idempotency_key=idempotency_key)
        return accepted
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different request",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/jobs/{job_id}/rerun",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
async def rerun_job(job_id: str, runtime: RuntimeDependency) -> JobAccepted:
    await runtime.start()
    try:
        return runtime.rerun(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(
    job_id: str,
    repository: RepositoryDependency,
) -> JobView:
    try:
        return repository.get(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@router.get("/jobs/{job_id}/result", response_model=TriageResult)
def get_job_result(
    job_id: str,
    repository: RepositoryDependency,
) -> TriageResult:
    try:
        return repository.get_result(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except ResultNotReadyError as exc:
        raise HTTPException(status_code=409, detail="job result is not ready") from exc


@router.get("/jobs/{job_id}/events", response_model=list[JobAuditEvent])
def get_job_events(
    job_id: str,
    repository: RepositoryDependency,
) -> list[JobAuditEvent]:
    try:
        return repository.list_audit_events(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@router.get("/jobs/{job_id}/stream")
async def stream_job_events(
    job_id: str,
    request: Request,
    repository: RepositoryDependency,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        repository.get(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc

    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def event_stream():
        cursor = after
        idle_ticks = 0
        terminal = {
            JobStatus.SUCCEEDED,
            JobStatus.DEGRADED,
            JobStatus.FAILED,
            JobStatus.REJECTED,
            JobStatus.EXPIRED,
            JobStatus.CANCELLED,
        }
        while True:
            if await request.is_disconnected():
                return
            events = repository.list_audit_events(job_id, after_event_id=cursor)
            for event in events:
                cursor = event.event_id
                payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                yield f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload}\n\n"
            job = repository.get(job_id)
            if job.status in terminal and not repository.list_audit_events(
                job_id, after_event_id=cursor
            ):
                return
            idle_ticks += 1
            if idle_ticks >= 60:
                idle_ticks = 0
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/traces/{trace_id}", response_model=TraceReplay)
def replay_trace(trace_id: str, graph_runner: GraphDependency) -> TraceReplay:
    replay = graph_runner.trace_replay(trace_id)
    if replay.checkpoint_count == 0:
        raise HTTPException(status_code=404, detail="trace not found")
    return replay
