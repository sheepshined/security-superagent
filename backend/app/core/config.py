from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Security SuperAgent"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_log_level: str = "INFO"

    model_provider: str = "mock"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    allow_external_model_real_data: bool = False

    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "security-superagent-dev"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_allow_real_security_data: bool = False

    xdr_mode: str = "mock"
    xdr_base_url: str = ""
    xdr_api_key: str = ""

    database_url: str = "sqlite:///./data/runtime/security_superagent.sqlite3"
    langgraph_checkpoint_db: str = "./data/runtime/langgraph-checkpoints.sqlite3"
    chroma_persist_directory: str = "./knowledge/index"
    knowledge_source_registry: str = "./knowledge/sources.json"
    knowledge_cache_directory: str = "./data/knowledge-cache"
    fastembed_cache_path: str = "./data/models/fastembed"
    knowledge_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    frontend_origin: str = "http://localhost:5173"

    mcp_enabled: bool = True
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8100, ge=1, le=65535)

    auth_required: bool = True
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""
    session_hours: int = Field(default=8, ge=1, le=24)
    cookie_secure: bool = False
    job_hard_timeout_seconds: int = Field(default=120, ge=30, le=600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
