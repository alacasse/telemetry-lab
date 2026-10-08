import json
from typing import Any

import pytest
from sqlalchemy import create_engine

from packages.thermal import diagnostics


def test_fatal_metadata_does_not_include_exception_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    try:
        raise RuntimeError("postgresql://secret:password@host/db SELECT secret")
    except RuntimeError as exc:
        payload = diagnostics.fatal_payload(
            exc, loop="clock_tick", action="clock_tick", generation=12
        )
    diagnostics.emit(payload)
    output = capsys.readouterr().err
    assert "password" not in output
    assert "SELECT" not in output
    assert "postgresql" not in output
    record = json.loads(output)
    assert record["generation"] == 12
    assert (
        record["frames"][-1]["function"] == "test_fatal_metadata_does_not_include_exception_content"
    )
    assert set(record["frames"][-1]) == {"file", "function", "line"}


def test_checkout_owner_and_release_are_observed_without_extra_checkout() -> None:
    engine = create_engine("sqlite://", poolclass=diagnostics.DiagnosticQueuePool)
    diagnostics.install_pool_diagnostics(engine)
    with diagnostics.action_context("clock_tick", "clock_tick"):
        with engine.connect():
            snapshot = diagnostics.pool_snapshot()
            assert len(snapshot) == 1
            assert snapshot[0]["action"] == "clock_tick"
            assert snapshot[0]["checkout_wait_seconds"] >= 0
            assert snapshot[0]["held_since_monotonic"] > 0
    assert diagnostics.pool_snapshot() == []
    engine.dispose()


def test_mock_generation_and_broken_output_do_not_break_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    payload = diagnostics.fatal_payload(
        ValueError("secret"), loop="supervisor", action="supervise", generation=Mock()
    )
    assert payload["generation"] is None

    def broken_print(*args: Any, **kwargs: Any) -> None:
        raise OSError("closed")

    monkeypatch.setattr("builtins.print", broken_print)
    diagnostics.emit(payload)


def test_exception_module_distinguishes_timeout_origins() -> None:
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    for exc, module in [
        (TimeoutError("secret"), "builtins"),
        (PoolTimeout("secret"), "sqlalchemy.exc"),
    ]:
        payload = diagnostics.fatal_payload(
            exc, loop="clock_tick", action="clock_tick", generation=2
        )
        assert payload["exception_type"] == "TimeoutError"
        assert payload["exception_module"] == module


def test_backend_pid_uses_only_driver_info() -> None:
    from types import SimpleNamespace

    assert diagnostics.backend_pid(SimpleNamespace(info=SimpleNamespace(backend_pid=123))) == 123
    assert diagnostics.backend_pid(object()) is None


def test_sqlstate_accepts_only_exact_safe_driver_code() -> None:
    from sqlalchemy.exc import DBAPIError

    class DriverError(Exception):
        def __init__(self, value: object) -> None:
            self.sqlstate = value

    for value, expected in [
        ("55P03", "55P03"),
        ("57014", "57014"),
        ("secret SQL", None),
        ("abcde", None),
        (12345, None),
    ]:
        exc = DBAPIError(None, None, DriverError(value))
        assert diagnostics.sqlstate(exc) == expected


def test_blocked_output_does_not_hold_pool_or_grow_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    entered, release = threading.Event(), threading.Event()

    def blocked_writer(payload: dict[str, Any]) -> None:
        entered.set()
        release.wait(3)

    monkeypatch.setattr(diagnostics, "write_timing", blocked_writer)
    engine = create_engine("sqlite://", poolclass=diagnostics.DiagnosticQueuePool)
    diagnostics.install_pool_diagnostics(engine)
    try:
        diagnostics.emit_timing({"event": "test_blocked_sink"})
        assert entered.wait(1)
        connection = engine.connect()
        # Force a slow-hold record without slowing the real checkout/checkin path.
        with diagnostics._lock:
            for owner in diagnostics._held.values():
                owner["held_since_monotonic"] -= 1
        started = time.monotonic()
        connection.close()
        with engine.connect():
            pass
        assert time.monotonic() - started < 0.2
        assert isinstance(engine.pool, diagnostics.DiagnosticQueuePool)
        assert engine.pool.checkedout() == 0
        for _ in range(1000):
            diagnostics.emit_timing({"event": "test_queue_bound"})
        assert diagnostics._timing_queue.qsize() == 128
        started = time.monotonic()
        diagnostics.emit_timing({"event": "dropped"})
        assert time.monotonic() - started < 0.2
        assert diagnostics._timing_queue.qsize() == 128
    finally:
        release.set()
        engine.dispose()


def test_unread_real_stderr_pipe_does_not_block_interpreter_exit() -> None:
    import subprocess
    import sys

    code = """
import time
from packages.thermal import diagnostics
diagnostics.start_timing_sink()
record = {"event": "thermal_pool_hold", "loop": "reading_publication",
          "action": "reading_publication", "process_id": 123, "generation": 2,
          "hold_seconds": 0.3, "held_since_monotonic": 1234.5,
          "pool": {"pool_id": 1234, "size": 5, "checked_out": 3, "overflow": 0},
          "backend_pid": 42, "thread_id": 123456789, "checkout_wait_seconds": 0.1}
for _ in range(128):
    diagnostics.emit_timing(record)
time.sleep(0.2)
"""
    process = subprocess.Popen([sys.executable, "-c", code], stderr=subprocess.PIPE)
    try:
        # Deliberately leave stderr unread until the process has exited.
        assert process.wait(timeout=4) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=2)
