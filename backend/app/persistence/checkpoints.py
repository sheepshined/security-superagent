import sqlite3
from pathlib import Path

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
    ReActAuditStep,
    TriageResult,
)


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
    saver = SqliteSaver(
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
                ReActAuditStep,
                TriageResult,
            ]
        ),
    )
    saver.setup()
    return saver
