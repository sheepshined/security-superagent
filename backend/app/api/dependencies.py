from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.adapters.mock_xdr import MockXDRAdapter
from app.adapters.models import DeepSeekModelAdapter
from app.adapters.response import MockResponseAdapter
from app.agents.approval.graph import ApprovalGraphRunner
from app.agents.triage.graph import TriageGraphRunner
from app.agents.triage.structured import StructuredTriageAnalyzer
from app.core.config import get_settings
from app.domain.enums import UserRole
from app.domain.models import UserView, utc_now
from app.observability.langsmith import LangSmithTracePolicy
from app.persistence.checkpoints import create_sqlite_checkpointer
from app.rag.catalog import KnowledgeCatalog
from app.rag.hybrid import HybridRetriever
from app.rag.index import KnowledgeIndexManager
from app.runtime.jobs import JobRepository, JobRuntime
from app.services.actions import ActionRepository, ActionService
from app.services.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthRepository,
    AuthService,
)
from app.services.workflows import WorkflowRepository, WorkflowService
from app.tools.registry import DEFAULT_TOOL_REGISTRY, ToolRegistry

SESSION_COOKIE = "ssa_session"
CSRF_COOKIE = "ssa_csrf"


@lru_cache
def get_xdr_adapter() -> MockXDRAdapter:
    return MockXDRAdapter()


@lru_cache
def get_response_adapter() -> MockResponseAdapter:
    return MockResponseAdapter()


@lru_cache
def get_tool_registry() -> ToolRegistry:
    return DEFAULT_TOOL_REGISTRY


@lru_cache
def get_job_repository() -> JobRepository:
    return JobRepository(get_settings().database_url)


@lru_cache
def get_auth_repository() -> AuthRepository:
    return AuthRepository(get_settings().database_url)


@lru_cache
def get_action_repository() -> ActionRepository:
    return ActionRepository(get_settings().database_url)


@lru_cache
def get_action_service() -> ActionService:
    return ActionService(
        get_action_repository(),
        get_job_repository(),
        get_tool_registry(),
        get_response_adapter(),
    )


@lru_cache
def get_workflow_repository() -> WorkflowRepository:
    return WorkflowRepository(get_settings().database_url)


@lru_cache
def get_workflow_service() -> WorkflowService:
    return WorkflowService(
        get_workflow_repository(),
        get_tool_registry(),
        get_knowledge_catalog(),
    )


@lru_cache
def get_auth_service() -> AuthService:
    settings = get_settings()
    service = AuthService(
        get_auth_repository(),
        session_hours=settings.session_hours,
    )
    service.bootstrap_admin(
        settings.bootstrap_admin_username,
        settings.bootstrap_admin_password,
    )
    return service


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


def _development_user() -> UserView:
    now = utc_now()
    return UserView(
        user_id="usr_local_auth_disabled",
        username="local-development",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def require_authenticated(
    request: Request,
    auth: AuthServiceDependency,
) -> UserView:
    if not get_settings().auth_required:
        return _development_user()
    try:
        return auth.authenticate(request.cookies.get(SESSION_COOKIE))
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="authentication required") from exc


def require_csrf(
    request: Request,
    auth: AuthServiceDependency,
) -> UserView:
    if not get_settings().auth_required:
        return _development_user()
    try:
        return auth.require_csrf(
            request.cookies.get(SESSION_COOKIE),
            request.headers.get("X-CSRF-Token"),
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="authentication required") from exc
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail="CSRF validation failed") from exc


def require_admin(user: Annotated[UserView, Depends(require_authenticated)]) -> UserView:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="administrator role required")
    return user


@lru_cache
def get_knowledge_catalog() -> KnowledgeCatalog:
    settings = get_settings()
    return KnowledgeCatalog(
        Path(settings.chroma_persist_directory),
        Path(settings.knowledge_source_registry),
    )


@lru_cache
def get_knowledge_index_manager() -> KnowledgeIndexManager:
    settings = get_settings()
    return KnowledgeIndexManager(
        get_knowledge_catalog(),
        embedding_model=settings.knowledge_embedding_model,
        model_cache=Path(settings.fastembed_cache_path),
    )


@lru_cache
def get_retriever() -> HybridRetriever:
    return get_knowledge_index_manager().create_retriever()


@lru_cache
def get_triage_graph_runner() -> TriageGraphRunner:
    settings = get_settings()
    analyzer = None
    if settings.model_provider == "deepseek" and settings.deepseek_api_key:
        analyzer = StructuredTriageAnalyzer(
            DeepSeekModelAdapter(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
                allow_real_security_data=settings.allow_external_model_real_data,
            )
        )
    return TriageGraphRunner(
        xdr=get_xdr_adapter(),
        retriever=get_retriever(),
        trace_policy=LangSmithTracePolicy(settings),
        analyzer=analyzer,
        checkpointer=create_sqlite_checkpointer(settings.langgraph_checkpoint_db),
    )


@lru_cache
def get_approval_graph_runner() -> ApprovalGraphRunner:
    settings = get_settings()
    return ApprovalGraphRunner(
        checkpointer=create_sqlite_checkpointer(settings.langgraph_checkpoint_db)
    )


@lru_cache
def get_job_runtime() -> JobRuntime:
    return JobRuntime(
        get_job_repository(),
        get_triage_graph_runner(),
        hard_timeout_seconds=get_settings().job_hard_timeout_seconds,
    )
