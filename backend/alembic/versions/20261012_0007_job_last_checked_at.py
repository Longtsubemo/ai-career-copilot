"""add job last checked timestamp

Revision ID: 20261012_0007
Revises: 20261012_0006
Create Date: 2026-10-12 06:30:00.000000

Adds ``jobs.last_checked_at``: when the source URL was last re-verified,
including checks that failed. It is deliberately separate from
``crawled_at`` (the last *successful* verification, which stays untouched
after a failed check) and exists so a per-job cooldown and a "last checked"
timestamp survive failures and process restarts.

The upgrade is non-destructive: the column is nullable and starts null for
every existing row. Nothing is backfilled from ``crawled_at`` — an old crawl
is not evidence of a recent check.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20261012_0007'
down_revision: Union[str, Sequence[str], None] = '20261012_0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'jobs',
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('jobs', 'last_checked_at')