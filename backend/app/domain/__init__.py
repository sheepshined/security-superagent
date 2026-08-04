"""Domain contracts shared by APIs, Agents, tools and adapters."""

from app.domain.models import (
    ActionProposal,
    AlertRef,
    Evidence,
    JobAccepted,
    JobView,
    NormalizedAlert,
    ScenarioSummary,
    TriageJobRequest,
    TriageResult,
)

__all__ = [
    "ActionProposal",
    "AlertRef",
    "Evidence",
    "JobAccepted",
    "JobView",
    "NormalizedAlert",
    "ScenarioSummary",
    "TriageJobRequest",
    "TriageResult",
]
