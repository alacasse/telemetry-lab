"""Launcher configuration and process observations for the local composition."""

import json
import math
import os
from pathlib import Path


def runtime_observation() -> dict:
    result: dict[str, object] = {
        "status": "unknown",
        "process_id": None,
        "release_revision": None,
        "source": "local-launcher-process-observation",
    }
    path = os.environ.get("THERMAL_RUNTIME_STATE")
    if not path:
        return result
    try:
        state = json.loads(Path(path).read_text())
        pid = state.get("thermal_pid")
        status = state.get("thermal_status", "unknown")
        if not isinstance(pid, int) or pid <= 0 or status not in {"available", "unavailable"}:
            return result
        if status == "available":
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                status = "unavailable"
            except PermissionError:
                status = "unknown"
        result.update(status=status, process_id=pid, release_revision=state.get("release_revision"))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result


def multiplier() -> float:
    value = float(os.environ.get("THERMAL_TIME_MULTIPLIER", "1"))
    if not math.isfinite(value) or not 0.5 <= value <= 2:
        raise ValueError("THERMAL_TIME_MULTIPLIER must be between 0.5 and 2")
    return value
