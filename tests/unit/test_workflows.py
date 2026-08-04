import json
from pathlib import Path

import pytest
from app.domain.enums import UserRole
from app.domain.models import UserView, utc_now
from app.rag.catalog import KnowledgeCatalog
from app.services.workflows import (
    WorkflowRepository,
    WorkflowService,
    WorkflowValidationError,
    default_template,
)
from app.tools.registry import DEFAULT_TOOL_REGISTRY


def _admin() -> UserView:
    now = utc_now()
    return UserView(
        user_id="admin-one",
        username="admin-one",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _service(tmp_path: Path) -> WorkflowService:
    registry_path = tmp_path / "sources.json"
    registry_path.write_text(
        json.dumps(
            [
                {
                    "source_id": source_id,
                    "name": source_id,
                    "source_type": "snapshot",
                    "uri": f"internal://{source_id}",
                    "version": "1",
                    "license": "test",
                    "license_uri": "internal://license",
                }
                for source_id in default_template().default_config.knowledge_source_ids
            ]
        ),
        encoding="utf-8",
    )
    database_url = f"sqlite:///{(tmp_path / 'workflow.sqlite3').as_posix()}"
    return WorkflowService(
        WorkflowRepository(database_url),
        DEFAULT_TOOL_REGISTRY,
        KnowledgeCatalog(tmp_path / "indexes", registry_path),
    )


def test_workflow_version_is_immutable_validated_and_activatable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    template = service.templates()[0]
    first = service.create_version(template.template_id, template.default_config, _admin())
    duplicate = service.create_version(template.template_id, template.default_config, _admin())

    assert duplicate.workflow_version_id == first.workflow_version_id
    validation = service.validate_version(first.workflow_version_id)
    assert validation.valid is True
    active = service.activate(first.workflow_version_id)
    assert active.status.value == "active"
    assert active.config_hash == first.config_hash
    assert service.require_active(first.workflow_version_id) == active


def test_workflow_must_be_active_before_job_binding(tmp_path: Path) -> None:
    service = _service(tmp_path)
    template = service.templates()[0]
    draft = service.create_version(template.template_id, template.default_config, _admin())

    with pytest.raises(WorkflowValidationError, match="must be active"):
        service.require_active(draft.workflow_version_id)


def test_workflow_rejects_disabled_safety_node_and_unknown_tool(tmp_path: Path) -> None:
    service = _service(tmp_path)
    template = service.templates()[0]
    unsafe = template.default_config.model_copy(deep=True)
    unsafe.node_enabled["critic_verify"] = False
    unsafe.tool_allowlist.append("shell.execute")

    result = service.validate_config(unsafe)

    assert result.valid is False
    assert any("critic_verify" in error for error in result.errors)
    assert any("shell.execute" in error for error in result.errors)
    with pytest.raises(WorkflowValidationError):
        service.create_version(template.template_id, unsafe, _admin())
