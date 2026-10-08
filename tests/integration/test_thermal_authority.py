"""Real PostgreSQL fencing with independent process connections."""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from packages.db.base import Base
from packages.db.migration_handler import run_migrations
from packages.thermal.authority import AuthorityBusy, AuthorityLost, PgAuthority, observe_authority
from packages.thermal.models import ThermalAuthority, ThermalSimulation
from packages.thermal.runtime import RoomClock
from packages.thermal.service import create_simulation


def process_acquire(url: str, start: object, result: object) -> None:
    sessions = sessionmaker(create_engine(url))
    authority = PgAuthority(sessions)
    start.wait()  # type: ignore[attr-defined]
    try:
        with sessions() as session, session.begin():
            authority.acquire(session)
        result.put(("acquired", authority.owner_id, authority.generation))  # type: ignore[attr-defined]
    except AuthorityBusy:
        result.put(("busy", None, None))  # type: ignore[attr-defined]


def process_initialize(url: str, start: object, result: object) -> None:
    import packages.thermal.process as runtime

    sessions = sessionmaker(create_engine(url))
    authority = PgAuthority(sessions)
    recoveries = 0
    original = runtime.recover_simulations

    def recover(session: Session, now: datetime | None = None) -> list[ThermalSimulation]:
        nonlocal recoveries
        recoveries += 1
        return original(session, now)

    runtime.recover_simulations = recover
    start.wait()  # type: ignore[attr-defined]
    try:
        runtime.initialize_runtime(sessions, authority, initialize=False)
        result.put(("acquired", authority.owner_id, authority.generation, recoveries))  # type: ignore[attr-defined]
    except AuthorityBusy:
        result.put(("busy", None, None, recoveries))  # type: ignore[attr-defined]


def process_suspended_owner(url: str, pipe: object) -> None:
    sessions = sessionmaker(create_engine(url))
    authority = PgAuthority(sessions)
    with sessions() as session, session.begin():
        authority.acquire(session)
    pipe.send((authority.owner_id, authority.generation))  # type: ignore[attr-defined]
    pipe.recv()  # type: ignore[attr-defined]
    results = []
    for operation in (authority.renew, lambda: RoomClock(sessions, authority).tick()):
        try:
            operation()
            results.append("admitted")
        except AuthorityLost:
            results.append("lost")
    try:
        with sessions() as session, session.begin():
            authority.release(session)
        results.append("released")
    except AuthorityLost:
        results.append("lost")
    pipe.send(results)  # type: ignore[attr-defined]


def process_locked_transfer(url: str, result: object) -> None:
    sessions = sessionmaker(create_engine(url))
    authority = PgAuthority(sessions)
    result.put("waiting")  # type: ignore[attr-defined]
    with sessions() as session, session.begin():
        authority.acquire(session)
    result.put((authority.owner_id, authority.generation))  # type: ignore[attr-defined]


@pytest.fixture
def database() -> Iterator[tuple[str, sessionmaker[Session]]]:
    url = os.getenv("TELEMETRY_LAB_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires isolated PostgreSQL: scripts/test-postgres.sh")
    assert run_migrations(database_url=url)["schema_matches_expected_head"]
    engine = create_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session, session.begin():
        for table in reversed(Base.metadata.sorted_tables):
            if table.name.startswith("thermal_") and table.name != "thermal_authority":
                session.execute(table.delete())
        row = session.get(ThermalAuthority, "shared-room")
        assert row is not None
        row.owner_id = None
        row.expires_at = None
        row.clock_passed_at = None
    yield url, sessions
    engine.dispose()


def acquire(sessions: sessionmaker[Session]) -> PgAuthority:
    authority = PgAuthority(sessions, monotonic=lambda: 0)
    with sessions() as session, session.begin():
        authority.acquire(session)
    return authority


def expire(sessions: sessionmaker[Session]) -> None:
    with sessions() as session, session.begin():
        row = session.get(ThermalAuthority, "shared-room")
        assert row is not None
        now = session.scalar(select(func.clock_timestamp()))
        assert now is not None
        row.expires_at = now - timedelta(seconds=1)


