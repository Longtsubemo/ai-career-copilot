"""Behaviour of the job discovery endpoint."""

from __future__ import annotations

from sqlalchemy import func, select

from app.clients.searxng import SearXNGError
from app.models.agent_run import AgentRun
from app.models.job import Job
from app.models.discovery_daily_usage import DiscoveryDailyUsage
from app.core.config import Settings
from tests.api.support import login
from tests.factories import make_user
from tests.fake_clients import (
    DEFAULT_PAGE_DESCRIPTION,
    FakeFirecrawlClient,
    FakeSearXNGClient,
    listing_page,
    search_result,
)

JOB_URL = "https://boards.greenhouse.io/acme/jobs/127817"
SITE_HINT = (
    "(site:boards.greenhouse.io OR site:jobs.ashbyhq.com OR "
    "site:jobs.lever.co OR site:jobs.smartrecruiters.com)"
)

REQUEST = {"role": "AI Engineer", "location": "Bangalore", "experience": "1-3 years"}


def _fakes() -> tuple[FakeSearXNGClient, FakeFirecrawlClient]:
    searxng = FakeSearXNGClient([search_result(JOB_URL, "AI Engineer")])
    firecrawl = FakeFirecrawlClient(
        {
            JOB_URL: listing_page(
                JOB_URL,
                title="Senior AI Engineer",
                description=DEFAULT_PAGE_DESCRIPTION,
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
    login(client, make_user(session))

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
    assert searxng.queries == [
        f"AI Engineer Bangalore jobs 1-3 years {SITE_HINT}",
        f"Artificial Intelligence Engineer Bangalore jobs 1-3 years {SITE_HINT}",
    ]

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
    login(client, make_user(session))

    first = client.post("/api/jobs/discover", json=REQUEST).json()
    second = client.post("/api/jobs/discover", json=REQUEST).json()

    assert second["id"] == first["id"]
    assert second["status"] == "succeeded"
    assert second["output"] == first["output"]
    assert len(searxng.queries) == 2
    assert session.scalar(select(func.count()).select_from(Job)) == 1
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_discovery_input_is_validated_before_any_run(client_factory, session):
    searxng, firecrawl = _fakes()
    client = client_factory(searxng=searxng, firecrawl=firecrawl)
    login(client, make_user(session))

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
    login(client, make_user(session))

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


def test_discovery_requires_authentication_before_provider_or_run(
    client_factory, session
):
    searxng, firecrawl = _fakes()
    client = client_factory(searxng=searxng, firecrawl=firecrawl)

    response = client.post("/api/jobs/discover", json=REQUEST)

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}
    assert searxng.queries == []
    assert firecrawl.requested == []
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert session.scalar(select(func.count()).select_from(DiscoveryDailyUsage)) == 0


def test_daily_user_limit_rejects_before_search(client_factory, session, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.agents.get_settings",
        lambda: Settings(_env_file=None, discovery_daily_user_limit=1),
    )
    searxng, firecrawl = _fakes()
    client = client_factory(searxng=searxng, firecrawl=firecrawl)
    login(client, make_user(session))

    first = client.post("/api/jobs/discover", json=REQUEST)
    second = client.post("/api/jobs/discover", json=REQUEST)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "daily discovery limit reached"}
    assert len(searxng.queries) == 2
    assert len(firecrawl.requested) == 1
    assert session.scalar(select(func.sum(DiscoveryDailyUsage.attempts))) == 2


def test_failed_search_still_consumes_daily_quota(client_factory, session, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.agents.get_settings",
        lambda: Settings(_env_file=None, discovery_daily_user_limit=1),
    )
    search = FakeSearXNGClient(error=SearXNGError("search backend is down"))
    firecrawl = FakeFirecrawlClient()
    client = client_factory(searxng=search, firecrawl=firecrawl)
    login(client, make_user(session))

    failed = client.post("/api/jobs/discover", json=REQUEST)
    limited = client.post("/api/jobs/discover", json=REQUEST)

    assert failed.status_code == 503
    assert limited.status_code == 429
    assert len(search.queries) == 1
    assert firecrawl.requested == []
    assert session.scalar(select(func.sum(DiscoveryDailyUsage.attempts))) == 2


def test_global_daily_limit_is_shared_between_users(client_factory, session, monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.agents.get_settings",
        lambda: Settings(
            _env_file=None,
            discovery_daily_user_limit=10,
            discovery_daily_global_limit=1,
        ),
    )
    first_search, first_crawl = _fakes()
    first_client = client_factory(searxng=first_search, firecrawl=first_crawl)
    login(first_client, make_user(session))
    second_search, second_crawl = _fakes()
    second_client = client_factory(searxng=second_search, firecrawl=second_crawl)
    login(second_client, make_user(session))

    assert first_client.post("/api/jobs/discover", json=REQUEST).status_code == 200
    limited = second_client.post("/api/jobs/discover", json=REQUEST)

    assert limited.status_code == 429
    assert len(first_search.queries) == 2
    assert second_search.queries == []
    assert len(first_crawl.requested) == 1
    assert second_crawl.requested == []
    assert session.scalar(select(func.sum(DiscoveryDailyUsage.attempts))) == 2
