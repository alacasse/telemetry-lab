"""Opt-in upgrade rehearsal using a private PostgreSQL database and LocalStack queue."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
import pytest
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import sessionmaker

from demo.thermal_runtime import RoomClock
from packages.db.migration_handler import run_migrations
from packages.thermal.commands import parse_command_message
from packages.thermal.models import ThermalCommand, ThermalSimulation
from packages.thermal.service import recover_simulations, resume_simulation


def test_legacy_command_survives_real_upgrade_and_redelivery() -> None:
    url = os.getenv("TELEMETRY_LAB_MIGRATION_POSTGRES_URL")
    endpoint = os.getenv("TELEMETRY_LAB_MIGRATION_SQS_ENDPOINT")
    if not url or not endpoint:
        pytest.skip("Run scripts/test-thermal-command-migration.sh for isolated services")
    assert endpoint.startswith("http://127.0.0.1:")
    assert os.getenv("TELEMETRY_LAB_MIGRATION_PRIVATE_SERVICES") == "1"
    sqs = boto3.client(
        "sqs",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    queue = sqs.create_queue(QueueName=f"telemetry-lab-local-migration-{uuid4().hex}")["QueueUrl"]
    expired_queue = sqs.create_queue(
        QueueName=f"telemetry-lab-local-expired-{uuid4().hex}"
    )["QueueUrl"]
    engine = create_engine(url)
    sessions = sessionmaker(bind=engine)
    ids = [str(uuid4()), str(uuid4())]
    now = datetime.now(UTC)
    deadline = now + timedelta(seconds=30)
    evidence: dict = {
        "endpoint": endpoint,
        "containers": json.loads(os.environ["TELEMETRY_LAB_MIGRATION_CONTAINERS"]),
        "legacy_body": json.dumps({"simulation_id": ids[0], "sequence": 1}),
    }
    tables = (
        "thermal_simulations",
        "thermal_readings",
        "thermal_commands",
        "hvac_decisions",
        "processing_observations",
    )

    def rows(connection: Connection) -> dict[str, list[dict[str, Any]]]:
        return {
            name: [
                dict(row)
                for row in connection.execute(
                    select(metadata.tables[name]).order_by(
                        *metadata.tables[name].primary_key.columns
                    )
                ).mappings()
            ]
            for name in tables
        }

    try:
        assert run_migrations("0003_thermal_simulations", database_url=url)[
            "schema_matches_requested_revision"
        ]
        metadata = MetaData()
        metadata.reflect(bind=engine)
        with engine.begin() as connection:
            for index, sid in enumerate(ids):
                connection.execute(
                    metadata.tables["thermal_simulations"].insert(),
                    dict(
                        simulation_id=sid,
                        building_id="thermal-demo",
                        zone_id="room-1",
                        created_at=now,
                        expires_at=deadline if index == 0 else now - timedelta(seconds=1),
                        status="active",
                        resume_generation=0,
                        multiplier=1.0,
                        temperature_c=19.0,
                        heater_on=False,
                        step=0,
                        state_version=0,
                        simulated_seconds=0.0,
                        reading_sequence=1,
                        command_sequence=3,
                        last_processed_reading=1,
                        controller_started=True,
                        controller_stopped=False,
                    ),
                )
                connection.execute(
                    metadata.tables["thermal_readings"].insert(),
                    dict(
                        simulation_id=sid,
                        sequence=1,
                        step=0,
                        state_version=0,
                        simulated_seconds=0.0,
                        observed_at=now,
                        temperature_c=19.0,
                        heater_on=False,
                        final=False,
                        published_at=now,
                        processed_event_id=f"event-{index}",
                        processed_at=now,
                        publication_status="accepted",
                        body='{"immutable":"old reading bytes"}',
                        transport_receipt={"status": "accepted", "message_id": f"receipt-{index}"},
                    ),
                )
                for seq, status in enumerate(("pending", "applied", "cancelled"), 1):
                    decision = f"decision-{index}-{seq}"
                    connection.execute(
                        metadata.tables["hvac_decisions"].insert(),
                        dict(
                            decision_id=decision,
                            event_id=f"event-{index}",
                            building_id="thermal-demo",
                            zone_id="room-1",
                            generated_at=now,
                            decision_type="thermal",
                            recommended_hvac_mode="heating",
                            recommended_airflow_pct=None,
                            reason_code="migration-fixture",
                            reason_text="Legacy heating decision",
                            confidence=1.0,
                            applied=status == "applied",
                        ),
                    )
                    connection.execute(
                        metadata.tables["thermal_commands"].insert(),
                        dict(
                            simulation_id=sid,
                            sequence=seq,
                            decision_id=decision,
                            reading_sequence=1,
                            heater_on=seq != 3,
                            created_at=now,
                            published_at=now,
                            transport_message_id=f"old-published-{index}-{seq}",
                            applied_at=now if status == "applied" else None,
                            applied_state_version=1 if status == "applied" else None,
                            status=status,
                        ),
                    )
                connection.execute(
                    metadata.tables["processing_observations"].insert(),
                    dict(
                        observation_id=f"proof-{index}",
                        attempt_id=f"attempt-{index}",
                        correlation_id=sid,
                        event_id=f"event-{index}",
                        envelope_id=f"envelope-{index}",
                        transport_message_id=f"telemetry-{index}",
                        original_event_id=None,
                        kind="processed",
                        observed_at=now,
                        runtime="sqs-compatible-emulator",
                        process_id=os.getpid(),
                        release_revision="legacy-fixture",
                        elapsed_ms=1.0,
                    ),
                )
            before = rows(connection)
        # Old publishers are absent: only this one-shot fixture exists in these services.
        message_id = sqs.send_message(QueueUrl=queue, MessageBody=evidence["legacy_body"])[
            "MessageId"
        ]
        expired_body = json.dumps({"simulation_id": ids[1], "sequence": 1})
        expired_message_id = sqs.send_message(QueueUrl=expired_queue, MessageBody=expired_body)[
            "MessageId"
        ]
        with engine.begin() as connection:
            connection.execute(
                metadata.tables["thermal_commands"]
                .update()
                .where(metadata.tables["thermal_commands"].c.simulation_id == ids[0])
                .where(metadata.tables["thermal_commands"].c.sequence == 1)
                .values(transport_message_id=message_id)
            )
            connection.execute(
                metadata.tables["thermal_commands"]
                .update()
                .where(metadata.tables["thermal_commands"].c.simulation_id == ids[1])
                .where(metadata.tables["thermal_commands"].c.sequence == 1)
                .values(transport_message_id=expired_message_id)
            )
            before = rows(connection)
        evidence["typed_migration"] = run_migrations(
            "0004_typed_thermal_commands", database_url=url
        )
        assert evidence["typed_migration"]["schema_matches_requested_revision"]
        metadata = MetaData()
        metadata.reflect(bind=engine)
        with engine.begin() as connection:
            # Typed commands authored after 0004 must retain their strict wire contract.
            connection.execute(
                metadata.tables["thermal_commands"]
                .update()
                .where(metadata.tables["thermal_commands"].c.sequence == 3)
                .values(legacy_wire_allowed=False)
            )
            typed_before = rows(connection)
        evidence["cooling_migration"] = run_migrations(
            "0005_cooling_and_stop", database_url=url
        )
        metadata = MetaData()
        metadata.reflect(bind=engine)
        with engine.connect() as connection:
            cooling_before = rows(connection)
        evidence["migration"] = run_migrations(database_url=url)
        assert evidence["migration"]["schema_matches_expected_head"]
        metadata = MetaData()
        metadata.reflect(bind=engine)
        with engine.connect() as connection:
            after = rows(connection)
        for name in tables:
            stripped = [
                {k: row[k] for k in old}
                for row, old in zip(after[name], typed_before[name], strict=True)
            ]
            assert stripped == typed_before[name], name
            assert [
                {k: row[k] for k in old}
                for row, old in zip(after[name], cooling_before[name], strict=True)
            ] == cooling_before[name], name
        for row in after["thermal_simulations"]:
            assert row["policy"] == "scenario"
            assert row["expires_at"] is not None
            assert row["settings_revision"] == 0
            assert row["mode"] == "heating"
            assert row["cooler_on"] is False
            assert row["stop_request_id"] is None
            assert row["stop_requested_at"] is None
            assert row["stop_command_sequence"] is None
            assert row["predecessor_simulation_id"] is None
        for row in after["thermal_readings"]:
            assert row["cooler_on"] is False
        for row in after["thermal_commands"]:
            assert row["origin"] == "automatic"
            assert row["stop_request_id"] is None
            assert row["final_reading_sequence"] is None
        assert [c["legacy_wire_allowed"] for c in after["thermal_commands"]] == [
            True, True, False,
        ] * 2
        assert [c["command_type"] for c in after["thermal_commands"]] == [
            "heating.start",
            "heating.start",
            "heating.stop",
        ] * 2
        with sessions() as session, session.begin():
            recover_simulations(session)
        clock = RoomClock(sessions, monotonic=lambda: 0.0)
        assert clock.command(ids[0], 1) is False
        with sessions() as session, session.begin():
            resumed = resume_simulation(session, ids[0])
            assert resumed.expires_at == deadline
            assert resumed.status == "active"

        def receive(queue_url: str = queue) -> dict[str, Any]:
            until = time.monotonic() + 10
            while time.monotonic() < until:
                messages = sqs.receive_message(
                    QueueUrl=queue_url,
                    WaitTimeSeconds=1,
                    VisibilityTimeout=1,
                    AttributeNames=["ApproximateReceiveCount"],
                ).get("Messages", [])
                if messages:
                    return dict(messages[0])
            pytest.fail("Legacy SQS delivery missing")

        first = receive()
        body = parse_command_message(first["Body"])
        assert clock.command(body.simulation_id, body.sequence, body.command_type)
        with sessions() as session:
            applied = session.get(ThermalCommand, (ids[0], 1))
            assert applied is not None
            original_application = (applied.applied_at, applied.applied_state_version)
            assert applied.status == "applied"
            assert applied.transport_message_id == message_id
            assert applied.published_at == now
        # Omit DeleteMessage after commit, then exercise the actual SQS redelivery.
        time.sleep(1.2)
        duplicate = receive()
        assert duplicate["MessageId"] == first["MessageId"] == message_id
        assert int(duplicate["Attributes"]["ApproximateReceiveCount"]) > int(
            first["Attributes"]["ApproximateReceiveCount"]
        )
        assert clock.command(body.simulation_id, body.sequence, body.command_type)
        sqs.delete_message(QueueUrl=queue, ReceiptHandle=duplicate["ReceiptHandle"])
        with sessions() as session:
            applied = session.get(ThermalCommand, (ids[0], 1))
            assert applied is not None
            assert (applied.applied_at, applied.applied_state_version) == original_application
            active = session.get(ThermalSimulation, ids[0])
            assert active is not None
            assert active.heater_on
        with sessions() as session, session.begin():
            expired = resume_simulation(session, ids[1])
            assert expired.status == "expired"
            assert expired.expires_at == now - timedelta(seconds=1)
        expired_delivery = receive(expired_queue)
        expired_wire = parse_command_message(expired_delivery["Body"])
        assert expired_delivery["MessageId"] == expired_message_id
        assert clock.command(
            expired_wire.simulation_id, expired_wire.sequence, expired_wire.command_type
        )
        sqs.delete_message(QueueUrl=expired_queue, ReceiptHandle=expired_delivery["ReceiptHandle"])
        with sessions() as session:
            expired_state = session.get(ThermalSimulation, ids[1])
            expired_command = session.get(ThermalCommand, (ids[1], 1))
            assert expired_state is not None and expired_command is not None
            assert not expired_state.heater_on
            assert expired_command.applied_at is None
            assert expired_command.transport_message_id == expired_message_id
            assert expired_command.published_at == now
        evidence.update(
            before=before,
            after_typed_migration=typed_before,
            after_migration=after,
            deliveries=[
                {
                    "message_id": m["MessageId"],
                    "receive_count": m["Attributes"]["ApproximateReceiveCount"],
                }
                for m in (first, duplicate, expired_delivery)
            ],
            applied_at=original_application[0],
        )
        if path := os.getenv("TELEMETRY_LAB_MIGRATION_EVIDENCE"):
            Path(path).write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    finally:
        sqs.delete_queue(QueueUrl=queue)
        sqs.delete_queue(QueueUrl=expired_queue)
        engine.dispose()
