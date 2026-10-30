"""enforce one confirmed booking per slot

Revision ID: 20261030_0002
Revises: 20261029_0001
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa

revision = "20261030_0002"
down_revision = "20261029_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_bookings_one_confirmed_per_slot",
        "bookings",
        ["slot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_bookings_one_confirmed_per_slot",
        table_name="bookings",
    )
