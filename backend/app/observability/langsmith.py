from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langsmith import Client, tracing_context

from app.adapters.models import DataClassification
from app.core.config import Settings


class LangSmithTracePolicy:
    """Enable tracing only when configuration and data classification both allow it."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_allowed(self, data_classification: DataClassification) -> bool:
        if not self._settings.langsmith_tracing or not self._settings.langsmith_api_key:
            return False
        if data_classification == DataClassification.REAL_SECURITY_DATA:
            return self._settings.langsmith_allow_real_security_data
        return True

    @contextmanager
    def trace(
        self,
        *,
        trace_id: str,
        data_classification: DataClassification,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[bool]:
        enabled = self.is_allowed(data_classification)
        client = (
            Client(
                api_key=self._settings.langsmith_api_key,
                api_url=self._settings.langsmith_endpoint,
            )
            if enabled
            else None
        )
        safe_metadata = {
            "trace_id": trace_id,
            "data_classification": data_classification.value,
            **(metadata or {}),
        }
        with tracing_context(
            enabled=enabled,
            client=client,
            project_name=self._settings.langsmith_project,
            tags=["security-superagent", data_classification.value],
            metadata=safe_metadata,
        ):
            yield enabled
