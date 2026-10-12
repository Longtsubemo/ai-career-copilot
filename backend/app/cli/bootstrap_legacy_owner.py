"""One-time, explicit assignment of legacy single-user data to its owner.

The authentication migration leaves the pre-existing candidate (and therefore
its resumes, matches, cover letters and runs) ownerless. This command lets an
operator bind that data to a named account by hand. It never runs on startup
and never assigns anything by registration order: it refuses when the intended
owner or the legacy row is ambiguous, and it writes only after confirmation.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.candidate import Candidate
from app.models.user import User
from app.services import auth as auth_service


class BootstrapError(RuntimeError):
    """The legacy ownership cannot be established safely."""


@dataclass(frozen=True)
class BootstrapPlan:
    """A read-only description of what a bootstrap run would do."""

    email: str
    user_exists: bool
    user_id: UUID | None
    user_has_candidate: bool
    legacy_candidate_ids: tuple[UUID, ...]

    @property
    def legacy_count(self) -> int:
        return len(self.legacy_candidate_ids)


@dataclass(frozen=True)
class BootstrapResult:
    """The outcome of a successful, confirmed bootstrap."""

    user_id: UUID
    candidate_id: UUID
    created_user: bool
    reused_legacy_candidate: bool


def _unassigned_candidates(session: Session) -> list[Candidate]:
    stmt = (
        select(Candidate)
        .where(Candidate.user_id.is_(None))
        .order_by(Candidate.created_at, Candidate.id)
    )
    return list(session.execute(stmt).scalars().all())


def inspect_bootstrap(session: Session, email: str) -> BootstrapPlan:
    """Describe the bootstrap that ``email`` would perform, writing nothing."""
    normalized = auth_service.normalize_email(email)
    user = auth_service.find_user_by_email(session, normalized)
    legacy = _unassigned_candidates(session)
    return BootstrapPlan(
        email=normalized,
        user_exists=user is not None,
        user_id=user.id if user is not None else None,
        user_has_candidate=user is not None and user.candidate is not None,
        legacy_candidate_ids=tuple(candidate.id for candidate in legacy),
    )


def bootstrap_legacy_owner(
    session: Session,
    *,
    email: str,
    password: str | None = None,
    name: str | None = None,
    confirm: bool = False,
) -> BootstrapResult:
    """Assign legacy data to the named owner, or refuse.

    ``confirm`` must be true for any write; without it the call raises so an
    accidental invocation cannot move data. A missing account is created only
    when a password is supplied. The command reuses the single ownerless
    legacy candidate when there is exactly one and refuses to guess when there
    are several.
    """
    plan = inspect_bootstrap(session, email)

    if plan.user_has_candidate:
        raise BootstrapError(
            "the named account already owns a candidate profile; nothing to bootstrap"
        )
    if plan.legacy_count > 1:
        raise BootstrapError(
            f"{plan.legacy_count} unassigned candidate profiles exist; "
            "refusing to guess which one the operator intends"
        )

    if not plan.user_exists:
        if not password:
            raise BootstrapError("a password is required to create the owner account")
        if not confirm:
            raise BootstrapError(
                "confirmation required; re-run with --yes to create the account "
                "and assign legacy data"
            )
        user = User(
            email=plan.email, password_hash=auth_service.hash_password(password)
        )
        session.add(user)
        session.flush()
        created_user = True
    else:
        user = session.get(User, plan.user_id)
        assert user is not None
        created_user = False

    if not confirm:
        raise BootstrapError(
            "confirmation required; re-run with --yes to assign legacy data"
        )

    if plan.legacy_count == 1:
        candidate = session.get(Candidate, plan.legacy_candidate_ids[0])
        assert candidate is not None
        candidate.user_id = user.id
        session.flush()
        return BootstrapResult(
            user_id=user.id,
            candidate_id=candidate.id,
            created_user=created_user,
            reused_legacy_candidate=True,
        )

    candidate_name = (
        name.strip() if name and name.strip() else plan.email.split("@", 1)[0]
    )
    candidate = Candidate(name=candidate_name, user_id=user.id)
    session.add(candidate)
    session.flush()
    return BootstrapResult(
        user_id=user.id,
        candidate_id=candidate.id,
        created_user=created_user,
        reused_legacy_candidate=False,
    )


def _describe(plan: BootstrapPlan) -> str:
    lines = [f"intended owner: {plan.email}"]
    if plan.user_exists:
        lines.append(f"account: existing ({plan.user_id})")
    else:
        lines.append("account: will be created")
    if plan.legacy_count == 1:
        lines.append(f"legacy candidate: {plan.legacy_candidate_ids[0]} (will be reused)")
    elif plan.legacy_count == 0:
        lines.append("legacy candidate: none found (a new profile will be created)")
    else:
        lines.append(f"legacy candidates: {plan.legacy_count} found (ambiguous)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, confirm with the operator and run the bootstrap."""
    parser = argparse.ArgumentParser(
        description="Assign legacy single-user data to a named owner account."
    )
    parser.add_argument("--email", required=True, help="email of the legacy owner")
    parser.add_argument("--name", default=None, help="candidate display name")
    parser.add_argument(
        "--password",
        default=None,
        help="password for a new owner account (prefer interactive prompt)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the confirmation prompt (for scripted, reviewed runs)",
    )
    args = parser.parse_args(argv)

    session = SessionLocal()
    try:
        plan = inspect_bootstrap(session, args.email)
        print(_describe(plan))
        if plan.legacy_count > 1 or plan.user_has_candidate:
            print("refusing to proceed automatically", file=sys.stderr)
            return 1

        password = args.password
        if not plan.user_exists and password is None:
            password = getpass.getpass("password for the new owner account: ")

        confirmed = args.yes
        if not confirmed:
            answer = input("proceed with this assignment? [y/N] ").strip().lower()
            confirmed = answer in {"y", "yes"}

        result = bootstrap_legacy_owner(
            session,
            email=args.email,
            password=password,
            name=args.name,
            confirm=confirmed,
        )
        session.commit()
        print(f"owner user: {result.user_id}")
        print(f"candidate:  {result.candidate_id}")
        print(f"reused legacy candidate: {result.reused_legacy_candidate}")
        return 0
    except BootstrapError as error:
        session.rollback()
        print(f"bootstrap refused: {error}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BootstrapError",
    "BootstrapPlan",
    "BootstrapResult",
    "bootstrap_legacy_owner",
    "inspect_bootstrap",
    "main",
]
