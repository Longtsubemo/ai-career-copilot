"""add job availability lifecycle

Revision ID: 20261012_0006
Revises: 20261012_0005
Create Date: 2026-10-12 06:00:00.000000

Adds ``jobs.availability`` and ``jobs.closed_at``.

Availability is what discovery established about the vacancy, kept separate
from the technical ``crawl_status``: ``unknown`` (never verified),
``available`` (a successful individual-page crawl confirmed it open),
``closed`` (affirmative closure evidence, e.g. HTTP 410 or an explicit
"no longer accepting applications" page). ``stale`` exists in the vocabulary
but is derived at read time from ``crawled_at`` (the last verification), never
persisted.

The upgrade is non-destructive: every existing row keeps its data and is
assigned ``unknown`` via the column default. Nothing is backfilled from
``crawled_at`` — a past crawl is not evidence that the vacancy is still open
today. ``closed_at`` starts null for every row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20261012_0006'
down_revision: Union[str, Sequence[str], None] = '20261012_0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'jobs',
        sa.Column(
            'availability',
            sa.Enum(
                'unknown',
                'available',
                'stale',
                'closed',
                name='availabilitystatus',
                native_enum=False,
                length=50,
            ),
            server_default='unknown',
            nullable=False,
        ),
    )
    op.add_column(
        'jobs', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        op.f('ix_jobs_availability'), 'jobs', ['availability'], unique=False
    )
    op.create_check_constraint(
        op.f('ck_jobs_availability_vocabulary'),
        'jobs',
        "availability IN ('unknown', 'available', 'stale', 'closed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('ck_jobs_availability_vocabulary'), 'jobs', type_='check'
    )
    op.drop_index(op.f('ix_jobs_availability'), table_name='jobs')
    op.drop_column('jobs', 'closed_at')
    op.drop_column('jobs', 'availability')
