"""Local-only controls; evidence reads have no simulation side effects."""

from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from packages.db.session import create_session_factory
from packages.thermal.models import ThermalSetting, ThermalSimulation
from packages.thermal.service import (
    SimulationConflict,
    create_simulation,
    create_thermostat,
    history_page,
    request_settings,
    request_stop,
    resume_simulation,
    snapshot,
)

router = APIRouter()


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


def local_snapshot(session: Session, row: ThermalSimulation) -> dict:
    result = snapshot(session, row)
    result["runtime"] = runtime_observation()
    return result


def multiplier() -> float:
    value = float(os.environ.get("THERMAL_TIME_MULTIPLIER", "1"))
    if not math.isfinite(value) or not 0.5 <= value <= 2:
        raise ValueError("THERMAL_TIME_MULTIPLIER must be between 0.5 and 2")
    return value


@lru_cache(maxsize=1)
def sessions() -> sessionmaker[Session]:
    return create_session_factory()


class StartSimulation(BaseModel):
    simulation_id: UUID
    policy: Literal["scenario", "thermostat"] = "scenario"
    mode: Literal["heating", "cooling"] = "heating"
    predecessor_simulation_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")


@router.post("/thermal-simulations")
def start(request: StartSimulation, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    with sessions()() as session, session.begin():
        try:
            row = (
                create_thermostat(session, str(request.simulation_id), multiplier())
                if request.policy == "thermostat"
                else create_simulation(
                    session,
                    str(request.simulation_id),
                    multiplier=multiplier(),
                    mode=request.mode,
                    predecessor_simulation_id=str(request.predecessor_simulation_id)
                    if request.predecessor_simulation_id
                    else None,
                )
            )
        except SimulationConflict as exc:
            live = session.scalar(
                select(ThermalSimulation).where(
                    ThermalSimulation.status.in_(["active", "interrupted", "stopping"])
                )
            )
            raise HTTPException(
                409,
                {
                    "error": str(exc),
                    "mode": live.mode if live else None,
                    "simulation_id": live.simulation_id if live else None,
                },
            ) from exc
        return local_snapshot(session, row)


@router.get("/thermal-simulations/current")
def current(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    with sessions()() as session:
        row = session.scalar(
            select(ThermalSimulation)
            .where(ThermalSimulation.policy == "thermostat")
            .order_by(ThermalSimulation.created_at.desc())
            .limit(1)
        )
        if row is None:
            raise HTTPException(404, "thermostat_not_found")
        return local_snapshot(session, row)


@router.get("/thermal-simulations/{simulation_id}")
def evidence(simulation_id: UUID, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        with sessions()() as session:
            row = session.get(ThermalSimulation, str(simulation_id))
            if row is None:
                raise HTTPException(404, "simulation_not_found")
            return local_snapshot(session, row)
    except SQLAlchemyError as exc:
        raise HTTPException(503, "thermal_evidence_unavailable") from exc


@router.post("/thermal-simulations/{simulation_id}/resume")
def resume(simulation_id: UUID, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    with sessions()() as session, session.begin():
        try:
            row = resume_simulation(session, str(simulation_id))
        except KeyError as exc:
            raise HTTPException(404, "simulation_not_found") from exc
        return local_snapshot(session, row)


@router.post("/thermal-simulations/{simulation_id}/stop")
def stop(simulation_id: UUID, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    with sessions()() as session, session.begin():
        try:
            row = request_stop(session, str(simulation_id))
        except KeyError as exc:
            raise HTTPException(404, "simulation_not_found") from exc
        return local_snapshot(session, row)


class SettingRequest(BaseModel):
    operation_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    mode: Literal["heating", "cooling"]
    target_c: float = Field(ge=15, le=30, allow_inf_nan=False)
    model_config = ConfigDict(extra="forbid")

    @field_validator("target_c")
    @classmethod
    def half_degree(cls, value: float) -> float:
        if value * 2 != round(value * 2):
            raise ValueError("target must use half-degree increments")
        return value


@router.post("/thermal-simulations/{simulation_id}/settings")
def settings_request(simulation_id: UUID, request: SettingRequest, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    with sessions()() as session, session.begin():
        try:
            request_settings(
                session,
                str(simulation_id),
                str(request.operation_id),
                request.expected_revision,
                request.mode,
                request.target_c,
            )
            row = session.get(ThermalSimulation, str(simulation_id))
            assert row is not None
            return local_snapshot(session, row)
        except KeyError as exc:
            raise HTTPException(404, "simulation_not_found") from exc
        except SimulationConflict as exc:
            raise HTTPException(409, str(exc)) from exc


@router.get("/thermal-simulations/{simulation_id}/settings/{operation_id}")
def setting_lookup(simulation_id: UUID, operation_id: UUID, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    with sessions()() as session:
        item = session.get(ThermalSetting, str(operation_id))
        if item is None or item.simulation_id != str(simulation_id):
            raise HTTPException(404, "setting_not_found")
        return {column.name: getattr(item, column.name) for column in item.__table__.columns}


@router.get("/thermal-simulations/{simulation_id}/history")
def history(
    simulation_id: UUID,
    response: Response,
    kind: Literal["readings", "commands", "settings", "decisions"] = "readings",
    cursor: int = 0,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    with sessions()() as session:
        if session.get(ThermalSimulation, str(simulation_id)) is None:
            raise HTTPException(404, "simulation_not_found")
        return history_page(session, str(simulation_id), kind, max(0, cursor))
