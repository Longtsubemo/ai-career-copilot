"""Legacy-owner bootstrap safeguards.

The command must never move existing private data without an explicit,
confirmed intent, and must refuse rather than guess when ownership is
ambiguous. These tests exercise the pure function, not the interactive CLI.

Revision 0004 makes ``candidates.user_id`` NOT NULL, so an ownerless legacy
candidate can no longer exist in a migrated database: the reuse path is
unreachable by construction and is covered by the migration test instead.
What remains testable here is that the command still creates an owner safely,
refuses without confirmation, and never steals a candidate from another
account.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli.bootstrap_legacy_owner import (
    BootstrapError,
    bootstrap_legacy_owner,
    inspect_bootstrap,
)
from app.models.candidate import Candidate
from app.models.user import User
from app.services import auth


def test_inspect_reports_no_legacy_candidate(session: Session) -> None:
    plan = inspect_bootstrap(session, "Owner@Example.com")

    assert plan.email == "owner@example.com"
    assert plan.user_exists is False
    assert plan.legacy_count == 0
    assert plan.legacy_candidate_ids == ()


def test_bootstrap_without_confirmation_writes_nothing(session: Session) -> None:
    with pytest.raises(BootstrapError):
        bootstrap_legacy_owner(
            session, email="owner@example.com", password="password123", confirm=False
        )

    assert session.execute(select(User)).scalars().all() == []


def test_bootstrap_requires_a_password_to_create_the_owner(session: Session) -> None:
    with pytest.raises(BootstrapError):
        bootstrap_legacy_owner(session, email="owner@example.com", confirm=True)


def test_bootstrap_creates_a_new_owner_and_candidate(session: Session) -> None:
    result = bootstrap_legacy_owner(
        session, email="owner@example.com", password="password123", confirm=True
    )

    assert result.created_user is True
    assert result.reused_legacy_candidate is False
    user = session.get(User, result.user_id)
    assert user is not None
    assert user.candidate is not None
    assert user.candidate.id == result.candidate_id
    assert user.candidate.user_id == result.user_id


def test_bootstrap_refuses_when_account_already_owns_a_candidate(
    session: Session,
) -> None:
    user, *_ = auth.register_user(
        session, email="owner@example.com", password="password123"
    )

    with pytest.raises(BootstrapError):
        bootstrap_legacy_owner(
            session, email="owner@example.com", password="password123", confirm=True
        )

    assert user.candidate is not None
    assert session.get(Candidate, user.candidate.id) is not None
