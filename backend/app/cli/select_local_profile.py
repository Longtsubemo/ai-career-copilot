"""Explicitly select one preserved profile after a multi-profile upgrade."""

from __future__ import annotations

import argparse
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.document import Document
from app.models.match import Match
from app.models.agent_run import AgentRun
from app.services.candidates import select_local_candidate


def _list_profiles(session: Session) -> None:
    candidates = session.execute(
        select(Candidate).order_by(Candidate.created_at, Candidate.id)
    ).scalars()
    for candidate in candidates:
        counts = []
        for label, model in (
            ("resumes/documents", Document),
            ("applications", Application),
            ("matches", Match),
            ("agent runs", AgentRun),
        ):
            count = session.scalar(
                select(func.count()).select_from(model).where(
                    model.candidate_id == candidate.id
                )
            ) or 0
            counts.append(f"{label}={count}")
        marker = " [currently selected]" if candidate.is_local_profile else ""
        print(
            f"{candidate.id}  {candidate.name or '(name not set)'}{marker}  "
            f"({', '.join(counts)})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List preserved candidate profiles or select one for this local "
            "installation. Selection does not merge or delete profile data."
        )
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--list", action="store_true", help="list saved profiles and data counts")
    selection.add_argument("--candidate-id", type=UUID, help="select this existing profile")
    args = parser.parse_args()

    with SessionLocal() as session:
        if args.list:
            _list_profiles(session)
            return 0

        try:
            candidate = select_local_candidate(session, args.candidate_id)
        except LookupError:
            parser.error("no candidate profile has that ID")
        session.commit()
        print(f"Selected local profile {candidate.id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
