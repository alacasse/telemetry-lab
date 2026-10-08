"""Make thermal command intentions explicit while preserving legacy delivery eligibility."""

import sqlalchemy as sa
from alembic import op

revision = "0004_typed_thermal_commands"
down_revision = "0003_thermal_simulations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("thermal_commands", sa.Column("command_type", sa.String(32), nullable=True))
    op.add_column(
        "thermal_commands",
        sa.Column("legacy_wire_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        sa.text(
            "UPDATE thermal_commands SET command_type = CASE WHEN heater_on "
            "THEN 'heating.start' ELSE 'heating.stop' END, legacy_wire_allowed = true"
        )
    )
    with op.batch_alter_table("thermal_commands") as batch:
        batch.alter_column("command_type", existing_type=sa.String(32), nullable=False)
        batch.create_check_constraint(
            "ck_thermal_command_type",
            "command_type IN ('heating.start','heating.stop','cooling.start','cooling.stop')",
        )
        batch.create_check_constraint(
            "ck_thermal_heating_projection",
            "(command_type != 'heating.start' OR heater_on = true) AND "
            "(command_type != 'heating.stop' OR heater_on = false)",
        )


def downgrade() -> None:
    with op.batch_alter_table("thermal_commands") as batch:
        batch.drop_constraint("ck_thermal_heating_projection", type_="check")
        batch.drop_constraint("ck_thermal_command_type", type_="check")
        batch.drop_column("legacy_wire_allowed")
        batch.drop_column("command_type")
