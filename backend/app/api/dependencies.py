from functools import lru_cache

from app.adapters.mock_xdr import MockXDRAdapter
from app.adapters.models import DeepSeekModelAdapter
from app.agents.approval.graph import ApprovalGraphRunner
from app.agents.triage.graph import TriageGraphRunner
from app.agents.triage.structured import StructuredTriageAnalyzer
from app.core.config import get_settings
from app.observability.langsmith import LangSmithTracePolicy
from app.persistence.checkpoints import create_sqlite_checkpointer
from app.rag.hybrid import HybridRetriever
from app.runtime.jobs import JobRepository, JobRuntime


@lru_cache
def get_xdr_adapter() -> MockXDRAdapter:
    return MockXDRAdapter()


@lru_cache
def get_job_repository() -> JobRepository:
    return JobRepository(get_settings().database_url)


@lru_cache
def get_retriever() -> HybridRetriever:
    return HybridRetriever()


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
    return JobRuntime(get_job_repository(), get_triage_graph_runner())
