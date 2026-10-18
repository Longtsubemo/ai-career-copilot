"""add durable discovery daily usage counters

Revision ID: 20261016_0008
Revises: 20261012_0007
Create Date: 2026-10-16 00:00:00.000000

The table keeps one UTC-day counter for each authenticated user and one global
counter. Composite primary-key upserts make quota admission atomic across
concurrent application instances. Expired days are pruned during reservation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261016_0008"
down_revision: Union[str, Sequence[str], None] = "20261012_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discovery_daily_usage",
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_discovery_daily_usage_attempts_nonnegative",
        ),
        sa.PrimaryKeyConstraint(
            "usage_date", "scope_key", name="pk_discovery_daily_usage"
        ),
    )


def downgrade() -> None:
    op.drop_table("discovery_daily_usage")
