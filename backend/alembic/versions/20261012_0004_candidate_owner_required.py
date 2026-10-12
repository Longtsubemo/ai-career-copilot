"""require a candidate owner

Revision ID: 20261012_0004
Revises: 20261012_0003
Create Date: 2026-10-12 02:10:00.000000

Contracts ``candidates.user_id`` to NOT NULL.

The authentication migration (0003) added the column nullable so a
pre-existing, ownerless candidate could survive until an operator bound it
explicitly with ``app.cli.bootstrap_legacy_owner``. Every remaining legacy row
must therefore already be owned before this revision runs: the upgrade counts
ownerless candidates and aborts with an actionable error instead of guessing
ownership or assigning rows automatically. The unique index created in 0003 is
unchanged; only nullability moves.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20261012_0004'
down_revision: Union[str, Sequence[str], None] = '20261012_0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    unassigned = bind.execute(
        sa.text("SELECT count(*) FROM candidates WHERE user_id IS NULL")
    ).scalar_one()
    if unassigned:
        raise RuntimeError(
            f"{unassigned} candidate profile(s) have no owner; resolve ownership "
            "first with `python -m app.cli.bootstrap_legacy_owner --email <owner>` "
            "before upgrading to 20261012_0004"
        )
    op.alter_column('candidates', 'user_id', existing_type=sa.Uuid(), nullable=False)


def downgrade() -> None:
    op.alter_column('candidates', 'user_id', existing_type=sa.Uuid(), nullable=True)
