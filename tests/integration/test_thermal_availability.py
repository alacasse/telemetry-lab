"""Finite retained commits exercise real process contention, not WALSync itself."""

from __future__ import annotations

import io
import json
import multiprocessing as mp
import os
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from packages.config import Settings
from packages.db.base import Base
from packages.db.migration_handler import run_migrations
from packages.thermal.authority import PgAuthority, create_authority_session_factory
from packages.thermal.models import ThermalAuthority, ThermalSimulation


@pytest.fixture
def availability_database() -> Iterator[str]:
    url = os.getenv("TELEMETRY_LAB_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    assert run_migrations(database_url=url)["schema_matches_expected_head"]
    engine = create_engine(url)
    with sessionmaker(engine).begin() as session:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name.startswith("thermal_") and table.name != "thermal_authority":
                session.execute(table.delete())
        row = session.get(ThermalAuthority, "shared-room")
        assert row is not None
        row.owner_id = row.expires_at = row.clock_passed_at = None
    yield url
    engine.dispose()


def availability_process(url: str, result: Any, mode: str) -> None:
    from packages.thermal.process import ThermalTransports, run_process

    os.environ["THERMAL_DIAGNOSTICS"] = "1"
    settings = Settings(_env_file=None, APP_ENV="local", DATABASE_URL=url)
    business = create_authority_session_factory(settings)
    renewal = create_authority_session_factory(settings)
    observer_engine = create_engine(url)
    observer = sessionmaker(observer_engine)
    authority = PgAuthority(renewal)
    retained = threading.Event()
    released = threading.Event()
    finished = threading.Event()
    attempts: list[dict[str, Any]] = []
    commits: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    effects: list[str] = []
    failures: list[str] = []
    initial: dict[str, Any] = {}
    armed = False
    injected = False
    lock = threading.Lock()
    original_guard = authority.guard
    original_transaction = getattr(authority, "transaction", None)
    original_renew = authority.renew
    local = threading.local()

    def renew() -> None:
        local.kind = "renewal"
        entering("renewal")
        original_renew()

    authority.renew = renew  # type: ignore[method-assign]

    def entering(kind: str) -> None:
        attempts.append(
            {
                "kind": kind,
                "time": time.monotonic(),
                "thread_id": threading.get_ident(),
                "during_retention": retained.is_set() and not released.is_set(),
            }
        )

    # A contender may enter before the holder reaches commit. Observe the
    # bounded gate polls as well, proving it remains queued during retention.
    if original_transaction is not None:
        original_wait = authority._admission_changed.wait
        recorded_waiters: set[int] = set()

        def admission_wait(timeout: float | None = None) -> bool:
            tid = threading.get_ident()
            if retained.is_set() and not released.is_set() and tid not in recorded_waiters:
                recorded_waiters.add(tid)
                entering(getattr(local, "kind", "business"))
            return original_wait(timeout)

        authority._admission_changed.wait = admission_wait  # type: ignore[method-assign]

    # Record entry before admission, so fixed gate waiters remain observable.
    if original_transaction is not None:

        @contextmanager
        def transaction(sessions: Any) -> Iterator[Any]:
            entering("renewal" if sessions is renewal else "business")
            with original_transaction(sessions) as session:
                yield session

        authority.transaction = transaction  # type: ignore[method-assign]
    else:

        def guard(session: Any) -> None:
            entering("renewal" if session.get_bind() is renewal.kw["bind"] else "business")
            original_guard(session)

        authority.guard = guard  # type: ignore[method-assign]

    def commit(kind: str) -> None:
        nonlocal injected
        with lock:
            delay = armed and not injected and kind == mode.split("_")[0]
            if delay:
                injected = True
        if delay:
            retained.set()
            started = time.monotonic()
            # Independent finite release: fatal diagnostics never release it.
            time.sleep(1.3)
            commits.append({"kind": kind, "retained_seconds": time.monotonic() - started})
            released.set()

    def business_commit(connection: Any) -> None:
        commit("business")

    def renewal_commit(connection: Any) -> None:
        commit("renewal")

    event.listen(business.kw["bind"], "commit", business_commit)
    event.listen(renewal.kw["bind"], "commit", renewal_commit)

    class Queue:
        def publish(self, body: str) -> str:
            effects.append("command_publish")
            return "test-command"

        def receive(self) -> list[dict]:
            effects.append("receive")
            return []

        def acknowledge(self, receipt: str) -> None:
            effects.append("acknowledge")

    def publish(body: str, sid: str) -> dict:
        effects.append("reading_publish")
        return {"test": True}

    def sample() -> dict[str, Any]:
        with observer() as session:
            row = session.get(ThermalAuthority, "shared-room")
            simulation = session.scalar(select(ThermalSimulation))
            assert row is not None and row.renewed_at is not None and simulation is not None
            return {
                "generation": row.generation,
                "owner": row.owner_id,
                "renewed": row.renewed_at.isoformat(),
                "heartbeat": row.clock_passed_at.isoformat() if row.clock_passed_at else None,
                "step": simulation.step,
                "time": time.monotonic(),
            }

    def stop_after_progress() -> None:
        try:
            if mode == "business_stop":
                assert retained.wait(5), "Commit retention never started"
                # Let the other real loops enter and queue during the held commit.
                time.sleep(0.3)
                initial["stop_requested"] = time.monotonic()
                os.kill(os.getpid(), signal.SIGTERM)
                return
            assert released.wait(5), "Commit retention never completed"
            end = time.monotonic() + 4.2
            while time.monotonic() < end and not finished.is_set():
                observations.append(sample())
                time.sleep(0.15)
            if not finished.is_set():
                initial["stop_requested"] = time.monotonic()
                os.kill(os.getpid(), signal.SIGTERM)
        except Exception as exc:
            failures.append(repr(exc))
            os.kill(os.getpid(), signal.SIGTERM)

    @contextmanager
    def transports() -> Iterator[ThermalTransports]:
        nonlocal armed
        initial.update(sample())
        armed = True
        stopper = threading.Thread(target=stop_after_progress, daemon=True)
        stopper.start()
        try:
            yield ThermalTransports(Queue(), publish)
        finally:
            finished.set()
            stopper.join(1)

    output, errors = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(errors):
            code = run_process(business, authority, transports=transports)
        result.put(
            {
                "mode": mode,
                "code": code,
                "initial": initial,
                "final": sample(),
                "attempts": attempts,
                "commits": commits,
                "observations": observations,
                "effects": effects,
                "failures": failures,
                "stdout": output.getvalue(),
                "stderr": errors.getvalue(),
                "injection": "finite pre-DBAPI commit retention; not reproduced WALSync",
            }
        )
    finally:
        finished.set()
        business.kw["bind"].dispose()
        renewal.kw["bind"].dispose()
        observer_engine.dispose()


@pytest.mark.parametrize("mode", ["business", "renewal"])
def test_finite_commit_contention_preserves_availability(
    availability_database: str, mode: str
) -> None:
    context = mp.get_context("spawn")
    result = context.Queue()
    child = context.Process(target=availability_process, args=(availability_database, result, mode))
    child.start()
    try:
        evidence = result.get(timeout=15)
        child.join(5)
        destination = os.getenv("TELEMETRY_LAB_AVAILABILITY_EVIDENCE")
        if destination:
            Path(destination).with_name(Path(destination).stem + "-" + mode + ".json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
        assert child.exitcode == 0
        assert evidence["code"] == 0, evidence["stdout"] + evidence["stderr"]
        assert not evidence["failures"]
        assert len(evidence["commits"]) == 1
        assert 1.25 <= evidence["commits"][0]["retained_seconds"] < 2
        contenders = [item for item in evidence["attempts"] if item["during_retention"]]
        assert any(item["kind"] == "business" for item in contenders)
        assert len({item["thread_id"] for item in contenders}) >= 2
        assert evidence["observations"]
        last = evidence["observations"][-1]
        assert last["generation"] == evidence["initial"]["generation"]
        assert last["owner"] == evidence["initial"]["owner"]
        assert last["renewed"] > evidence["initial"]["renewed"]
        assert last["heartbeat"] is not None
        assert last["step"] > evidence["initial"]["step"] + 5
        assert "reading_publish" in evidence["effects"] and "receive" in evidence["effects"]
        assert evidence["final"]["owner"] is None
        assert evidence["final"]["time"] - evidence["initial"]["stop_requested"] < 4
        assert "thermal_fatal" not in evidence["stderr"]
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)
        result.close()


def test_sigterm_cancels_queued_loops_and_finishes_retained_commit(
    availability_database: str,
) -> None:
    context = mp.get_context("spawn")
    result = context.Queue()
    child = context.Process(
        target=availability_process, args=(availability_database, result, "business_stop")
    )
    child.start()
    try:
        evidence = result.get(timeout=12)
        child.join(5)
        destination = os.getenv("TELEMETRY_LAB_AVAILABILITY_EVIDENCE")
        if destination:
            Path(destination).with_name(Path(destination).stem + "-queued-stop.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
        assert child.exitcode == 0 and evidence["code"] == 0
        assert evidence["failures"] == []
        assert len(evidence["commits"]) == 1
        contenders = [item for item in evidence["attempts"] if item["during_retention"]]
        assert len({item["thread_id"] for item in contenders}) >= 2
        assert evidence["final"]["owner"] is None
        assert evidence["final"]["generation"] == evidence["initial"]["generation"]
        assert evidence["final"]["time"] - evidence["initial"]["stop_requested"] < 4
        assert "thermal_fatal" not in evidence["stderr"]
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)
        result.close()
