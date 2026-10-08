from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from sqlalchemy.orm import Session, sessionmaker

from packages.aws import annotate_trace, configure_tracing
from packages.config import Settings, get_settings
from packages.db.repositories import (
    get_building_state,
    list_buildings,
    list_decisions,
    list_events,
    list_zones,
)
from packages.db.session import create_session_factory
from packages.logging import configure_logging
from packages.schemas.api import (
    BuildingItem,
    BuildingListResponse,
    BuildingStateResponse,
    DecisionItem,
    DecisionListResponse,
    EventItem,
    EventListResponse,
    HealthResponse,
    ZoneListResponse,
    ZoneStateItem,
)

settings = get_settings()
settings.require_staging_runtime_env_vars("APP_ENV", "DATABASE_SECRET_ARN", "QUEUE_BACKEND")
configure_tracing("query-service", settings)
configure_logging("query-service", settings.log_level)
app = FastAPI(title="Telemetry Lab Query Service")


def get_settings_dependency() -> Settings:
    return get_settings()


@lru_cache(maxsize=1)
def _get_cached_session_factory() -> sessionmaker[Session]:
    return create_session_factory(get_settings())


def clear_dependency_caches() -> None:
    _get_cached_session_factory.cache_clear()


def get_session_factory_dependency() -> sessionmaker[Session]:
    return _get_cached_session_factory()


def require_staging_auth(
    x_staging_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dependency),
) -> None:
    if not settings.is_staging:
        return

    expected_token = settings.resolve_staging_auth_token()
    if x_staging_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="staging_auth_required"
        )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="query-service")


@app.get("/buildings", response_model=BuildingListResponse)
def buildings(
    _: None = Depends(require_staging_auth),
    session_factory: sessionmaker[Session] = Depends(get_session_factory_dependency),
) -> BuildingListResponse:
    with session_factory() as session:
        items = [BuildingItem(building_id=item.building_id) for item in list_buildings(session)]
    return BuildingListResponse(items=items)


@app.get("/buildings/{building_id}/state", response_model=BuildingStateResponse)
def building_state(
    building_id: str,
    _: None = Depends(require_staging_auth),
    session_factory: sessionmaker[Session] = Depends(get_session_factory_dependency),
) -> BuildingStateResponse:
    annotate_trace(building_id=building_id)
    with session_factory() as session:
        item = get_building_state(session, building_id)
        if item is None:
            raise HTTPException(status_code=404, detail="building_not_found")
        return BuildingStateResponse(
            building_id=item.building_id,
            last_processed_at=item.last_processed_at,
            zone_count=item.zone_count,
            avg_temperature_c=item.avg_temperature_c,
            avg_humidity_pct=item.avg_humidity_pct,
            total_occupancy=item.total_occupancy,
            dominant_hvac_mode=item.dominant_hvac_mode,
            active_alerts=item.active_alerts,
        )


@app.get("/buildings/{building_id}/zones", response_model=ZoneListResponse)
def building_zones(
    building_id: str,
    _: None = Depends(require_staging_auth),
    session_factory: sessionmaker[Session] = Depends(get_session_factory_dependency),
) -> ZoneListResponse:
    annotate_trace(building_id=building_id)
    with session_factory() as session:
        items = [
            ZoneStateItem(
                zone_id=item.zone_id,
                last_processed_at=item.last_processed_at,
                temperature_c=item.temperature_c,
                humidity_pct=item.humidity_pct,
                occupancy=item.occupancy,
                co2_ppm=item.co2_ppm,
                hvac_mode=item.hvac_mode,
                airflow_pct=item.airflow_pct,
                anomaly_flags=item.anomaly_flags,
            )
            for item in list_zones(session, building_id)
        ]
    if not items:
        raise HTTPException(status_code=404, detail="building_not_found")
    return ZoneListResponse(building_id=building_id, items=items)


@app.get("/buildings/{building_id}/decisions", response_model=DecisionListResponse)
def building_decisions(
    building_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    _: None = Depends(require_staging_auth),
    session_factory: sessionmaker[Session] = Depends(get_session_factory_dependency),
) -> DecisionListResponse:
    annotate_trace(building_id=building_id)
    with session_factory() as session:
        items = [
            DecisionItem(
                decision_id=item.decision_id,
                zone_id=item.zone_id,
                generated_at=item.generated_at,
                decision_type=item.decision_type,
                recommended_hvac_mode=item.recommended_hvac_mode,
                recommended_airflow_pct=item.recommended_airflow_pct,
                reason=item.reason_code,
            )
            for item in list_decisions(session, building_id, limit=limit)
        ]
    return DecisionListResponse(building_id=building_id, items=items)


@app.get("/buildings/{building_id}/events", response_model=EventListResponse)
def building_events(
    building_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    _: None = Depends(require_staging_auth),
    session_factory: sessionmaker[Session] = Depends(get_session_factory_dependency),
) -> EventListResponse:
    annotate_trace(building_id=building_id)
    with session_factory() as session:
        items = [
            EventItem(
                event_id=item.event_id,
                zone_id=item.zone_id,
                processed_at=item.processed_at,
                temperature_c=item.temperature_c,
                humidity_pct=item.humidity_pct,
                occupancy=item.occupancy,
                co2_ppm=item.co2_ppm,
                hvac_mode=item.hvac_mode,
                airflow_pct=item.airflow_pct,
                processing_status=item.processing_status,
            )
            for item in list_events(session, building_id, limit=limit)
        ]
    return EventListResponse(building_id=building_id, items=items)


@app.get("/simulations/{correlation_id}")
def simulation_trace(
    correlation_id: str,
    event_id: str | None = Query(default=None, min_length=1, max_length=64),
    _: None = Depends(require_staging_auth),
    settings: Settings = Depends(get_settings_dependency),
    session_factory: sessionmaker[Session] = Depends(get_session_factory_dependency),
) -> dict:
    from sqlalchemy.exc import SQLAlchemyError

    from packages.tracking.query import read_trace

    if not 1 <= len(correlation_id) <= 128:
        raise HTTPException(status_code=422, detail="invalid_correlation_id")
    try:
        result = read_trace(session_factory, correlation_id, event_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="trace_unavailable") from exc
    result["runtime"] = "local-python-process" if not settings.is_staging else "unknown"
    for item in result["results"]:
        item["runtime"] = result["runtime"]
    return result
