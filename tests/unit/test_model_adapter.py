import httpx
import pytest
from app.adapters.models import (
    DataClassification,
    DeepSeekModelAdapter,
    ExternalModelDataDeniedError,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    _deepseek_trace_inputs,
    _deepseek_trace_outputs,
)
from pytest import MonkeyPatch


class _FakeDeepSeekResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "model": "deepseek-v4-flash",
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "total_tokens": 19,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        }


class _FakeDeepSeekClient:
    def __enter__(self) -> "_FakeDeepSeekClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, *args: object, **kwargs: object) -> _FakeDeepSeekResponse:
        return _FakeDeepSeekResponse()


class _RetryingDeepSeekClient(_FakeDeepSeekClient):
    def __init__(self) -> None:
        self.calls = 0

    def post(self, *args: object, **kwargs: object) -> _FakeDeepSeekResponse:
        self.calls += 1
        if self.calls == 1:
            raise httpx.ReadTimeout("synthetic timeout")
        return _FakeDeepSeekResponse()


def test_deepseek_adapter_denies_real_security_data_before_network_call() -> None:
    adapter = DeepSeekModelAdapter(
        api_key="test-only-placeholder",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        allow_real_security_data=False,
    )
    request = ModelRequest(
        messages=[ModelMessage(role="user", content="真实 XDR 告警")],
        data_classification=DataClassification.REAL_SECURITY_DATA,
    )

    with pytest.raises(ExternalModelDataDeniedError):
        adapter.complete(request)


def test_deepseek_adapter_accepts_nested_token_usage(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.adapters.models.httpx.Client",
        lambda **kwargs: _FakeDeepSeekClient(),
    )
    adapter = DeepSeekModelAdapter(
        api_key="test-only-placeholder",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )

    response = adapter.complete(
        ModelRequest(
            messages=[ModelMessage(role="user", content="synthetic test")],
            data_classification=DataClassification.SYNTHETIC,
        )
    )

    assert response.usage["prompt_tokens_details"] == {"cached_tokens": 0}
    assert response.usage["completion_tokens_details"] == {"reasoning_tokens": 5}


def test_deepseek_adapter_retries_once_after_timeout(monkeypatch: MonkeyPatch) -> None:
    client = _RetryingDeepSeekClient()
    monkeypatch.setattr("app.adapters.models.httpx.Client", lambda **kwargs: client)
    monkeypatch.setattr("app.adapters.models.time.sleep", lambda *_: None)
    adapter = DeepSeekModelAdapter(
        api_key="test-only-placeholder",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        timeout_seconds=30,
        max_attempts=2,
    )

    response = adapter.complete(
        ModelRequest(
            messages=[ModelMessage(role="user", content="synthetic test")],
            data_classification=DataClassification.SYNTHETIC,
        )
    )

    assert client.calls == 2
    assert response.provider == "deepseek"


def test_deepseek_trace_payload_has_chat_shape_usage_and_no_credentials() -> None:
    request = ModelRequest(
        messages=[ModelMessage(role="user", content="synthetic test")],
        data_classification=DataClassification.SYNTHETIC,
        max_tokens=321,
    )

    inputs = _deepseek_trace_inputs(
        {
            "request": request,
            "model": "deepseek-v4-flash",
            "api_key": "must-not-appear",
        }
    )
    outputs = _deepseek_trace_outputs(
        ModelResponse(
            content='{"status":"ok"}',
            model="deepseek-v4-flash",
            provider="deepseek",
            usage={"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
        )
    )

    assert inputs["messages"] == [{"role": "user", "content": "synthetic test"}]
    assert "api_key" not in inputs
    assert outputs["usage_metadata"] == {
        "input_tokens": 12,
        "output_tokens": 7,
        "total_tokens": 19,
    }
    assert outputs["choices"][0]["message"]["role"] == "assistant"
