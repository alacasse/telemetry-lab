from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from packages.db.base import Base
from packages.thermal.http import create_router
from packages.thermal.models import ThermalSetting, ThermalSimulation
from packages.thermal.service import create_simulation, create_thermostat


@pytest.fixture
def sessions() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(engine)
    engine.dispose()


def client(sessions: sessionmaker[Session], status: str = "unknown") -> TestClient:
    app = FastAPI()
    app.include_router(
        create_router(
            sessions,
            time_multiplier=1.5,
            observe_runtime=lambda: {"status": status, "source": "test-observer"},
        )
    )
    return TestClient(app)


def test_construction_and_missing_reads_do_not_initialize(sessions: sessionmaker[Session]) -> None:
    api = client(sessions)
    assert api.get("/thermal-simulations/current").json() == {"detail": "thermostat_not_found"}
    missing = str(uuid4())
    for path in [missing, f"{missing}/history", f"{missing}/settings/{uuid4()}"]:
        assert api.get(f"/thermal-simulations/{path}").status_code == 404
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ThermalSimulation)) == 0


def test_reads_and_router_reload_preserve_state(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        row = create_thermostat(session)
        row.heater_on = True
        row.phase = "acting"
        sid = row.simulation_id
    with sessions() as session:
        initial = session.get(ThermalSimulation, sid)
        assert initial is not None
        before = {c.name: getattr(initial, c.name) for c in initial.__table__.columns}
    for status in ["unknown", "unavailable", "available"]:
        api = client(sessions, status)
        current = api.get("/thermal-simulations/current")
        evidence = api.get(f"/thermal-simulations/{sid}")
        assert current.json() == evidence.json()
        assert evidence.headers["cache-control"] == "no-store"
        assert evidence.json()["runtime"] == {"status": status, "source": "test-observer"}
        assert evidence.json()["status"] == "active"
        assert api.get(f"/thermal-simulations/{sid}/history").status_code == 200
    with sessions() as session:
        persisted = session.get(ThermalSimulation, sid)
        assert persisted is not None
        assert {c.name: getattr(persisted, c.name) for c in persisted.__table__.columns} == before


def test_evidence_does_not_expire_scenario(sessions: sessionmaker[Session]) -> None:
    sid = str(uuid4())
    with sessions.begin() as session:
        create_simulation(session, sid, now=datetime.now(UTC) - timedelta(days=1))
    assert client(sessions).get(f"/thermal-simulations/{sid}").json()["status"] == "active"
    with sessions() as session:
        assert session.get(ThermalSimulation, sid).status == "active"  # type: ignore[union-attr]


def test_creation_settings_idempotency_conflicts_and_history(
    sessions: sessionmaker[Session],
) -> None:
    api = client(sessions)
    sid, operation = str(uuid4()), str(uuid4())
    start = api.post("/thermal-simulations", json={"simulation_id": sid, "policy": "thermostat"})
    assert start.status_code == 200 and start.json()["multiplier"] == 1.5
    assert start.headers["cache-control"] == "no-store"
    setting = {
        "operation_id": operation,
        "expected_revision": 0,
        "mode": "heating",
        "target_c": 24,
    }
    url = f"/thermal-simulations/{sid}/settings"
    first = api.post(url, json=setting)
    assert first.status_code == 200 and first.json()["settings_revision"] == 1
    assert api.post(url, json=setting).json() == first.json()
    assert api.post(url, json={**setting, "target_c": 25}).status_code == 409
    assert api.post(url, json={**setting, "operation_id": str(uuid4())}).status_code == 409
    lookup = api.get(f"{url}/{operation}")
    assert lookup.status_code == 200 and lookup.json()["revision"] == 1
    assert lookup.headers["cache-control"] == "no-store"
    history = api.get(f"/thermal-simulations/{sid}/history?kind=settings&cursor=-1")
    assert history.json() == {"items": [lookup.json()], "next_cursor": None}
    assert (
        api.get(f"/thermal-simulations/{sid}/history?kind=settings&cursor=1").json()["items"] == []
    )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ThermalSetting)) == 1
    stopped = api.post(f"/thermal-simulations/{sid}/stop")
    assert stopped.status_code == 200 and stopped.json()["status"] == "stopping"
    assert api.post(f"/thermal-simulations/{sid}/resume").status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_revision": -1},
        {"expected_revision": "0"},
        {"mode": "automatic"},
        {"target_c": 14},
        {"target_c": 30.5},
        {"target_c": 22.1},
        {"operation_id": "bad"},
        {"extra": True},
    ],
)
def test_invalid_settings_are_rejected(
    sessions: sessionmaker[Session],
    changes: dict,
) -> None:
    request = {
        "operation_id": str(uuid4()),
        "expected_revision": 0,
        "mode": "heating",
        "target_c": 24,
    }
    assert (
        client(sessions)
        .post(f"/thermal-simulations/{uuid4()}/settings", json={**request, **changes})
        .status_code
        == 422
    )


def test_missing_mutations_and_invalid_history(sessions: sessionmaker[Session]) -> None:
    api = client(sessions)
    sid = str(uuid4())
    for action in ["resume", "stop"]:
        response = api.post(f"/thermal-simulations/{sid}/{action}")
        assert response.status_code == 404 and response.json()["detail"] == "simulation_not_found"
    assert api.get(f"/thermal-simulations/{sid}/history?kind=other").status_code == 422
    assert api.get("/thermal-simulations/invalid").status_code == 422


def test_evidence_database_failure_is_distinct_from_missing() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        response = client(sessionmaker(engine)).get(f"/thermal-simulations/{uuid4()}")
        assert response.status_code == 503
        assert response.json() == {"detail": "thermal_evidence_unavailable"}
    finally:
        engine.dispose()
