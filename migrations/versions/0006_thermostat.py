"""Persistent thermostat settings and exact evidence references."""

import sqlalchemy as sa
from alembic import op

revision = "0006_thermostat"
down_revision = "0005_cooling_and_stop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, typ, default in (
        ("policy", sa.String(16), "scenario"),
        ("target_c", sa.Float(), "22"),
        ("settings_revision", sa.Integer(), "0"),
        ("phase", sa.String(32), "idle"),
        ("observation_requested", sa.Boolean(), sa.false()),
    ):
        op.add_column(
            "thermal_simulations", sa.Column(name, typ, nullable=False, server_default=default)
        )
    op.add_column("thermal_simulations", sa.Column("settled_revision", sa.Integer(), nullable=True))
    with op.batch_alter_table("thermal_simulations") as batch:
        batch.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    for table in ("thermal_readings", "thermal_commands"):
        op.add_column(
            table, sa.Column("settings_revision", sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column(
        "thermal_readings",
        sa.Column("cause", sa.String(16), nullable=False, server_default="periodic"),
    )
    op.create_table(
        "thermal_settings",
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column("simulation_id", sa.String(36), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("target_c", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
    )
    op.create_index("ix_thermal_settings_simulation_id", "thermal_settings", ["simulation_id"])


def downgrade() -> None:
    raise RuntimeError("Thermostat history cannot be safely projected into expiring scenarios")
