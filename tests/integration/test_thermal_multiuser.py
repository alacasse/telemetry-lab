"""Shared-room HTTP writes and passive authority observation on real PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from packages.db.base import Base
from packages.db.migration_handler import run_migrations
from packages.thermal.authority import PgAuthority, observe_authority
from packages.thermal.http import create_router
from packages.thermal.models import ThermalAuthority, ThermalSimulation
from packages.thermal.runtime import RoomClock


def test_two_clients_conflict_and_observe_without_authority_lock() -> None:
    url = os.getenv("TELEMETRY_LAB_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires isolated PostgreSQL: scripts/test-postgres.sh")
    assert run_migrations(database_url=url)["schema_matches_expected_head"]
    engine = create_engine(url, connect_args={"options": "-c statement_timeout=2000"})
    sessions = sessionmaker(engine)
    # This explicitly opt-in database is disposable, with no running engine.
    with sessions.begin() as session:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name.startswith("thermal_") and table.name != "thermal_authority":
                session.execute(table.delete())
        row = session.get(ThermalAuthority, "shared-room")
        assert row is not None
        row.owner_id = None
        row.expires_at = None

    def observe() -> dict:
        with sessions() as session:
            return observe_authority(session)

    app = FastAPI()
    app.include_router(create_router(sessions, time_multiplier=1, observe_runtime=observe))
    try:
        with TestClient(app) as alice, TestClient(app) as bob:
            created = alice.post(
                "/thermal-simulations",
                json={"simulation_id": str(uuid4()), "policy": "thermostat"},
            )
            assert created.status_code == 200, created.text
            sid = created.json()["simulation_id"]
            assert bob.get("/thermal-simulations/current").json()["simulation_id"] == sid
            authority = PgAuthority(sessions)
            with sessions.begin() as session:
                authority.acquire(session)
            RoomClock(sessions, authority).tick()
            with sessions.begin() as held:
                authority.guard(held)

                # Both setting routes hold simulation locks while invoking observe().
                # Their observation must not wait on this authority lock.
                def change(client: TestClient, target: float) -> Response:
                    return client.post(
                        f"/thermal-simulations/{sid}/settings",
                        json={
                            "operation_id": str(uuid4()),
                            "expected_revision": 0,
                            "mode": "heating",
                            "target_c": target,
                        },
                    )

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(change, alice, 24), pool.submit(change, bob, 25)]
                    responses = [future.result(timeout=5) for future in futures]
                assert sorted(response.status_code for response in responses) == [200, 409]
                accepted = next(
                    response.json() for response in responses if response.status_code == 200
                )
                assert accepted["runtime"]["status"] == "available"
                assert accepted["runtime"]["owner_id"] == authority.owner_id
            with sessions() as session:
                before = session.get(ThermalSimulation, sid)
                assert before is not None
                evidence = {
                    column.name: getattr(before, column.name) for column in before.__table__.columns
                }
            for reader in (alice, bob):
                current = reader.get("/thermal-simulations/current").json()
                assert current["settings_revision"] == 1
                assert current["target_c"] == accepted["target_c"]
                assert reader.get(f"/thermal-simulations/{sid}/history").status_code == 200
            with sessions() as session:
                after = session.get(ThermalSimulation, sid)
                assert after is not None
                assert {
                    column.name: getattr(after, column.name) for column in after.__table__.columns
                } == evidence
                assert len(list(session.scalars(select(ThermalSimulation)))) == 1
    finally:
        engine.dispose()
