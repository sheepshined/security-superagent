from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.agents.triage.graph import TriageGraphRunner
from app.api.dependencies import (
    get_job_repository,
    get_job_runtime,
    get_triage_graph_runner,
)
from app.domain.models import (
    JobAccepted,
    JobAuditEvent,
    JobView,
    TraceReplay,
    TriageJobRequest,
    TriageResult,
)
from app.runtime.jobs import (
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
)
def create_triage_job(
    request: TriageJobRequest,
    background_tasks: BackgroundTasks,
    runtime: RuntimeDependency,
) -> JobAccepted:
    accepted, created = runtime.submit(request)
    if created:
        background_tasks.add_task(runtime.process, accepted.job_id, request)
    return accepted


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


@router.get("/traces/{trace_id}", response_model=TraceReplay)
def replay_trace(trace_id: str, graph_runner: GraphDependency) -> TraceReplay:
    replay = graph_runner.trace_replay(trace_id)
    if replay.checkpoint_count == 0:
        raise HTTPException(status_code=404, detail="trace not found")
    return replay
