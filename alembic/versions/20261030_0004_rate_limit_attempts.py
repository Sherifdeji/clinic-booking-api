"""add persistent booking rate limit attempts

Revision ID: 20261030_0004
Revises: 20261030_0003
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa

revision = "20261030_0004"
down_revision = "20261030_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rate_limit_attempts_email_attempted_at",
        "rate_limit_attempts",
        ["email", "attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rate_limit_attempts_email_attempted_at",
        table_name="rate_limit_attempts",
    )
    op.drop_table("rate_limit_attempts")
