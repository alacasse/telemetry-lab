from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from packages.thermal import process as thermal_runtime
from packages.thermal.authority import AuthorityBusy, AuthorityLost


def test_startup_rolls_back_acquisition_and_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE proof (value TEXT)"))
    sessions = sessionmaker(engine)
    authority = Mock()
    authority.acquire.side_effect = lambda session: session.execute(
        text("INSERT INTO proof VALUES ('acquired')")
    )
    original_execute = Session.execute

    def execute(
        self: Session, statement: Any, *args: Any, **kwargs: Any
    ) -> Any:
        if "pg_advisory" in str(statement):
            return None
        return original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", execute)
    monkeypatch.setattr(
        thermal_runtime, "recover_simulations",
        lambda session: session.execute(text("INSERT INTO proof VALUES ('recovered')")),
    )
    monkeypatch.setattr(thermal_runtime, "create_thermostat", Mock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        thermal_runtime.initialize_runtime(sessions, authority, initialize=True)
    with sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM proof")) == 0
    authority.fail.assert_called_once()


def test_busy_startup_has_no_recovery_or_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = sessionmaker(create_engine("sqlite://"))
    authority = Mock()
    authority.acquire.side_effect = AuthorityBusy()
    recover, create = Mock(), Mock()
    monkeypatch.setattr(thermal_runtime, "recover_simulations", recover)
    monkeypatch.setattr(thermal_runtime, "create_thermostat", create)
    with pytest.raises(AuthorityBusy):
        thermal_runtime.initialize_runtime(sessions, authority, initialize=True)
    recover.assert_not_called()
    create.assert_not_called()


@pytest.mark.parametrize("observation,expected", [
    ({"status": "unknown", "authority_free": True}, None),
    ({"status": "unavailable", "authority_free": False}, False),
    ({"status": "unavailable", "authority_free": True}, True),
    ({"status": "available", "authority_free": False}, False),
])
def test_launcher_uses_lease_instead_of_clock_availability(
    monkeypatch: pytest.MonkeyPatch, observation: dict, expected: bool | None,
) -> None:
    path = Path(__file__).resolve().parents[2] / "scripts/demo-local.py"
    spec = importlib.util.spec_from_file_location("demo_launcher", path)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setattr(
        launcher.subprocess, "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(observation))),
    )
    assert launcher.authority_free({}, "/tmp") is expected


@pytest.mark.parametrize("failure", ["none", "fatal", "stranded", "lost"])
def test_shutdown_deadline_and_cleanup_admission(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """One exhausted join must leave zero time, and unsafe stops cannot recover."""
    sessions = sessionmaker(create_engine("sqlite://"))
    authority = Mock()
    if failure == "fatal":
        authority.renew.side_effect = RuntimeError("database failed")
    if failure == "lost":
        authority.check_active.side_effect = AuthorityLost()
    monkeypatch.setattr(thermal_runtime, "initialize_runtime", Mock())
    monkeypatch.setattr(thermal_runtime, "RoomClock", Mock())
    recovery = Mock()
    monkeypatch.setattr(thermal_runtime, "recover_simulations", recovery)
    handlers: list[Any] = []
    monkeypatch.setattr(
        thermal_runtime.signal, "signal", lambda _, callback: handlers.append(callback)
    )
    monkeypatch.setattr(thermal_runtime.threading, "Timer", Mock())
    now = [100.0]
    monkeypatch.setattr(thermal_runtime.time, "monotonic", lambda: now[0])
    threads: list[Mock] = []

    def thread_factory(*, target: Any, args: tuple, daemon: bool) -> Mock:
        thread = Mock()
        thread.is_alive.return_value = failure == "stranded"
        if not threads:
            if failure == "fatal":
                thread.start.side_effect = lambda: target(*args)
            else:
                thread.start.side_effect = lambda: handlers[0](0, None)

        def join(*, timeout: float) -> None:
            if failure == "stranded":
                now[0] += timeout  # A stranded thread exhausts the shared budget.

        thread.join.side_effect = join
        threads.append(thread)
        return thread

    monkeypatch.setattr(thermal_runtime.threading, "Thread", thread_factory)
    from contextlib import nullcontext

    assert thermal_runtime.run_process(
        sessions, authority,
        transports=lambda: nullcontext(thermal_runtime.ThermalTransports(Mock(), Mock())),
    ) == (0 if failure == "none" else 1)
    assert threads[0].join.call_args.kwargs["timeout"] == 4
    if failure == "stranded":
        assert all(thread.join.call_args.kwargs["timeout"] == 0 for thread in threads[1:])
    if failure == "none":
        recovery.assert_called_once()
        authority.release.assert_called_once()
    else:
        recovery.assert_not_called()
        authority.release.assert_not_called()
        authority.fail.assert_called()


@pytest.mark.parametrize("free", [None, False, True])
def test_supervisor_spends_budget_only_when_starting(
    monkeypatch: pytest.MonkeyPatch, free: bool | None,
) -> None:
    path = Path(__file__).resolve().parents[2] / "scripts/demo-local.py"
    spec = importlib.util.spec_from_file_location("demo_supervisor", path)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    exited = Mock()
    exited.poll.return_value = 1
    state = {"thermal_restarts": 0}
    probe, spawn = Mock(return_value=free), Mock()
    monkeypatch.setattr(launcher, "authority_free", probe)
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    replacement = launcher.restart_thermal(exited, state, {}, "/tmp", None)
    assert state["thermal_restarts"] == (1 if free is True else 0)
    if free is True:
        spawn.assert_called_once()
        assert replacement is spawn.return_value
    else:
        spawn.assert_not_called()
        assert replacement is exited


def test_supervisor_busy_refusal_ends_automatic_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(__file__).resolve().parents[2] / "scripts/demo-local.py"
    spec = importlib.util.spec_from_file_location("busy_supervisor", path)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    process = Mock()
    process.poll.return_value = 75
    state = {"thermal_restarts": 1}
    probe, spawn = Mock(return_value=True), Mock()
    monkeypatch.setattr(launcher, "authority_free", probe)
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    for _ in range(3):
        assert launcher.restart_thermal(process, state, {}, "/tmp", None) is process
    # The refusal persists even if subsequent diagnostics alter the exit code.
    process.poll.return_value = 1
    assert launcher.restart_thermal(process, state, {}, "/tmp", None) is process
    assert state["thermal_restarts"] == 1
    probe.assert_not_called()
    spawn.assert_not_called()


@pytest.mark.parametrize("crash_windows", [False, True])
def test_historical_integration_groups_cover_crash_windows_separately(crash_windows: bool) -> None:
    path = Path(__file__).resolve().parents[2] / "scripts/demo-local.py"
    spec = importlib.util.spec_from_file_location("test_groups_launcher", path)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    selection = launcher.integration_test_selection(thermostat=False, crash_windows=crash_windows)
    assert str(launcher.ROOT / "tests/integration/test_thermal_demo.py") in selection
    assert selection[-2:] == ["-k", "crash_windows" if crash_windows else "not crash_windows"]
    assert (str(launcher.ROOT / "tests/integration/test_local_demo.py") in selection) is (
        not crash_windows
    )


def test_busy_process_never_constructs_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    authority, transports = Mock(), Mock()
    authority.acquire.side_effect = AuthorityBusy()
    sessions = sessionmaker(create_engine("sqlite://"))
    assert thermal_runtime.run_process(sessions, authority, transports=transports) == 75
    transports.assert_not_called()


@pytest.mark.parametrize("hang", ["transport", "cleanup"])
def test_signal_shutdown_bounds_hung_cleanup(hang: str) -> None:
    """A real subprocess must exit even when adapter/database cleanup never returns."""
    import sys
    import time

    code = r"""
import os, signal, threading, time
from contextlib import contextmanager
from unittest.mock import MagicMock, Mock
from packages.thermal import process
process.initialize_runtime = Mock()
process.RoomClock = Mock()
process.reading_publication = Mock()
process.command_publication = Mock()
process.receive_commands = Mock()
process.recover_simulations = Mock()
authority = Mock()
@contextmanager
def transports():
    print('READY', flush=True)
    yield process.ThermalTransports(Mock(), Mock())
    if HANG == 'transport':
        while True: time.sleep(1)
class Sessions:
    def __call__(self):
        if HANG == 'cleanup':
            while True: time.sleep(1)
        return MagicMock()
raise SystemExit(process.run_process(Sessions(), authority, transports=transports))
"""
    child = subprocess.Popen(
        [sys.executable, "-c", "HANG = " + repr(hang) + "\n" + code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        started = time.monotonic()
        child.send_signal(__import__("signal").SIGTERM)
        time.sleep(0.1)
        child.send_signal(__import__("signal").SIGINT)
        child.communicate(timeout=5)
        assert child.returncode == 1
        assert time.monotonic() - started < 4.8
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
