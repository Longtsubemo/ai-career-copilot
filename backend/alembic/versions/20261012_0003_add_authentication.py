"""add authentication

Revision ID: 20261012_0003
Revises: 20261003_0002
Create Date: 2026-10-12 01:36:24.990281

Introduces the identity layer: a ``users`` table, server-side ``sessions``
holding only token hashes, and an owner column on ``candidates``.

``candidates.user_id`` is intentionally nullable and unique: existing
single-user rows are left unassigned until an operator runs
``python -m app.cli.bootstrap_legacy_owner`` to bind them explicitly. The
unique index allows any number of NULLs, so no legacy profile is silently
claimed by whoever registers first.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20261012_0003'
down_revision: Union[str, Sequence[str], None] = '20261003_0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('sessions',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('csrf_token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions'))
    )
    op.create_index(op.f('ix_sessions_token_hash'), 'sessions', ['token_hash'], unique=True)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.add_column('candidates', sa.Column('user_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_candidates_user_id'), 'candidates', ['user_id'], unique=True)
    op.create_foreign_key(op.f('fk_candidates_user_id_users'), 'candidates', 'users', ['user_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_candidates_user_id_users'), 'candidates', type_='foreignkey')
    op.drop_index(op.f('ix_candidates_user_id'), table_name='candidates')
    op.drop_column('candidates', 'user_id')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_token_hash'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
