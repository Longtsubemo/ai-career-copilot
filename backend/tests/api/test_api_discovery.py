"""Behaviour of the job discovery endpoint."""

from __future__ import annotations

from sqlalchemy import func, select

from app.clients.searxng import SearXNGError
from app.models.agent_run import AgentRun
from app.models.job import Job
from tests.fake_clients import (
    FakeFirecrawlClient,
    FakeSearXNGClient,
    listing_page,
    search_result,
)

JOB_URL = "https://example.com/jobs/ai-engineer-bangalore"

REQUEST = {"role": "AI Engineer", "location": "Bangalore", "experience": "1-3 years"}


def _fakes() -> tuple[FakeSearXNGClient, FakeFirecrawlClient]:
    searxng = FakeSearXNGClient([search_result(JOB_URL, "AI Engineer")])
    firecrawl = FakeFirecrawlClient(
        {
            JOB_URL: listing_page(
                JOB_URL,
                title="Senior AI Engineer",
                description="Own the agent platform.",
                location="Bangalore",
                company="Acme Inc.",
                employment_type="Full-time",
                experience="1-3 years",
            )
        }
    )
    return searxng, firecrawl


def test_discover_persists_a_job(client_factory, session):
    searxng, firecrawl = _fakes()
    client = client_factory(searxng=searxng, firecrawl=firecrawl)

    response = client.post("/api/jobs/discover", json=REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["agent_name"] == "job_discovery"
    assert body["status"] == "succeeded"
    assert body["error"] is None
    output = body["output"]
    assert output["jobs_created"] == 1
    assert output["jobs_updated"] == 0
    assert output["jobs_skipped"] == 0
    assert len(output["job_ids"]) == 1
    assert searxng.queries == ["AI Engineer Bangalore jobs 1-3 years"]

    listed = client.get("/api/jobs")
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["title"] == "Senior AI Engineer"

    job = session.execute(select(Job)).scalars().one()
    assert job.company.name == "Acme Inc."
    assert job.crawl_status.value == "scraped"
    assert output["job_ids"] == [str(job.id)]
    assert job.canonical_url == JOB_URL


def test_repeated_discovery_reuses_the_run_and_upserts(client_factory, session):
    searxng, firecrawl = _fakes()
    client = client_factory(searxng=searxng, firecrawl=firecrawl)

    first = client.post("/api/jobs/discover", json=REQUEST).json()
    second = client.post("/api/jobs/discover", json=REQUEST).json()

    assert second["id"] == first["id"]
    assert second["status"] == "succeeded"
    assert second["output"] == first["output"]
    assert len(searxng.queries) == 1
    assert session.scalar(select(func.count()).select_from(Job)) == 1
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_discovery_input_is_validated_before_any_run(client_factory, session):
    searxng, firecrawl = _fakes()
    client = client_factory(searxng=searxng, firecrawl=firecrawl)

    missing = client.post(
        "/api/jobs/discover", json={"role": "AI Engineer", "location": "Bangalore"}
    )
    blank = client.post(
        "/api/jobs/discover",
        json={"role": "   ", "location": "Bangalore", "experience": "1-3 years"},
    )
    extra = client.post("/api/jobs/discover", json={**REQUEST, "salary": "any"})

    assert missing.status_code == 422
    assert blank.status_code == 422
    assert extra.status_code == 422
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert searxng.queries == []


def test_search_failure_becomes_a_503_with_the_run(client_factory, session):
    client = client_factory(
        searxng=FakeSearXNGClient(error=SearXNGError("search backend is down")),
        firecrawl=FakeFirecrawlClient(),
    )

    response = client.post("/api/jobs/discover", json=REQUEST)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["message"] == "SearXNGError: search backend is down"
    run = detail["run"]
    assert run["agent_name"] == "job_discovery"
    assert run["status"] == "pending"
    assert run["attempt_count"] == 1
    assert "Traceback" not in detail["message"]
    assert session.scalar(select(func.count()).select_from(Job)) == 0
