"""Run a synthetic-only DeepSeek connectivity check without printing credentials."""

import json

from app.adapters.models import (
    DataClassification,
    DeepSeekModelAdapter,
    ModelMessage,
    ModelRequest,
)
from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise SystemExit("DEEPSEEK_API_KEY is missing from the local .env file")
    system_prompt = "Return one JSON object only."
    system_prompt += " This request contains synthetic test data."
    adapter = DeepSeekModelAdapter(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        allow_real_security_data=settings.allow_external_model_real_data,
    )
    response = adapter.complete(
        ModelRequest(
            messages=[
                ModelMessage(
                    role="system",
                    content=system_prompt,
                ),
                ModelMessage(
                    role="user",
                    content='Return {"status":"ok","purpose":"connectivity_check"} as JSON.',
                ),
            ],
            data_classification=DataClassification.SYNTHETIC,
            response_format={"type": "json_object"},
            max_tokens=100,
        )
    )
    print(
        json.dumps(
            {
                "provider": response.provider,
                "model": response.model,
                "content": response.content,
                "usage": response.usage,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
