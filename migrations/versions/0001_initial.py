"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-09 10:47:20-04:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telemetry_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column("building_id", sa.String(length=128), nullable=False),
        sa.Column("zone_id", sa.String(length=128), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temperature_c", sa.Float(), nullable=False),
        sa.Column("humidity_pct", sa.Float(), nullable=False),
        sa.Column("occupancy", sa.Integer(), nullable=False),
        sa.Column("co2_ppm", sa.Integer(), nullable=False),
        sa.Column("hvac_mode", sa.String(length=64), nullable=False),
        sa.Column("airflow_pct", sa.Integer(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("anomaly_flags", sa.JSON(), nullable=False),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_telemetry_events_idempotency_key"),
    )
    op.create_index("ix_telemetry_events_building_id", "telemetry_events", ["building_id"])
    op.create_index("ix_telemetry_events_zone_id", "telemetry_events", ["zone_id"])
    op.create_index("ix_telemetry_events_event_timestamp", "telemetry_events", ["event_timestamp"])
    op.create_index(
        "ix_telemetry_events_processing_status", "telemetry_events", ["processing_status"]
    )

    op.create_table(
        "zone_latest_state",
        sa.Column("building_id", sa.String(length=128), primary_key=True),
        sa.Column("zone_id", sa.String(length=128), primary_key=True),
        sa.Column("last_event_id", sa.String(length=64), nullable=False),
        sa.Column("last_processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=False),
        sa.Column("humidity_pct", sa.Float(), nullable=False),
        sa.Column("occupancy", sa.Integer(), nullable=False),
        sa.Column("co2_ppm", sa.Integer(), nullable=False),
        sa.Column("hvac_mode", sa.String(length=64), nullable=False),
        sa.Column("airflow_pct", sa.Integer(), nullable=False),
        sa.Column("anomaly_flags", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_zone_latest_state_event_timestamp", "zone_latest_state", ["event_timestamp"]
    )

    op.create_table(
        "building_latest_state",
        sa.Column("building_id", sa.String(length=128), primary_key=True),
        sa.Column("last_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("zone_count", sa.Integer(), nullable=False),
        sa.Column("avg_temperature_c", sa.Float(), nullable=False),
        sa.Column("avg_humidity_pct", sa.Float(), nullable=False),
        sa.Column("total_occupancy", sa.Integer(), nullable=False),
        sa.Column("dominant_hvac_mode", sa.String(length=64), nullable=False),
        sa.Column("active_alerts", sa.Integer(), nullable=False),
    )

    op.create_table(
        "hvac_decisions",
        sa.Column("decision_id", sa.String(length=64), primary_key=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("building_id", sa.String(length=128), nullable=False),
        sa.Column("zone_id", sa.String(length=128), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_type", sa.String(length=64), nullable=False),
        sa.Column("recommended_hvac_mode", sa.String(length=64), nullable=True),
        sa.Column("recommended_airflow_pct", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("reason_text", sa.String(length=512), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_hvac_decisions_event_id", "hvac_decisions", ["event_id"])
    op.create_index("ix_hvac_decisions_building_id", "hvac_decisions", ["building_id"])
    op.create_index("ix_hvac_decisions_zone_id", "hvac_decisions", ["zone_id"])
    op.create_index("ix_hvac_decisions_generated_at", "hvac_decisions", ["generated_at"])


def downgrade() -> None:
    op.drop_index("ix_hvac_decisions_generated_at", table_name="hvac_decisions")
    op.drop_index("ix_hvac_decisions_zone_id", table_name="hvac_decisions")
    op.drop_index("ix_hvac_decisions_building_id", table_name="hvac_decisions")
    op.drop_index("ix_hvac_decisions_event_id", table_name="hvac_decisions")
    op.drop_table("hvac_decisions")
    op.drop_table("building_latest_state")
    op.drop_index("ix_zone_latest_state_event_timestamp", table_name="zone_latest_state")
    op.drop_table("zone_latest_state")
    op.drop_index("ix_telemetry_events_processing_status", table_name="telemetry_events")
    op.drop_index("ix_telemetry_events_event_timestamp", table_name="telemetry_events")
    op.drop_index("ix_telemetry_events_zone_id", table_name="telemetry_events")
    op.drop_index("ix_telemetry_events_building_id", table_name="telemetry_events")
    op.drop_table("telemetry_events")
