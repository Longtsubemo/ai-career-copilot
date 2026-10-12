"""Behaviour of the read-only job endpoints and job re-verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.clients.firecrawl import FirecrawlError
from app.models.enums import AvailabilityStatus
from tests.api.support import login, make_user_client
from tests.factories import make_job, make_company, make_user
from tests.fake_clients import FakeFirecrawlClient, crawl_result


def _seed_jobs(session):
    company = make_company(session, name="Acme Inc.")
    return [
        make_job(
            session,
            company,
            title="AI Engineer",
            location="Bangalore",
            experience_level="1-3 years",
            source="searxng",
        ),
        make_job(
            session,
            company,
            title="Data Engineer",
            location="Pune",
            experience_level="5+ years",
            source="searxng",
        ),
        make_job(
            session,
            company,
            title="Platform Engineer",
            location="100% Remote",
            experience_level="3-5 years",
            source="manual",
            is_active=False,
        ),
        make_job(
            session,
            company,
            title="Backend Engineer",
            location="100 Remote",
            experience_level="1-3 years",
            source="manual",
        ),
    ]


def test_job_list_starts_empty(client):
    response = client.get("/api/jobs")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_returns_every_job_with_a_total(client, session):
    jobs = _seed_jobs(session)

    response = client.get("/api/jobs")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert len(body["items"]) == 4
    assert {row["id"] for row in body["items"]} == {str(job.id) for job in jobs}
    assert body["items"][0]["canonical_url"]


def test_job_rows_expose_the_company_name(client, session):
    jobs = _seed_jobs(session)
    make_job(session, title="Solo Engineer")

    listed = client.get("/api/jobs").json()["items"]
    detail = client.get(f"/api/jobs/{jobs[0].id}").json()

    assert {row["company_name"] for row in listed} == {"Acme Inc.", None}
    assert detail["company_name"] == "Acme Inc."
    assert detail["company_id"] == str(jobs[0].company_id)


def test_location_filter_is_a_case_insensitive_substring(client, session):
    _seed_jobs(session)

    response = client.get("/api/jobs", params={"location": "bangalore"})

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "AI Engineer"


def test_location_filter_escapes_like_wildcards(client, session):
    _seed_jobs(session)

    response = client.get("/api/jobs", params={"location": "100%"})

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["location"] == "100% Remote"


def test_experience_level_and_source_filters(client, session):
    _seed_jobs(session)

    experience = client.get(
        "/api/jobs", params={"experience_level": "1-3 years"}
    ).json()
    assert experience["total"] == 2

    source = client.get("/api/jobs", params={"source": "manual"}).json()
    assert source["total"] == 2

    active = client.get("/api/jobs", params={"is_active": False}).json()
    assert active["total"] == 1
    assert active["items"][0]["title"] == "Platform Engineer"


def test_pagination_is_stable(client, session):
    jobs = _seed_jobs(session)

    first_page = client.get("/api/jobs", params={"limit": 2, "offset": 0}).json()
    second_page = client.get("/api/jobs", params={"limit": 2, "offset": 2}).json()

    assert first_page["total"] == 4
    assert len(first_page["items"]) == 2
    assert len(second_page["items"]) == 2
    assert {row["id"] for row in first_page["items"]}.isdisjoint(
        {row["id"] for row in second_page["items"]}
    )
    assert {row["id"] for row in first_page["items"]} | {
        row["id"] for row in second_page["items"]
    } == {str(job.id) for job in jobs}


def test_pagination_bounds_are_validated(client):
    assert client.get("/api/jobs", params={"limit": 101}).status_code == 422
    assert client.get("/api/jobs", params={"limit": 0}).status_code == 422
    assert client.get("/api/jobs", params={"offset": -1}).status_code == 422


def test_read_one_job(client, session):
    job = make_job(session, company=make_company(session))

    response = client.get(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(job.id)
    assert body["title"] == "AI Engineer"
    assert body["canonical_url"] == job.canonical_url
    assert body["is_active"] is True


def test_read_unknown_job_is_404(client):
    response = client.get(f"/api/jobs/{uuid4()}")

    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


def test_malformed_job_id_is_422(client):
    response = client.get("/api/jobs/not-a-uuid")

    assert response.status_code == 422


def test_job_without_verification_is_unknown(client, session):
    job = make_job(session, company=make_company(session))

    body = client.get(f"/api/jobs/{job.id}").json()

    assert body["availability"] == "unknown"
    assert body["last_verified_at"] is None
    assert body["closed_at"] is None


def test_recent_verified_job_is_available(client, session):
    now = datetime.now(timezone.utc)
    job = make_job(
        session,
        company=make_company(session),
        availability=AvailabilityStatus.available,
        crawled_at=now - timedelta(days=1),
    )

    body = client.get(f"/api/jobs/{job.id}").json()

    assert body["availability"] == "available"
    assert body["last_verified_at"] is not None


def test_old_verified_job_is_stale(client, session):
    now = datetime.now(timezone.utc)
    job = make_job(
        session,
        company=make_company(session),
        availability=AvailabilityStatus.available,
        crawled_at=now - timedelta(days=90),
    )

    body = client.get(f"/api/jobs/{job.id}").json()

    assert body["availability"] == "stale"


def test_closed_job_stays_closed_regardless_of_age(client, session):
    now = datetime.now(timezone.utc)
    job = make_job(
        session,
        company=make_company(session),
        availability=AvailabilityStatus.closed,
        crawled_at=now - timedelta(days=365),
        closed_at=now - timedelta(days=365),
    )

    body = client.get(f"/api/jobs/{job.id}").json()

    assert body["availability"] == "closed"
    assert body["closed_at"] is not None


def _seed_availability(session):
    now = datetime.now(timezone.utc)
    company = make_company(session, name="Acme Inc.")
    return {
        "recent": make_job(
            session,
            company,
            title="Fresh Eng",
            availability=AvailabilityStatus.available,
            crawled_at=now - timedelta(days=1),
        ),
        "old": make_job(
            session,
            company,
            title="Aged Eng",
            availability=AvailabilityStatus.available,
            crawled_at=now - timedelta(days=90),
        ),
        "closed": make_job(
            session,
            company,
            title="Gone Eng",
            availability=AvailabilityStatus.closed,
            crawled_at=now - timedelta(days=90),
            closed_at=now - timedelta(days=90),
        ),
        "unknown": make_job(session, company, title="Mystery Eng"),
    }


def test_availability_filter_isolates_every_effective_state(client, session):
    jobs = _seed_availability(session)

    available = client.get("/api/jobs", params={"availability": "available"}).json()
    stale = client.get("/api/jobs", params={"availability": "stale"}).json()
    closed = client.get("/api/jobs", params={"availability": "closed"}).json()
    unknown = client.get("/api/jobs", params={"availability": "unknown"}).json()

    assert available["total"] == 1
    assert available["items"][0]["id"] == str(jobs["recent"].id)
    assert stale["total"] == 1
    assert stale["items"][0]["id"] == str(jobs["old"].id)
    assert closed["total"] == 1
    assert closed["items"][0]["id"] == str(jobs["closed"].id)
    assert unknown["total"] == 1
    assert unknown["items"][0]["id"] == str(jobs["unknown"].id)


def test_availability_filter_combines_with_other_filters(client, session):
    now = datetime.now(timezone.utc)
    company = make_company(session)
    make_job(session, company, title="AI in Pune", location="Pune",
             availability=AvailabilityStatus.available, crawled_at=now - timedelta(days=1))
    make_job(session, company, title="AI in Pune",
             location="100% Remote", availability=AvailabilityStatus.available,
             crawled_at=now - timedelta(days=1))

    body = client.get(
        "/api/jobs",
        params={"location": "pune", "availability": "available"},
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["location"] == "Pune"


def test_availability_filter_is_applied_before_pagination_and_total(client, session):
    now = datetime.now(timezone.utc)
    company = make_company(session)
    for index in range(3):
        make_job(session, company, title=f"Fresh {index}",
                 availability=AvailabilityStatus.available,
                 crawled_at=now - timedelta(days=1))
    make_job(session, company, title="Old", availability=AvailabilityStatus.available,
             crawled_at=now - timedelta(days=90))
    make_job(session, company, title="Mystery")

    first = client.get(
        "/api/jobs", params={"availability": "available", "limit": 1, "offset": 0}
    ).json()
    second = client.get(
        "/api/jobs", params={"availability": "available", "limit": 2, "offset": 1}
    ).json()

    assert first["total"] == 3
    assert len(first["items"]) == 1
    assert second["total"] == 3
    assert len(second["items"]) == 2
    rows = [first["items"][0], *second["items"]]
    assert {row["availability"] for row in rows} == {"available"}


def test_unfiltered_and_clear_filter_restore_the_full_list(client, session):
    _seed_availability(session)

    all_jobs = client.get("/api/jobs").json()
    assert all_jobs["total"] == 4

    filtered = client.get("/api/jobs", params={"availability": "closed"}).json()
    assert filtered["total"] == 1

    restored = client.get("/api/jobs").json()
    assert restored["total"] == 4


def test_invalid_availability_value_is_422(client, session):
    _seed_availability(session)

    assert client.get("/api/jobs", params={"availability": "hiring"}).status_code == 422


def test_verify_requires_an_authenticated_session(client, session):
    job = make_job(session, company=make_company(session))

    response = client.post(f"/api/jobs/{job.id}/verify")

    assert response.status_code == 401


def test_verify_unknown_job_is_404(client_factory, session):
    client, _ = make_user_client(client_factory, session)

    response = client.post(f"/api/jobs/{uuid4()}/verify")

    assert response.status_code == 404


def test_verify_credible_page_updates_availability(client_factory, session):
    user = make_user(session)
    job = make_job(
        session,
        company=make_company(session),
        job_url="https://jobs.example.com/openings/101",
    )
    fake = FakeFirecrawlClient(
        {job.job_url: crawl_result(job.job_url, markdown="# AI Engineer")}
    )
    client = client_factory(firecrawl=fake)
    login(client, user)

    response = client.post(f"/api/jobs/{job.id}/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "verified_available"
    assert body["message"]
    assert body["checked_at"]
    assert body["job"]["availability"] == "available"
    assert body["job"]["last_verified_at"] is not None
    assert fake.requested == [job.job_url]


def test_verify_failure_preserves_state_and_reports_check_failed(client_factory, session):
    user = make_user(session)
    now = datetime.now(timezone.utc)
    job = make_job(
        session,
        company=make_company(session),
        job_url="https://jobs.example.com/openings/102",
        availability=AvailabilityStatus.available,
        crawled_at=now - timedelta(days=1),
    )
    fake = FakeFirecrawlClient(
        {job.job_url: FirecrawlError("scrape returned HTTP 404", status_code=404, kind="http_error")}
    )
    client = client_factory(firecrawl=fake)
    login(client, user)

    response = client.post(f"/api/jobs/{job.id}/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "check_failed"
    assert body["job"]["availability"] == "available"
    assert body["job"]["last_verified_at"] is not None
    assert "preserved" in body["message"]


def test_verify_closed_page_marks_job_closed(client_factory, session):
    user = make_user(session)
    job = make_job(
        session,
        company=make_company(session),
        job_url="https://jobs.example.com/openings/103",
        availability=AvailabilityStatus.available,
        crawled_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    fake = FakeFirecrawlClient(
        {job.job_url: crawl_result(job.job_url, markdown="no longer accepting applications")}
    )
    client = client_factory(firecrawl=fake)
    login(client, user)

    response = client.post(f"/api/jobs/{job.id}/verify")

    body = response.json()
    assert body["outcome"] == "verified_closed"
    assert body["job"]["availability"] == "closed"
    assert body["job"]["closed_at"] is not None


def test_verify_respects_the_per_job_cooldown(client_factory, session):
    user = make_user(session)
    job = make_job(session, company=make_company(session), job_url="https://jobs.example.com/openings/104")
    fake = FakeFirecrawlClient({job.job_url: crawl_result(job.job_url, markdown="# AI Engineer")})
    client = client_factory(firecrawl=fake)
    login(client, user)

    first = client.post(f"/api/jobs/{job.id}/verify")
    second = client.post(f"/api/jobs/{job.id}/verify")

    assert first.status_code == 200
    assert second.status_code == 429
    assert "re-verify" in second.json()["detail"]
    assert len(fake.requested) == 1


def test_verify_never_scrapes_a_substituted_url(client_factory, session):
    user = make_user(session)
    job = make_job(
        session,
        company=make_company(session),
        job_url="https://jobs.example.com/openings/105",
    )
    fake = FakeFirecrawlClient()
    client = client_factory(firecrawl=fake)
    login(client, user)

    response = client.post(f"/api/jobs/{job.id}/verify?url=https://evil.example.com")

    assert response.status_code == 200 or response.status_code == 429
    assert all(url == job.job_url for url in fake.requested)
