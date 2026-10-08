"""Add symmetric cooling and durable requested-stop evidence without rewriting history."""

import sqlalchemy as sa
from alembic import op

revision = "0005_cooling_and_stop"
down_revision = "0004_typed_thermal_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "thermal_simulations",
        sa.Column("mode", sa.String(16), nullable=False, server_default="heating"),
    )
    for table in ("thermal_simulations", "thermal_readings", "thermal_commands"):
        op.add_column(
            table, sa.Column("cooler_on", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    for name, typ in (
        ("predecessor_simulation_id", sa.String(36)),
        ("stop_request_id", sa.String(36)),
        ("stop_requested_at", sa.DateTime(timezone=True)),
        ("stop_command_sequence", sa.Integer()),
    ):
        op.add_column("thermal_simulations", sa.Column(name, typ, nullable=True))
    op.add_column(
        "thermal_simulations",
        sa.Column("stop_recovery_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "thermal_commands",
        sa.Column("origin", sa.String(16), nullable=False, server_default="automatic"),
    )
    op.add_column("thermal_commands", sa.Column("stop_request_id", sa.String(36), nullable=True))
    op.add_column(
        "thermal_commands", sa.Column("final_reading_sequence", sa.Integer(), nullable=True)
    )
    with op.batch_alter_table("thermal_commands") as batch:
        batch.alter_column("decision_id", existing_type=sa.String(64), nullable=True)
        batch.alter_column("reading_sequence", existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table("thermal_simulations") as batch:
        batch.create_check_constraint(
            "ck_thermal_exclusive_actuators", "NOT (heater_on AND cooler_on)"
        )
        batch.create_check_constraint("ck_thermal_mode", "mode IN ('heating','cooling')")


def downgrade() -> None:
    raise RuntimeError("Cooling evidence cannot be safely projected into the heating-only schema")
