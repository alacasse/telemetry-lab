import json
from pathlib import Path

import pytest

from demo import thermal_local


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


def test_missing_and_invalid_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("THERMAL_RUNTIME_STATE", raising=False)
    unknown = thermal_local.runtime_observation()
    assert unknown == {
        "status": "unknown",
        "process_id": None,
        "release_revision": None,
        "source": "local-launcher-process-observation",
    }
    path = tmp_path / "runtime.json"
    monkeypatch.setenv("THERMAL_RUNTIME_STATE", str(path))
    assert thermal_local.runtime_observation() == unknown
    for content in [
        "bad",
        "[]",
        '{"thermal_pid": -1}',
        '{"thermal_pid": 1, "thermal_status": "bad"}',
    ]:
        path.write_text(content)
        assert thermal_local.runtime_observation() == unknown


@pytest.mark.parametrize(
    "error,status",
    [(None, "available"), (ProcessLookupError, "unavailable"), (PermissionError, "unknown")],
)
def test_process_observation_preserves_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: type[Exception] | None,
    status: str,
) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {"thermal_pid": 123, "thermal_status": "available", "release_revision": "revision"}
        )
    )
    monkeypatch.setenv("THERMAL_RUNTIME_STATE", str(path))

    def probe(pid: int, signal: int) -> None:
        assert (pid, signal) == (123, 0)
        if error:
            raise error()

    monkeypatch.setattr(thermal_local.os, "kill", probe)
    assert thermal_local.runtime_observation() == {
        "status": status,
        "process_id": 123,
        "release_revision": "revision",
        "source": "local-launcher-process-observation",
    }
