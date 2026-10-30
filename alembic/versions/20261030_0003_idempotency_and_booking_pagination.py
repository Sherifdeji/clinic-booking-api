"""add idempotency records and booking history index

Revision ID: 20261030_0003
Revises: 20261030_0002
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa

revision = "20261030_0003"
down_revision = "20261030_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_bookings_email_created_at_id",
        "bookings",
        ["email", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_bookings_email_created_at_id", table_name="bookings")
    op.drop_table("idempotency_records")
