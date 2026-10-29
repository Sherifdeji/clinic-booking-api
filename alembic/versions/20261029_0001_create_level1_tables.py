"""create level 1 tables

Revision ID: 20261029_0001
Revises:
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slots",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=200), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_slots_start", "slots", ["start"], unique=False)
    op.create_table(
        "bookings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slot_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["slot_id"], ["slots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bookings_slot_status", "bookings", ["slot_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_bookings_slot_status", table_name="bookings")
    op.drop_table("bookings")
    op.drop_index("ix_slots_start", table_name="slots")
    op.drop_table("slots")
