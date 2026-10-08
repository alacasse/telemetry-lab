from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from demo import thermal_local
from packages.thermal.authority import unknown_observation


@pytest.mark.parametrize("value", ["nan", "inf", "0.49", "2.01", "bad"])
def test_invalid_multiplier(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("THERMAL_TIME_MULTIPLIER", value)
    with pytest.raises(ValueError):
        thermal_local.multiplier()


def test_multiplier_defaults_and_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THERMAL_TIME_MULTIPLIER", raising=False)
    assert thermal_local.multiplier() == 1
    for value in ["0.5", "2"]:
        monkeypatch.setenv("THERMAL_TIME_MULTIPLIER", value)
        assert thermal_local.multiplier() == float(value)


def test_database_observation_failure_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = MagicMock(side_effect=OperationalError("observe", {}, Exception("offline")))
    monkeypatch.setattr(thermal_local, "observation_sessions", sessions)
    assert thermal_local.runtime_observation() == unknown_observation()


def test_runtime_observation_preserves_database_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = {
        **unknown_observation(),
        "status": "available",
        "owner_id": "test-owner",
        "generation": 8,
        "process_id": 123,
        "release_revision": "revision",
    }
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    observer = MagicMock(return_value=observed)
    monkeypatch.setattr(thermal_local, "observation_sessions", lambda: factory)
    monkeypatch.setattr(thermal_local, "observe_authority", observer)
    monkeypatch.setenv("THERMAL_RUNTIME_STATE", "/nonexistent/ignored-launcher-state.json")
    assert thermal_local.runtime_observation() == observed
    observer.assert_called_once_with(session)
    factory.return_value.__exit__.assert_called_once()


def test_sqlite_cannot_claim_real_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    from packages.config import Settings

    monkeypatch.setattr(
        thermal_local,
        "get_settings",
        lambda: Settings(_env_file=None, APP_ENV="local", DATABASE_URL="sqlite://"),
    )
    thermal_local.observation_sessions.cache_clear()
    try:
        assert thermal_local.runtime_observation() == unknown_observation()
    finally:
        thermal_local.observation_sessions.cache_clear()