def test_simultaneous_process_acquisition_has_one_winner_and_busy_no_effect(
    database: tuple,
) -> None:
    url, sessions = database
    sid = str(uuid4())
    with sessions() as session, session.begin():
        create_simulation(session, sid)
    context = mp.get_context("spawn")
    start, result = context.Event(), context.Queue()
    children = [
        context.Process(target=process_initialize, args=(url, start, result)) for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        start.set()
        outcomes = [result.get(timeout=10) for _ in children]
        assert sorted(item[0] for item in outcomes) == ["acquired", "busy"]
        winner = next(item for item in outcomes if item[0] == "acquired")
        with sessions() as session:
            row = session.get(ThermalAuthority, "shared-room")
            assert (row.owner_id, row.generation) == winner[1:3]
            simulation = session.get(ThermalSimulation, sid)
            assert simulation.status == "interrupted" and simulation.step == 0
        assert winner[3] == 1
        assert next(item for item in outcomes if item[0] == "busy")[3] == 0
    finally:
        for child in children:
            child.join(10)
            if child.is_alive():
                child.kill()
                child.join()
        assert all(child.exitcode == 0 for child in children)


def test_crashed_process_replaced_after_real_lease_expiry(database: tuple) -> None:
    url, sessions = database
    context = mp.get_context("spawn")
    result, start = context.Queue(), context.Event()
    child = context.Process(target=process_acquire, args=(url, start, result))
    child.start()
    start.set()
    original = result.get(timeout=10)
    child.join(10)
    assert child.exitcode == 0 and original[0] == "acquired"
    replacement = PgAuthority(sessions)
    with pytest.raises(AuthorityBusy), sessions() as session, session.begin():
        replacement.acquire(session)
    deadline = time.monotonic() + 12
    while True:
        try:
            with sessions() as session, session.begin():
                replacement.acquire(session)
            break
        except AuthorityBusy:
            assert time.monotonic() < deadline
            time.sleep(0.1)
    assert replacement.generation == original[2] + 1
    assert replacement.owner_id != original[1]


def test_admitted_old_transaction_commits_before_waiting_successor(database: tuple) -> None:
    url, sessions = database
    old = acquire(sessions)
    sid = str(uuid4())
    with sessions() as session, session.begin():
        create_simulation(session, sid)
    context = mp.get_context("spawn")
    result = context.Queue()
    child = context.Process(target=process_locked_transfer, args=(url, result))
    try:
        with sessions() as session, session.begin():
            old.guard(session)
            row = session.get(ThermalAuthority, "shared-room")
            row.expires_at = session.scalar(select(func.clock_timestamp())) + timedelta(seconds=0.1)
            simulation = session.get(ThermalSimulation, sid)
            assert simulation is not None
            simulation.temperature_c = 20
            session.flush()
            child.start()
            assert result.get(timeout=10) == "waiting"
            time.sleep(0.3)
            assert child.is_alive()
        successor = result.get(timeout=10)
        child.join(10)
        assert old.generation is not None
        assert successor[1] == old.generation + 1
        with sessions() as session:
            row = session.get(ThermalAuthority, "shared-room")
            assert row.owner_id == successor[0]
            assert row.generation == successor[1]
            assert session.get(ThermalSimulation, sid).temperature_c == 20
        with pytest.raises(AuthorityLost), sessions() as session, session.begin():
            old.guard(session)
            session.get(ThermalSimulation, sid).temperature_c = 30
        with sessions() as session:
            assert session.get(ThermalSimulation, sid).temperature_c == 20
    finally:
        if child.is_alive():
            child.kill()
            child.join()


def test_old_generation_rejects_work_and_late_cleanup_after_transfer(database: tuple) -> None:
    _, sessions = database
    old = acquire(sessions)
    expire(sessions)
    successor = acquire(sessions)
    for operation in (old.guard, old.release):
        with pytest.raises(AuthorityLost), sessions() as session, session.begin():
            operation(session)
    with sessions() as session:
        row = session.get(ThermalAuthority, "shared-room")
        assert (row.owner_id, row.generation) == (successor.owner_id, successor.generation)


def test_suspended_owner_cannot_renew_or_restart_before_supervisor(database: tuple) -> None:
    _, sessions = database
    now = [100.0]
    authority = PgAuthority(sessions, monotonic=lambda: now[0])
    with sessions() as session, session.begin():
        authority.acquire(session)
    now[0] += 5
    with pytest.raises(AuthorityLost):
        authority.renew()
    now[0] = 100
    with pytest.raises(AuthorityLost), sessions() as session, session.begin():
        authority.guard(session)
    assert not authority.active


def test_connection_loss_permanently_disables_authority(database: tuple) -> None:
    _, sessions = database
    authority = acquire(sessions)
    with sessions() as session:
        pid = session.scalar(text("SELECT pg_backend_pid()"))
    # Terminate the pooled connection, then renewal must fail rather than silently retry.
    killer = create_engine(sessions.kw["bind"].url, poolclass=NullPool)
    try:
        with killer.connect() as connection:
            connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    finally:
        killer.dispose()
    with pytest.raises(DBAPIError):
        authority.renew()
    assert not authority.active
    with pytest.raises(AuthorityLost), sessions() as session, session.begin():
        authority.release(session)


def test_atomic_startup_rollback_restores_authority_and_simulation(
    database: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    import packages.thermal.process as runtime

    _, sessions = database
    sid = str(uuid4())
    with sessions() as session, session.begin():
        create_simulation(session, sid)
    with sessions() as session:
        original_generation = session.get(ThermalAuthority, "shared-room").generation
    failed = PgAuthority(sessions)

    def fail_initialization(*args: object, **kwargs: object) -> None:
        raise RuntimeError("startup failed")

    monkeypatch.setattr(runtime, "create_thermostat", fail_initialization)
    with pytest.raises(RuntimeError, match="startup failed"):
        runtime.initialize_runtime(sessions, failed, initialize=True)
    assert not failed.active
    with sessions() as session:
        row = session.get(ThermalAuthority, "shared-room")
        assert row.owner_id is None and row.generation == original_generation
        assert session.get(ThermalSimulation, sid).status == "active"


def test_idle_and_interrupted_clock_publish_passive_availability(database: tuple) -> None:
    _, sessions = database
    authority = acquire(sessions)
    clock = RoomClock(sessions, authority)
    with sessions() as session:
        assert observe_authority(session)["status"] == "unavailable"
    clock.tick()
    with sessions() as session:
        observed = observe_authority(session)
        assert observed["status"] == "available"
        assert observed["owner_id"] == authority.owner_id
    expire(sessions)
    with sessions() as session:
        assert observe_authority(session)["status"] == "unavailable"


def test_real_suspension_old_process_cannot_restart_or_clean_up(database: tuple) -> None:
    url, sessions = database
    context = mp.get_context("spawn")
    parent, remote = context.Pipe()
    child = context.Process(target=process_suspended_owner, args=(url, remote))
    child.start()
    try:
        original = parent.recv()
        assert child.pid is not None
        os.kill(child.pid, signal.SIGSTOP)
        time.sleep(10.2)
        successor = acquire(sessions)
        assert successor.generation == original[1] + 1
        parent.send("continue")
        os.kill(child.pid, signal.SIGCONT)
        assert parent.poll(10)
        assert parent.recv() == ["lost", "lost", "lost"]
        child.join(10)
        assert child.exitcode == 0
        with sessions() as session:
            row = session.get(ThermalAuthority, "shared-room")
            assert row.owner_id == successor.owner_id
            assert row.generation == successor.generation
    finally:
        if child.is_alive():
            child.kill()
            child.join()


def test_cached_authority_row_cannot_admit_old_owner_after_transfer(database: tuple) -> None:
    _, sessions = database
    old = acquire(sessions)
    with sessions() as cached_session:
        cached = cached_session.get(ThermalAuthority, "shared-room")
        assert cached is not None and cached.owner_id == old.owner_id
        cached_session.commit()  # Factory intentionally retains the identity map.
        with sessions.begin() as session:
            old.release(session)
        successor = acquire(sessions)
        with pytest.raises(AuthorityLost), cached_session.begin():
            old.guard(cached_session)
        with sessions() as session:
            current = session.get(ThermalAuthority, "shared-room")
            assert current is not None and current.owner_id == successor.owner_id
