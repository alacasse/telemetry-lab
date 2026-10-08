"""External pool/row contention still fails closed after local admission changes."""
from __future__ import annotations

import io
import json
import multiprocessing as mp
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from packages.config import Settings
from packages.db.base import Base
from packages.db.migration_handler import run_migrations
from packages.thermal.authority import AuthorityLost, PgAuthority, create_authority_session_factory
from packages.thermal.models import ThermalAuthority


@pytest.fixture
def database() -> Iterator[str]:
    url = os.getenv("TELEMETRY_LAB_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires isolated PostgreSQL: scripts/test-postgres.sh")
    assert run_migrations(database_url=url)["schema_matches_expected_head"]
    engine = create_engine(url)
    sessions = sessionmaker(engine)
    with sessions.begin() as session:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name.startswith("thermal_") and table.name != "thermal_authority":
                session.execute(table.delete())
        row = session.get(ThermalAuthority, "shared-room")
        assert row is not None
        row.owner_id = None
        row.expires_at = None
        row.clock_passed_at = None
    try:
        yield url
    finally:
        engine.dispose()


def contention_process(url: str, result: Any, mode: str = "pool") -> None:
    # The watchdog belongs in a child process: a regression must not exit pytest.
    import packages.thermal.process as runtime
    from packages.thermal import diagnostics

    os.environ["THERMAL_DIAGNOSTICS"] = "1"
    settings = Settings(_env_file=None, APP_ENV="local", DATABASE_URL=url)
    business = create_authority_session_factory(settings)
    renewal = create_authority_session_factory(settings)
    authority = PgAuthority(renewal)
    acquired, release = threading.Event(), threading.Event()
    external_engine = create_engine(url)
    external = sessionmaker(external_engine)
    effects = []
    holder_errors = []
    snapshots = []
    original_emit = diagnostics.emit
    renewal_times: list[datetime] = []

    def emit(payload: dict[str, Any]) -> None:
        original_emit(payload)
        if (payload.get("event") == "thermal_fatal"
                and (mode == "external_lock"
                     or payload.get("exception_type") == "TimeoutError")):
            release.set()

    diagnostics.emit = emit

    def snapshot() -> dict[str, list[dict[str, Any]]]:
        with renewal() as session:
            return {
                table.name: [dict(row) for row in session.execute(select(table)).mappings()]
                for table in Base.metadata.sorted_tables
                if table.name.startswith("thermal_") and table.name != "thermal_authority"
            }

    def hold_connection() -> None:
        try:
            factory = business if mode == "pool" else external
            with factory() as session, session.begin():
                if mode == "pool":
                    # Deliberately bypass engine admission to occupy its pool.
                    session.execute(text("SELECT 1"))
                else:
                    # An independent database client cannot participate in the
                    # process-local gate. The SQL lock limit must still apply.
                    session.execute(select(ThermalAuthority).with_for_update())
                acquired.set()
                if not release.wait(3):
                    raise AssertionError("Expected a real pool or SQL lock timeout")
        except Exception as exc:
            holder_errors.append(type(exc).__name__)
            acquired.set()

    class Queue:
        def publish(self, body: str) -> str:
            effects.append("command_publish")
            return "unexpected"

        def receive(self) -> list[dict]:
            effects.append("receive")
            return []

        def acknowledge(self, receipt: str) -> None:
            effects.append("acknowledge")

    def publish_reading(body: str, sid: str) -> dict:
        effects.append("reading_publish")
        return {}

    @contextmanager
    def transports() -> Iterator[runtime.ThermalTransports]:
        snapshots.append(snapshot())
        with renewal() as session:
            row = session.get(ThermalAuthority, "shared-room")
            assert row is not None and row.renewed_at is not None
            renewal_times.append(row.renewed_at)
        holder = threading.Thread(target=hold_connection, daemon=True)
        holder.start()
        assert acquired.wait(2) and not holder_errors
        try:
            yield runtime.ThermalTransports(Queue(), publish_reading)
        finally:
            release.set()
            holder.join(2)
            assert not holder.is_alive()

    output, errors = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(errors):
            code = runtime.run_process(business, authority, transports=transports)
        snapshots.append(snapshot())
        rejected = []
        for operation in (authority.guard, authority.release):
            try:
                with business() as session, session.begin():
                    operation(session)
            except AuthorityLost:
                rejected.append(True)
        with renewal() as session:
            row = session.get(ThermalAuthority, "shared-room")
            assert row is not None and row.renewed_at is not None
            retained_owner = row.owner_id == authority.owner_id
            renewed = row.renewed_at > renewal_times[0]
        result.put({
            "injection": mode, "code": code, "active": authority.active, "effects": effects,
            "unchanged": snapshots[0] == snapshots[1], "rejected": rejected,
            "retained_owner": retained_owner, "renewed": renewed, "holder_errors": holder_errors,
            "stdout": output.getvalue(), "stderr": errors.getvalue(),
        })
    finally:
        release.set()
        business.kw["bind"].dispose()
        renewal.kw["bind"].dispose()
        external_engine.dispose()


@pytest.mark.parametrize("mode", ["pool", "external_lock"])
def test_injected_contention_fails_closed(database: str, mode: str) -> None:
    context = mp.get_context("spawn")
    result = context.Queue()
    child = context.Process(target=contention_process, args=(database, result, mode))
    child.start()
    try:
        evidence = result.get(timeout=15)
        child.join(5)
        assert child.exitcode == 0
        assert evidence["code"] == 1 and not evidence["active"]
        assert evidence["rejected"] == [True, True]
        assert evidence["unchanged"] and evidence["retained_owner"]
        assert evidence["effects"] == [] and evidence["holder_errors"] == []
        if mode == "pool":
            assert evidence["renewed"]
        fatals = [json.loads(line) for line in evidence["stderr"].splitlines()
                  if json.loads(line).get("event") == "thermal_fatal"]
        if mode == "external_lock":
            lock_failure = next(item for item in fatals if item["sqlstate"] == "55P03")
            assert lock_failure["exception_module"] == "sqlalchemy.exc"
            assert any(frame["function"] == "_lock" for frame in lock_failure["frames"])
        else:
            timeout = next(item for item in fatals if item["exception_type"] == "TimeoutError")
            assert timeout["exception_module"] == "sqlalchemy.exc"
            assert timeout["loop"] in {
                "clock_tick", "reading_publication", "command_publication", "receive_commands"
            }
            assert any(frame["function"] == "_do_get" for frame in timeout["frames"])
            assert "thermal fatal: TimeoutError" in evidence["stdout"]
        for output in (evidence["stdout"], evidence["stderr"]):
            assert database not in output
            assert "local-test" not in output
            assert "postgresql" not in output
        artifact = os.getenv("TELEMETRY_LAB_CONTENTION_EVIDENCE")
        if artifact:
            from pathlib import Path
            destination = Path(artifact)
            if mode == "external_lock":
                destination = destination.with_name(
                    destination.stem + "-external-lock" + destination.suffix
                )
            destination.write_text(json.dumps(evidence, indent=2) + "\n")
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)
        result.close()
