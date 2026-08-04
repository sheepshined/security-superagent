from enum import StrEnum
from typing import Any, Protocol

import httpx
from langsmith import traceable
from pydantic import Field

from app.domain.models import StrictModel


class DataClassification(StrEnum):
    SYNTHETIC = "synthetic"
    DESENSITIZED = "desensitized"
    REAL_SECURITY_DATA = "real_security_data"


class ModelMessage(StrictModel):
    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str = Field(min_length=1, max_length=100000)


class ModelRequest(StrictModel):
    messages: list[ModelMessage] = Field(min_length=1, max_length=128)
    data_classification: DataClassification
    response_format: dict[str, Any] | None = None
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=2000, ge=1, le=32000)


class ModelResponse(StrictModel):
    content: str
    model: str
    provider: str
    usage: dict[str, int | dict[str, int]] = Field(default_factory=dict)


def _deepseek_trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Serialize model inputs without exposing adapter credentials."""
    request = inputs.get("request")
    model = inputs.get("model")
    if not isinstance(request, ModelRequest):
        return {"model": model, "messages": []}
    return {
        "model": model,
        "messages": [message.model_dump(mode="json") for message in request.messages],
        "response_format": request.response_format,
        "data_classification": request.data_classification.value,
    }


def _deepseek_invocation_params(inputs: dict[str, Any]) -> dict[str, Any]:
    request = inputs.get("request")
    return {
        "ls_provider": "deepseek",
        "ls_model_type": "chat",
        "ls_model_name": inputs.get("model"),
        "ls_temperature": request.temperature if isinstance(request, ModelRequest) else None,
        "ls_max_tokens": request.max_tokens if isinstance(request, ModelRequest) else None,
    }


def _deepseek_trace_outputs(response: Any) -> dict[str, Any]:
    if not isinstance(response, ModelResponse):
        return {"output": None}
    usage = response.usage
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    usage_metadata = {
        "input_tokens": input_tokens if isinstance(input_tokens, int) else 0,
        "output_tokens": output_tokens if isinstance(output_tokens, int) else 0,
        "total_tokens": total_tokens if isinstance(total_tokens, int) else 0,
    }
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": response.content,
                }
            }
        ],
        "model": response.model,
        "provider": response.provider,
        "usage": usage,
        "usage_metadata": usage_metadata,
    }


class ModelAdapter(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


class ExternalModelDataDeniedError(PermissionError):
    pass


class DeepSeekModelAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        allow_real_security_data: bool = False,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeek API key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._allow_real_security_data = allow_real_security_data
        self._timeout_seconds = timeout_seconds

    def complete(self, request: ModelRequest) -> ModelResponse:
        if (
            request.data_classification == DataClassification.REAL_SECURITY_DATA
            and not self._allow_real_security_data
        ):
            raise ExternalModelDataDeniedError(
                "real XDR data is not allowed to leave the approved platform boundary"
            )
        return self._complete_traced(
            request,
            model=self._model,
            langsmith_extra={
                "metadata": {
                    "data_classification": request.data_classification.value,
                },
                "tags": ["deepseek", "external-model"],
            },
        )

    @traceable(
        name="deepseek_chat_completion",
        run_type="llm",
        process_inputs=_deepseek_trace_inputs,
        process_outputs=_deepseek_trace_outputs,
        _invocation_params_fn=_deepseek_invocation_params,
    )
    def _complete_traced(
        self,
        request: ModelRequest,
        *,
        model: str,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_format:
            payload["response_format"] = request.response_format
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        return ModelResponse(
            content=body["choices"][0]["message"]["content"],
            model=body.get("model", model),
            provider="deepseek",
            usage=body.get("usage", {}),
        )


class MockModelAdapter:
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content='{"status":"mock","evidence_policy":"enforced"}',
            model="mock-security-model",
            provider="mock",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
