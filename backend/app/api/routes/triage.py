import asyncio
import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.adapters.mock_xdr import ScenarioNotFoundError
from app.adapters.platform_xdr import XdrAlertNotFoundError
from app.adapters.protocols import XDRAdapter
from app.agents.triage.graph import TriageGraphRunner
from app.api.dependencies import (
    get_job_repository,
    get_job_runtime,
    get_triage_graph_runner,
    get_workflow_service,
    get_xdr_adapter,
    require_external_tool_or_csrf,
)
from app.core.config import Settings, get_settings
from app.domain.enums import JobStatus, RunMode, Severity
from app.domain.models import (
    AlertRef,
    JobAccepted,
    JobAuditEvent,
    JobView,
    TraceReplay,
    TriageJobRequest,
    TriageOptions,
    TriageResult,
    UserView,
)
from app.runtime.jobs import (
    IdempotencyConflictError,
    JobNotFoundError,
    JobRepository,
    JobRuntime,
    ResultNotReadyError,
)
from app.services.workflows import (
    WorkflowNotFoundError,
    WorkflowService,
    WorkflowValidationError,
)

router = APIRouter(tags=["triage"])
RuntimeDependency = Annotated[JobRuntime, Depends(get_job_runtime)]
RepositoryDependency = Annotated[JobRepository, Depends(get_job_repository)]
GraphDependency = Annotated[TriageGraphRunner, Depends(get_triage_graph_runner)]
WorkflowServiceDependency = Annotated[WorkflowService, Depends(get_workflow_service)]
XDRDependency = Annotated[XDRAdapter, Depends(get_xdr_adapter)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/triage/jobs",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_external_tool_or_csrf)],
)
async def create_triage_job(
    request: TriageJobRequest,
    runtime: RuntimeDependency,
    workflows: WorkflowServiceDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    if request.options.workflow_version_id:
        try:
            workflows.require_active(request.options.workflow_version_id)
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workflow version not found") from exc
        except WorkflowValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.result.model_dump(mode="json"),
            ) from exc
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
    dependencies=[Depends(require_external_tool_or_csrf)],
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


TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.DEGRADED,
    JobStatus.FAILED,
    JobStatus.REJECTED,
    JobStatus.EXPIRED,
    JobStatus.CANCELLED,
}
INVESTIGATE_POLL_TIMEOUT_SECONDS = 100.0
INVESTIGATE_POLL_INTERVAL_SECONDS = 0.5


@router.get(
    "/triage/investigate",
    response_model=TriageResult,
    status_code=status.HTTP_200_OK,
)
async def investigate_alert(
    alert_uuid: Annotated[
        str,
        Query(
            min_length=1,
            max_length=128,
            description="平台告警/事件的 uuid,即 scenarios 接口返回的 scenario_id 字段值",
        ),
    ],
    runtime: RuntimeDependency,
    repository: RepositoryDependency,
    xdr: XDRDependency,
    settings: SettingsDependency,
    _: Annotated[UserView, Depends(require_external_tool_or_csrf)],
    analyst_query: Annotated[str, Query(max_length=4000, description="可选,补充调查问题")] = "",
    time_window_minutes: Annotated[str | None, Query(description="可选,时间窗分钟,默认 60")] = None,
) -> TriageResult:
    """One-shot investigation for external tool platforms.

    Deliberately a GET with query parameters: GPTs-style tool platforms
    serialize ``parameters`` reliably only for GET operations (their POST
    tool calls drop arguments entirely). Runs the full triage flow for a
    platform alert and blocks until the job reaches a terminal state,
    returning the final TriageResult. Read-only: no response actions are
    ever triggered here.
    """
    if settings.xdr_mode == "platform":
        source, mode = "sangfor-xdr", RunMode.PLATFORM
    else:
        source, mode = "mock-xdr", RunMode.MOCK
    try:
        xdr.get_alert(AlertRef(source=source, external_id=alert_uuid))
    except (ScenarioNotFoundError, XdrAlertNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="alert not found") from exc

    try:
        time_window = int(time_window_minutes) if time_window_minutes else 60
    except (TypeError, ValueError):
        time_window = 60
    if not 5 <= time_window <= 1440:
        time_window = 60

    await runtime.start()
    try:
        accepted, _ = runtime.submit(
            TriageJobRequest(
                alert_ref=AlertRef(source=source, external_id=alert_uuid),
                mode=mode,
                analyst_query=analyst_query or "",
                options=TriageOptions(
                    time_window_minutes=time_window,
                    allow_superagent_escalation=True,
                    max_risk_level=Severity.HIGH,
                ),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    deadline = time.monotonic() + INVESTIGATE_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        job = repository.get(accepted.job_id)
        if job.status in TERMINAL_JOB_STATUSES:
            if job.status in {JobStatus.SUCCEEDED, JobStatus.DEGRADED}:
                try:
                    return repository.get_result(accepted.job_id)
                except ResultNotReadyError as exc:
                    raise HTTPException(
                        status_code=409, detail="job result is not ready"
                    ) from exc
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"investigation finished with status {job.status.value}",
                    "job_id": accepted.job_id,
                },
            )
        await asyncio.sleep(INVESTIGATE_POLL_INTERVAL_SECONDS)

    raise HTTPException(
        status_code=409,
        detail={
            "message": "investigation still running",
            "job_id": accepted.job_id,
        },
    )
