from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from threading import RLock

from app.domain.enums import WorkflowVersionStatus
from app.domain.models import (
    UserView,
    WorkflowConfig,
    WorkflowTemplateView,
    WorkflowValidationResult,
    WorkflowVersionView,
    utc_now,
)
from app.persistence.migrations import migrate
from app.persistence.sqlite import create_sqlite_connection
from app.rag.catalog import KnowledgeCatalog
from app.tools.registry import ToolRegistry, ToolRegistryError


class WorkflowNotFoundError(LookupError):
    pass


class WorkflowValidationError(ValueError):
    def __init__(self, result: WorkflowValidationResult) -> None:
        super().__init__("; ".join(result.errors))
        self.result = result


FIXED_NODES = [
    "load_alert",
    "critic_verify",
    "policy_finalize",
    "response_safety_gate",
]
CONFIGURABLE_NODES = [
    "plan_investigation",
    "execute_initial_plan",
    "retrieve_knowledge",
    "structured_assessment",
    "revise_plan",
    "bounded_react",
]
ALLOWED_AGENTS = {
    "network_agent",
    "endpoint_agent",
    "identity_agent",
    "knowledge_agent",
}
DEFAULT_TEMPLATE_ID = "security_triage_controlled_v1"


def default_template() -> WorkflowTemplateView:
    nodes = {node: True for node in [*FIXED_NODES, *CONFIGURABLE_NODES]}
    return WorkflowTemplateView(
        template_id=DEFAULT_TEMPLATE_ID,
        name="受控安全研判工作流",
        description="固定保留 Critic、确定性裁决和响应安全门的模板化工作流。",
        fixed_nodes=FIXED_NODES,
        configurable_nodes=CONFIGURABLE_NODES,
        default_config=WorkflowConfig(
            node_enabled=nodes,
            model="deepseek",
            node_timeout_seconds=30,
            retries=1,
            max_tool_calls=8,
            max_replans=1,
            agents=sorted(ALLOWED_AGENTS),
            tool_allowlist=[
                "query_network_context",
                "query_endpoint_context",
                "query_identity_context",
                "search_security_knowledge",
                "edr.isolate_host",
            ],
            knowledge_source_ids=[
                "mitre-attack-enterprise",
                "cisa-kev",
                "sigma-lateral-movement",
                "synthetic-lateral-movement-playbook",
            ],
        ),
    )


