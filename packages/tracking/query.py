"""Targeted, passive, allowlisted evidence reads. No queue client or raw payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from packages.db.models import HvacDecision, ProcessingObservation, TelemetryEvent

LIMIT = 100


def summarize_sends(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify only visible deliveries, not absolute SQS receive counts or HTTP receipts."""
    sends: dict[str, dict[str, Any]] = {}
    attempts: set[str] = set()
    messages: set[tuple[str, str, str]] = set()
    for observation in observations:
        event_id = observation["event_id"]
        send = sends.setdefault(event_id, {"event_id": event_id, "attempts": []})
        attempt_id = observation["attempt_id"]
        if attempt_id in attempts:
            continue
        attempts.add(attempt_id)
        transport = observation["transport_message_id"]
        key = (event_id, observation["envelope_id"], transport)
        delivery = (
            "unknown" if not transport else ("redelivery" if key in messages else "first_observed")
        )
        messages.add(key)
        send["attempts"].append(
            {
                "attempt_id": attempt_id,
                "envelope_id": observation["envelope_id"],
                "transport_message_id": transport,
                "delivery": delivery,
            }
        )
    return list(sends.values())


def read_trace(
    sessions: sessionmaker[Session],
    correlation_id: str,
    event_id: str | None = None,
) -> dict[str, Any]:
    fetched_at = datetime.now(UTC)
    observations: list[dict[str, Any]] = []
    journal_quality = "observed"
    truncated = False
    try:
        with sessions() as session:
            stmt = select(ProcessingObservation).where(
                ProcessingObservation.correlation_id == correlation_id
            )
            if event_id:
                stmt = stmt.where(ProcessingObservation.event_id == event_id)
            rows = session.scalars(
                stmt.order_by(
                    ProcessingObservation.observed_at, ProcessingObservation.observation_id
                ).limit(LIMIT + 1)
            ).all()
            truncated = len(rows) > LIMIT
            for row in rows[:LIMIT]:
                observations.append(
                    {
                        "observation_id": row.observation_id,
                        "attempt_id": row.attempt_id,
                        "event_id": row.event_id,
                        "envelope_id": row.envelope_id,
                        "transport_message_id": row.transport_message_id,
                        "original_event_id": row.original_event_id,
                        "kind": row.kind,
                        "source": "processing-worker",
                        "storage": "postgresql-journal",
                        "runtime": row.runtime,
                        "process_id": row.process_id,
                        "release_revision": row.release_revision,
                        "observed_at": row.observed_at,
                        "fetched_at": fetched_at,
                        "scope": "attempt",
                        "quality": "observed",
                        "elapsed_ms": row.elapsed_ms,
                    }
                )
    except SQLAlchemyError:
        journal_quality = "unavailable"

    # A failed journal query cannot hide independently committed business data.
    results = []
    original_ids = {o["original_event_id"] for o in observations if o["original_event_id"]}
    with sessions() as session:
        match = TelemetryEvent.correlation_id == correlation_id
        if event_id:
            match = match & (TelemetryEvent.event_id == event_id)
        if original_ids:
            match = match | TelemetryEvent.event_id.in_(original_ids)
        events = session.scalars(
            select(TelemetryEvent)
            .where(match)
            .order_by(TelemetryEvent.event_timestamp, TelemetryEvent.event_id)
            .limit(LIMIT + 1)
        ).all()
        truncated = truncated or len(events) > LIMIT
        for event in events[:LIMIT]:
            decisions = session.scalars(
                select(HvacDecision).where(HvacDecision.event_id == event.event_id)
            ).all()
            results.append(
                {
                    "event_id": event.event_id,
                    "status": event.processing_status,
                    "source": "query-service",
                    "storage": "postgresql-business-result",
                    "quality": "observed",
                    "scope": "measurement",
                    "observed_at": fetched_at,
                    "fetched_at": fetched_at,
                    "event_timestamp": event.event_timestamp,
                    "received_at": event.received_at,
                    "processed_at": event.processed_at,
                    "measurement": {
                        k: getattr(event, k)
                        for k in (
                            "building_id",
                            "zone_id",
                            "temperature_c",
                            "humidity_pct",
                            "occupancy",
                            "co2_ppm",
                            "hvac_mode",
                            "airflow_pct",
                        )
                    },
                    "decisions": [
                        {
                            "decision_id": d.decision_id,
                            "event_id": d.event_id,
                            "generated_at": d.generated_at,
                            "decision_type": d.decision_type,
                            "recommended_airflow_pct": d.recommended_airflow_pct,
                            "recommended_hvac_mode": d.recommended_hvac_mode,
                            "reason_code": d.reason_code,
                            "reason_text": d.reason_text,
                            "applied": d.applied,
                        }
                        for d in decisions
                    ],
                }
            )
    # Membership comes from persisted originals/terminal links, never delivery order
    # or guessed similarity of normalized payloads. Unlinked sends stay unassigned.
    membership = {r["event_id"]: [r["event_id"]] for r in results}
    for observation in observations:
        original = observation["original_event_id"]
        if original:
            members = membership.setdefault(original, [original])
            if observation["event_id"] not in members:
                members.append(observation["event_id"])
    return {
        "measurements": [
            {"original_event_id": original, "send_event_ids": members}
            for original, members in membership.items()
        ],
        "correlation_id": correlation_id,
        "event_id": event_id,
        "fetched_at": fetched_at,
        "source": "query-service",
        "scope": "simulation",
        "quality": "observed",
        "journal_quality": journal_quality,
        "observations": observations,
        "sends": summarize_sends(observations),
        "results": results,
        "result_quality": "observed" if results else "unknown",
        "truncated": truncated,
    }
