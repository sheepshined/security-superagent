from datetime import datetime

import pytest
from app.domain.models import NormalizedAlert
from pydantic import ValidationError


def test_normalized_alert_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        NormalizedAlert(
            alert_id="alt_1",
            source="mock-xdr",
            external_id="A-1",
            title="test",
            severity="high",
            occurred_at=datetime(2026, 8, 1, 12, 0, 0),
            ingested_at="2026-08-01T12:00:01+00:00",
            raw_ref="mock://alert/1",
        )