class WorkflowRepository:
    def __init__(self, database_url: str = "sqlite:///:memory:") -> None:
        self._connection = create_sqlite_connection(database_url)
        self._lock = RLock()
        with self._lock:
            migrate(self._connection)

    def create(
        self,
        template_id: str,
        config: WorkflowConfig,
        config_hash: str,
        user: UserView,
    ) -> WorkflowVersionView:
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM workflow_versions WHERE template_id = ? AND config_hash = ?",
                (template_id, config_hash),
            ).fetchone()
            if existing:
                return self._to_view(existing)
            row = self._connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) AS version "
                "FROM workflow_versions WHERE template_id = ?",
                (template_id,),
            ).fetchone()
            version_number = int(row["version"]) + 1
            created_at = utc_now()
            workflow_version_id = f"wfv_{secrets.token_hex(16)}"
            self._connection.execute(
                """
                INSERT INTO workflow_versions(
                    workflow_version_id, template_id, version_number,
                    config_json, config_hash, status, created_by,
                    created_at, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    workflow_version_id,
                    template_id,
                    version_number,
                    config.model_dump_json(),
                    config_hash,
                    WorkflowVersionStatus.DRAFT.value,
                    user.user_id,
                    created_at.isoformat(),
                ),
            )
        return self.get(workflow_version_id)

    def get(self, workflow_version_id: str) -> WorkflowVersionView:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workflow_versions WHERE workflow_version_id = ?",
                (workflow_version_id,),
            ).fetchone()
            if row is None:
                raise WorkflowNotFoundError(workflow_version_id)
            return self._to_view(row)

    def list(self, template_id: str | None = None) -> list[WorkflowVersionView]:
        with self._lock:
            if template_id:
                rows = self._connection.execute(
                    "SELECT * FROM workflow_versions WHERE template_id = ? "
                    "ORDER BY version_number DESC",
                    (template_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM workflow_versions ORDER BY created_at DESC"
                ).fetchall()
            return [self._to_view(row) for row in rows]

    def mark_validated(self, workflow_version_id: str) -> WorkflowVersionView:
        with self._lock, self._connection:
            current = self.get(workflow_version_id)
            if current.status == WorkflowVersionStatus.ACTIVE:
                return current
            self._connection.execute(
                "UPDATE workflow_versions SET status = ? WHERE workflow_version_id = ?",
                (WorkflowVersionStatus.VALIDATED.value, workflow_version_id),
            )
        return self.get(workflow_version_id)

    def activate(self, workflow_version_id: str) -> WorkflowVersionView:
        with self._lock, self._connection:
            current = self.get(workflow_version_id)
            if current.status not in {
                WorkflowVersionStatus.VALIDATED,
                WorkflowVersionStatus.ACTIVE,
            }:
                raise WorkflowValidationError(
                    WorkflowValidationResult(
                        valid=False,
                        errors=["workflow version must be validated before activation"],
                    )
                )
            activated_at = utc_now()
            self._connection.execute(
                "UPDATE workflow_versions SET status = ? WHERE template_id = ? AND status = ?",
                (
                    WorkflowVersionStatus.VALIDATED.value,
                    current.template_id,
                    WorkflowVersionStatus.ACTIVE.value,
                ),
            )
            self._connection.execute(
                "UPDATE workflow_versions SET status = ?, activated_at = ? "
                "WHERE workflow_version_id = ?",
                (
                    WorkflowVersionStatus.ACTIVE.value,
                    activated_at.isoformat(),
                    workflow_version_id,
                ),
            )
        return self.get(workflow_version_id)

    @staticmethod
    def _to_view(row: sqlite3.Row) -> WorkflowVersionView:
        return WorkflowVersionView(
            workflow_version_id=row["workflow_version_id"],
            template_id=row["template_id"],
            version_number=row["version_number"],
            config=WorkflowConfig.model_validate_json(row["config_json"]),
            config_hash=row["config_hash"],
            status=WorkflowVersionStatus(row["status"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
            activated_at=row["activated_at"],
        )


class WorkflowService:
    def __init__(
        self,
        repository: WorkflowRepository,
        registry: ToolRegistry,
        knowledge: KnowledgeCatalog,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.knowledge = knowledge

    def templates(self) -> list[WorkflowTemplateView]:
        return [default_template()]

    def create_version(
        self,
        template_id: str,
        config: WorkflowConfig,
        user: UserView,
    ) -> WorkflowVersionView:
        if template_id != DEFAULT_TEMPLATE_ID:
            raise WorkflowNotFoundError(template_id)
        validation = self.validate_config(config)
        if not validation.valid:
            raise WorkflowValidationError(validation)
        config_hash = hashlib.sha256(
            json.dumps(
                config.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return self.repository.create(template_id, config, config_hash, user)

    def validate_version(self, workflow_version_id: str) -> WorkflowValidationResult:
        version = self.repository.get(workflow_version_id)
        result = self.validate_config(version.config)
        if result.valid:
            self.repository.mark_validated(workflow_version_id)
        return result

    def activate(self, workflow_version_id: str) -> WorkflowVersionView:
        validation = self.validate_version(workflow_version_id)
        if not validation.valid:
            raise WorkflowValidationError(validation)
        return self.repository.activate(workflow_version_id)

    def require_active(self, workflow_version_id: str) -> WorkflowVersionView:
        version = self.repository.get(workflow_version_id)
        if version.status != WorkflowVersionStatus.ACTIVE:
            raise WorkflowValidationError(
                WorkflowValidationResult(
                    valid=False,
                    errors=["workflow version must be active before it can be bound to a job"],
                )
            )
        return version

    def validate_config(self, config: WorkflowConfig) -> WorkflowValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        allowed_nodes = set(FIXED_NODES) | set(CONFIGURABLE_NODES)
        for node in FIXED_NODES:
            if config.node_enabled.get(node) is not True:
                errors.append(f"fixed safety node must remain enabled: {node}")
        unknown_nodes = sorted(set(config.node_enabled) - allowed_nodes)
        if unknown_nodes:
            errors.append(f"unknown workflow nodes: {', '.join(unknown_nodes)}")
        unknown_agents = sorted(set(config.agents) - ALLOWED_AGENTS)
        if unknown_agents:
            errors.append(f"unknown agents: {', '.join(unknown_agents)}")
        for tool_name in config.tool_allowlist:
            try:
                self.registry.get(tool_name)
            except ToolRegistryError:
                errors.append(f"unknown tool: {tool_name}")
        source_ids = {item.source_id for item in self.knowledge.list_sources()}
        unknown_sources = sorted(set(config.knowledge_source_ids) - source_ids)
        if unknown_sources:
            errors.append(f"unknown knowledge sources: {', '.join(unknown_sources)}")
        if not config.knowledge_source_ids:
            warnings.append(
                "workflow has no knowledge sources and will rely on deterministic evidence"
            )
        if config.model not in {"deepseek", "mock"}:
            errors.append("model must be deepseek or mock")
        return WorkflowValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
        )
