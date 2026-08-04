import asyncio
import sqlite3
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from app.adapters.models import DataClassification
from app.core.config import PROJECT_ROOT
from app.domain.enums import FactType, Severity, ToolRisk, Verdict
from app.domain.models import (
    ActionProposal,
    AlertRef,
    Citation,
    CriticReport,
    Evidence,
    Finding,
    InvestigationPlan,
    InvestigationPlanStep,
    InvestigationTrace,
    NormalizedAlert,
    PlanStepExecution,
    ReActAuditStep,
    ReplanRequest,
    TriageResult,
)


class ThreadedAsyncSqliteSaver(SqliteSaver):
    """Expose SqliteSaver through LangGraph's async checkpoint API.

    SQLite remains local and serialized by SqliteSaver's lock while database work is
    moved off the event loop. This lets the production worker use ``ainvoke`` without
    adding a separate checkpoint service.
    """

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        rows = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for row in rows:
            yield row

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(
            self.put,
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)


def resolve_checkpoint_path(configured_path: str) -> Path:
    path = Path(configured_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def create_sqlite_checkpointer(configured_path: str) -> SqliteSaver:
    path = resolve_checkpoint_path(configured_path)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    saver = ThreadedAsyncSqliteSaver(
        connection,
        serde=JsonPlusSerializer(
            allowed_msgpack_modules=[
                DataClassification,
                FactType,
                Severity,
                ToolRisk,
                Verdict,
                ActionProposal,
                AlertRef,
                Citation,
                CriticReport,
                Evidence,
                Finding,
                InvestigationPlan,
                InvestigationPlanStep,
                InvestigationTrace,
                NormalizedAlert,
                PlanStepExecution,
                ReActAuditStep,
                ReplanRequest,
                TriageResult,
            ]
        ),
    )
    saver.setup()
    return saver
