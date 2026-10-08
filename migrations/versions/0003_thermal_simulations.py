"""Persist thermal room state, command journal and immutable readings."""

import sqlalchemy as sa
from alembic import op

revision = "0003_thermal_simulations"
down_revision = "0002_processing_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "thermal_simulations",
        sa.Column("simulation_id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("building_id", sa.String(length=128), nullable=False, primary_key=False),
        sa.Column("zone_id", sa.String(length=128), nullable=False, primary_key=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=32), nullable=False, primary_key=False),
        sa.Column("resume_generation", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("multiplier", sa.Float(), nullable=False, primary_key=False),
        sa.Column("temperature_c", sa.Float(), nullable=False, primary_key=False),
        sa.Column("heater_on", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("step", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("state_version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("simulated_seconds", sa.Float(), nullable=False, primary_key=False),
        sa.Column("reading_sequence", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("command_sequence", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("last_processed_reading", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("controller_started", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("controller_stopped", sa.Boolean(), nullable=False, primary_key=False),
    )
    op.create_table(
        "thermal_readings",
        sa.Column("simulation_id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("step", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("state_version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("simulated_seconds", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("temperature_c", sa.Float(), nullable=False, primary_key=False),
        sa.Column("heater_on", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("final", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("processed_event_id", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("publication_status", sa.String(length=32), nullable=False, primary_key=False),
        sa.Column("body", sa.Text(), nullable=False, primary_key=False),
        sa.Column("transport_receipt", sa.JSON(), nullable=True, primary_key=False),
    )
    op.create_table(
        "thermal_commands",
        sa.Column("simulation_id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("decision_id", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("applied_state_version", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("reading_sequence", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("heater_on", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("transport_message_id", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("status", sa.String(length=32), nullable=False, primary_key=False),
    )


def downgrade() -> None:
    op.drop_table("thermal_commands")
    op.drop_table("thermal_readings")
    op.drop_table("thermal_simulations")
