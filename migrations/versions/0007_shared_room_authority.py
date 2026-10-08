"""Permanent shared-room authority, independent of simulation lifetime."""

import sqlalchemy as sa
from alembic import op

revision = "0007_shared_room_authority"
down_revision = "0006_thermostat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "thermal_authority",
        sa.Column("resource", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clock_passed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("revision", sa.String(128), nullable=True),
    )
    op.bulk_insert(table, [{"resource": "shared-room", "generation": 0}])


def downgrade() -> None:
    op.drop_table("thermal_authority")
