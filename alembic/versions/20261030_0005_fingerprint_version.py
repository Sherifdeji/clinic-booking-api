"""Version request fingerprints without invalidating existing idempotency keys.

Revision ID: 20261030_0005
Revises: 20261030_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20261030_0005"
down_revision = "20261030_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows retain normalized-field semantics (v1). New requests use v2.
    op.add_column(
        "idempotency_records",
        sa.Column("fingerprint_version", sa.Integer(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("idempotency_records", "fingerprint_version")
