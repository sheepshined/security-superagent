from enum import StrEnum


class RunMode(StrEnum):
    MOCK = "mock"
    PLATFORM = "platform"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    DEGRADED = "DEGRADED"


class ModelHealth(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class Verdict(StrEnum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FactType(StrEnum):
    OBSERVED_FACT = "observed_fact"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"


class ToolRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class UserRole(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    APPROVER = "approver"


class ActionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    DRY_RUN_PASSED = "DRY_RUN_PASSED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"
    ROLLED_BACK = "ROLLED_BACK"
    EXPIRED = "EXPIRED"


class WorkflowVersionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ACTIVE = "active"
