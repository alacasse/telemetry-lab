"""Durable attempt observations and independent business-result correlation."""

import sqlalchemy as sa
from alembic import op

revision = "0002_processing_observations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("telemetry_events", sa.Column("correlation_id", sa.String(128), nullable=True))
    op.create_index("ix_telemetry_events_correlation_id", "telemetry_events", ["correlation_id"])
    op.create_table(
        "processing_observations",
        sa.Column("observation_id", sa.String(64), primary_key=True),
        sa.Column("attempt_id", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("envelope_id", sa.String(64), nullable=False),
        sa.Column("transport_message_id", sa.String(128), nullable=True),
        sa.Column("original_event_id", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("runtime", sa.String(64), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("release_revision", sa.String(128), nullable=False),
        sa.Column("elapsed_ms", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_processing_observations_correlation_id", "processing_observations", ["correlation_id"]
    )


def downgrade() -> None:
    op.drop_table("processing_observations")
    op.drop_index("ix_telemetry_events_correlation_id", table_name="telemetry_events")
    op.drop_column("telemetry_events", "correlation_id")
