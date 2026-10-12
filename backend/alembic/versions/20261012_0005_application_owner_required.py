"""require an application owner

Revision ID: 20261012_0005
Revises: 20261012_0004
Create Date: 2026-10-12 03:20:00.000000

Adds ``applications.candidate_id`` and contracts it to NOT NULL.

Applications were written before this milestone without an owner, so the
upgrade has to decide who owns them. Ownership is only backfilled when it is
unambiguous: with exactly one candidate profile every existing application is
assigned to it. With several profiles the owner cannot be inferred, and with
none there is nobody to assign to — both cases abort with an actionable error
and change nothing, rather than guessing or handing every old row to whichever
account registers first. The column, index and cascade foreign key mirror the
model exactly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20261012_0005'
down_revision: Union[str, Sequence[str], None] = '20261012_0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    application_count = bind.execute(
        sa.text("SELECT count(*) FROM applications")
    ).scalar_one()
    candidate_id = None
    if application_count:
        candidate_count = bind.execute(
            sa.text("SELECT count(*) FROM candidates")
        ).scalar_one()
        if candidate_count == 0:
            raise RuntimeError(
                f"{application_count} application(s) exist but no candidate "
                "profile is available to own them; create the owner profile "
                "before upgrading to 20261012_0005"
            )
        if candidate_count > 1:
            raise RuntimeError(
                f"{application_count} application(s) exist and "
                f"{candidate_count} candidate profiles are available, so "
                "ownership is ambiguous; assign each application to a "
                "candidate before upgrading to 20261012_0005"
            )
        candidate_id = bind.execute(
            sa.text("SELECT id FROM candidates")
        ).scalar_one()

    op.add_column(
        'applications', sa.Column('candidate_id', sa.Uuid(), nullable=True)
    )

    if candidate_id is not None:
        bind.execute(
            sa.text(
                "UPDATE applications SET candidate_id = :candidate_id "
                "WHERE candidate_id IS NULL"
            ),
            {"candidate_id": candidate_id},
        )

    op.create_index(
        op.f('ix_applications_candidate_id'),
        'applications',
        ['candidate_id'],
        unique=False,
    )
    op.create_foreign_key(
        op.f('fk_applications_candidate_id_candidates'),
        'applications',
        'candidates',
        ['candidate_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.alter_column(
        'applications', 'candidate_id', existing_type=sa.Uuid(), nullable=False
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('fk_applications_candidate_id_candidates'),
        'applications',
        type_='foreignkey',
    )
    op.drop_index(
        op.f('ix_applications_candidate_id'), table_name='applications'
    )
    op.drop_column('applications', 'candidate_id')
