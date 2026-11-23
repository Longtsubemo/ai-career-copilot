"""Replace accounts and sessions with a persistent local profile.

Revision ID: 20261115_0009
Revises: 20261016_0008
Create Date: 2026-11-15

Candidate IDs and every candidate-linked application record stay in place.
When an older database has several profiles, none is selected automatically;
the application provides an explicit profile-selection command.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261115_0009"
down_revision: Union[str, Sequence[str], None] = "20261016_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    candidate_count = bind.execute(
        sa.text("SELECT count(*) FROM candidates")
    ).scalar_one()
    account_count = bind.execute(sa.text("SELECT count(*) FROM users")).scalar_one()

    op.add_column(
        "candidates",
        sa.Column(
            "is_local_profile",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    if candidate_count == 1:
        bind.execute(
            sa.text("UPDATE candidates SET is_local_profile = true")
        )
    elif candidate_count > 1:
        # Preserve every profile and its foreign-key relationships. Requiring
        # an explicit choice avoids silently blending separate account data.
        print(
            f"Preserved {candidate_count} candidate profiles from "
            f"{account_count} account(s); select one explicitly before launch."
        )

    # Collapse old per-account provider counters into one local-install count.
    # Adding counts preserves the total amount of previous provider use.
    bind.execute(
        sa.text(
            "INSERT INTO discovery_daily_usage (usage_date, scope_key, attempts) "
            "SELECT usage_date, 'local', sum(attempts)::integer "
            "FROM discovery_daily_usage WHERE scope_key LIKE 'user:%' "
            "GROUP BY usage_date "
            "ON CONFLICT (usage_date, scope_key) DO UPDATE "
            "SET attempts = discovery_daily_usage.attempts + EXCLUDED.attempts"
        )
    )
    bind.execute(
        sa.text("DELETE FROM discovery_daily_usage WHERE scope_key LIKE 'user:%'")
    )

    op.create_index(
        "uq_candidates_single_local_profile",
        "candidates",
        ["is_local_profile"],
        unique=True,
        postgresql_where=sa.text("is_local_profile IS TRUE"),
    )

    op.drop_constraint("fk_candidates_user_id_users", "candidates", type_="foreignkey")
    op.drop_index("ix_candidates_user_id", table_name="candidates")
    op.drop_column("candidates", "user_id")
    op.drop_table("sessions")
    op.drop_table("users")


def downgrade() -> None:
    raise RuntimeError(
        "This migration removes password and session records. Restore the "
        "pre-upgrade database backup to return to an authenticated version."
    )
