"""The whole product workflow, driven only through HTTP."""

from __future__ import annotations

from sqlalchemy import func, select

from app.agents.cover_letter import CoverLetterResult
from app.agents.job_analysis import JobAnalysisResult
from app.agents.job_matching import MatchResult
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.document import Document
from app.models.enums import DocumentKind, RunStatus
from app.models.job import Job
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from tests.api.support import (
    ANALYSIS_PAYLOAD,
    ANALYSIS_RESULT,
    LETTER,
    MATCH,
    build_candidate,
    login,
)
from tests.fake_clients import (
    FakeFirecrawlClient,
    FakeSearXNGClient,
    SchemaScriptedGeminiClient,
    listing_page,
    search_result,
)

JOB_URL = "https://boards.greenhouse.io/acme/jobs/1234567"

RESUME = (
    b"Ada Lovelace\nEXPERIENCE: five years building agent pipelines in python."
)

DISCOVERY_REQUEST = {
    "role": "AI Engineer",
    "location": "Bangalore",
    "experience": "1-3 years",
}


def _pipeline_clients():
    searxng = FakeSearXNGClient([search_result(JOB_URL, "AI Engineer")])
    firecrawl = FakeFirecrawlClient(
        {
            JOB_URL: listing_page(
                JOB_URL,
                title="Senior AI Engineer",
                description=(
                    "Design, build, and maintain production AI agent systems. "
                    "Investigate reliability issues, collaborate with product "
                    "and engineering teams, and improve deployed services. "
                    "Qualifications include strong Python skills, experience "
                    "shipping machine-learning systems, and clear communication."
                ),
                location="Bangalore",
                company="Acme Inc.",
                employment_type="Full-time",
                experience="1-3 years",
            )
        }
    )
    gemini = SchemaScriptedGeminiClient(
        {
            JobAnalysisResult: ANALYSIS_RESULT,
            MatchResult: MATCH,
            CoverLetterResult: LETTER,
        }
    )
    return searxng, firecrawl, gemini


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_full_workflow_from_profile_to_application(client_factory, session):
    searxng, firecrawl, gemini = _pipeline_clients()
    client = client_factory(searxng=searxng, firecrawl=firecrawl, gemini=gemini)

    candidate = build_candidate(session, name="Ada Lovelace")
    login(client, candidate.user)

    resume = client.post(
        "/api/resumes/master",
        files={"file": ("resume.txt", RESUME, "text/plain")},
    )
    assert resume.status_code == 201
    resume_id = resume.json()["document_id"]

    discovery = client.post("/api/jobs/discover", json=DISCOVERY_REQUEST)
    assert discovery.status_code == 200
    assert discovery.json()["status"] == "succeeded"
    job_id = discovery.json()["output"]["job_ids"][0]

    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["title"] == "Senior AI Engineer"
    assert job.json()["location"] == "Bangalore"

    analysis = client.post(f"/api/jobs/{job_id}/analyze")
    assert analysis.status_code == 200
    assert analysis.json()["status"] == "succeeded"
    assert analysis.json()["output"]["analysis"] == ANALYSIS_PAYLOAD

    match = client.post(f"/api/jobs/{job_id}/match")
    assert match.status_code == 200
    assert match.json()["output"]["match"]["score"] == 72.5
    assert match.json()["output"]["resume_document_id"] == resume_id
    assert _count(session, Match) == 1

    assert _count(session, Application) == 0

    letter = client.post(f"/api/jobs/{job_id}/cover-letter")
    assert letter.status_code == 200
    letter_output = letter.json()["output"]
    assert letter_output["cover_letter"] == {
        "content": LETTER.content,
        "confidence": LETTER.confidence,
    }
    assert letter_output["master_resume_document_id"] == resume_id

    application = client.post(
        "/api/applications",
        json={"job_id": job_id, "status": "applied", "notes": "via API"},
    )
    assert application.status_code == 201
    assert application.json()["job_title_snapshot"] == "Senior AI Engineer"
    assert application.json()["company_name_snapshot"] == "Acme Inc."

    assert _count(session, Candidate) == 1
    assert _count(session, Job) == 1
    assert _count(session, JobAnalysis) == 1
    assert _count(session, Match) == 1
    assert _count(session, Application) == 1
    assert _count(session, AgentRun) == 4
    assert _count(session, Document) == 2

    kinds = {
        document.kind
        for document in session.execute(select(Document)).scalars().all()
    }
    assert kinds == {DocumentKind.master_resume, DocumentKind.cover_letter}

    runs = session.execute(select(AgentRun)).scalars().all()
    assert {run.status for run in runs} == {RunStatus.succeeded}
    assert [call["schema"] for call in gemini.calls] == [
        JobAnalysisResult,
        MatchResult,
        CoverLetterResult,
    ]
    assert searxng.queries == [
        "AI Engineer Bangalore jobs 1-3 years (site:boards.greenhouse.io OR "
        "site:jobs.ashbyhq.com OR site:jobs.lever.co OR "
        "site:jobs.smartrecruiters.com)",
        "Artificial Intelligence Engineer Bangalore jobs 1-3 years "
        "(site:boards.greenhouse.io OR site:jobs.ashbyhq.com OR "
        "site:jobs.lever.co OR site:jobs.smartrecruiters.com)",
    ]


def test_rerunning_the_pipeline_changes_nothing(client_factory, session):
    searxng, firecrawl, gemini = _pipeline_clients()
    client = client_factory(searxng=searxng, firecrawl=firecrawl, gemini=gemini)

    candidate = build_candidate(session, name="Ada Lovelace")
    login(client, candidate.user)
    client.post(
        "/api/resumes/master",
        files={"file": ("resume.txt", RESUME, "text/plain")},
    )
    job_id = client.post(
        "/api/jobs/discover", json=DISCOVERY_REQUEST
    ).json()["output"]["job_ids"][0]

    first = {
        "discover": client.post("/api/jobs/discover", json=DISCOVERY_REQUEST).json(),
        "analyze": client.post(f"/api/jobs/{job_id}/analyze").json(),
        "match": client.post(f"/api/jobs/{job_id}/match").json(),
        "letter": client.post(f"/api/jobs/{job_id}/cover-letter").json(),
    }

    second = {
        "discover": client.post("/api/jobs/discover", json=DISCOVERY_REQUEST).json(),
        "analyze": client.post(f"/api/jobs/{job_id}/analyze").json(),
        "match": client.post(f"/api/jobs/{job_id}/match").json(),
        "letter": client.post(f"/api/jobs/{job_id}/cover-letter").json(),
    }

    assert {name: run["id"] for name, run in second.items()} == {
        name: run["id"] for name, run in first.items()
    }
    assert second["discover"]["output"] == first["discover"]["output"]
    assert second["analyze"]["output"] == first["analyze"]["output"]
    assert second["match"]["output"] == first["match"]["output"]
    assert second["letter"]["output"] == first["letter"]["output"]

    assert _count(session, AgentRun) == 4
    assert _count(session, Job) == 1
    assert _count(session, JobAnalysis) == 1
    assert _count(session, Match) == 1
    assert _count(session, Document) == 2
    assert len(gemini.calls) == 3
    assert len(searxng.queries) == 2
