"""Container-only application actions and persistent evidence for the disposable suite."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from uuid import uuid4

import boto3
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from packages.db.session import create_session_factory
from packages.thermal.models import (
    ThermalAuthority,
    ThermalCommand,
    ThermalReading,
    ThermalSetting,
    ThermalSimulation,
)
from packages.thermal.service import request_settings, resume_simulation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "snapshot", "settings", "resume"])
    args = parser.parse_args()
    if args.action == "setup":
        command.upgrade(Config("alembic.ini"), "head")
        client = boto3.client("sqs", endpoint_url=os.environ["AWS_ENDPOINT_URL"],
                              region_name=os.environ.get("AWS_REGION", "ca-central-1"))
        for name in ("thermal-validation-measurements", "thermal-validation-commands"):
            client.create_queue(QueueName=name)
        return
    sessions = create_session_factory()
    with sessions.begin() as session:
        room = session.scalar(
            select(ThermalSimulation).order_by(ThermalSimulation.created_at.desc())
        )
        if args.action in {"settings", "resume"}:
            assert room is not None
            if args.action == "settings":
                request_settings(session, room.simulation_id, str(uuid4()),
                                 room.settings_revision, "heating", 30)
            else:
                resume_simulation(session, room.simulation_id)
        authority = session.get(ThermalAuthority, "shared-room")
        assert authority is not None
        readings = list(session.scalars(select(ThermalReading)))
        commands = list(session.scalars(select(ThermalCommand)))
        business = []
        for model in (ThermalSimulation, ThermalReading, ThermalCommand, ThermalSetting):
            rows = session.scalars(select(model).order_by(*model.__table__.primary_key))
            business.append([
                {column.name: getattr(row, column.name) for column in model.__table__.columns}
                for row in rows
            ])
        result = {
            "business": hashlib.sha256(
                json.dumps(business, sort_keys=True, default=str).encode()
            ).hexdigest(),
            "generation": authority.generation,
            "owner": authority.owner_id,
            "free": authority.owner_id is None or authority.expires_at is None or
                    authority.expires_at <= session.scalar(text("SELECT clock_timestamp()")),
            "clock": str(authority.clock_passed_at),
            "room": None if room is None else {
                key: getattr(room, key) for key in (
                    "simulation_id", "status", "step", "temperature_c", "simulated_seconds",
                    "reading_sequence", "command_sequence", "settings_revision",
                    "resume_generation",
                )
            },
            "published": sum(r.published_at is not None and r.transport_receipt is not None
                             for r in readings),
            "processed": sum(r.processed_at is not None for r in readings),
            "applied": sum(c.applied_at is not None for c in commands),
            "command_receipts": sum(c.transport_message_id is not None for c in commands),
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
