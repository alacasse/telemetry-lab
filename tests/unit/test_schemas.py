from __future__ import annotations

from datetime import UTC, datetime

from packages.schemas.telemetry import build_idempotency_key


def test_idempotency_key_uses_building_zone_and_timestamp() -> None:
    timestamp = datetime(2026, 4, 9, 12, 0, tzinfo=UTC)
    assert (
        build_idempotency_key("building-001", "zone-a", timestamp)
        == "building-001:zone-a:2026-04-09T12:00:00+00:00"
    )
