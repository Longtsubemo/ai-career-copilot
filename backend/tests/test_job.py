"""Discovery and crawl state on Job."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import CrawlStatus
from app.services.normalization import canonicalize_url
from tests.factories import make_company, make_job


def test_search_result_can_be_persisted_before_crawling(session: Session) -> None:
    job = make_job(session, description=None)
    session.commit()

    assert job.description is None
    assert job.crawl_status == CrawlStatus.pending
    assert job.crawled_at is None


def test_crawl_and_discovery_timestamps_are_server_populated(session: Session) -> None:
    job = make_job(session)
    session.commit()
    session.expire_all()

    reloaded = session.get(type(job), job.id)
    assert reloaded is not None
    assert reloaded.first_seen_at is not None
    assert reloaded.last_seen_at is not None
    assert reloaded.created_at is not None
    assert reloaded.updated_at is not None


def test_duplicate_canonical_url_is_rejected(session: Session) -> None:
    make_job(session, job_url="https://example.com/jobs/1", canonical_url="https://example.com/jobs/1")
    session.commit()

    with pytest.raises(IntegrityError):
        make_job(session, job_url="https://example.com/jobs/1", canonical_url="https://example.com/jobs/1")
        session.commit()
    session.rollback()


def test_tracking_variants_dedupe_to_one_job(session: Session) -> None:
    """Two spellings of the same posting must resolve to a single stored job."""
    plain_url = "https://example.com/jobs/42"
    tracked_url = "https://www.example.com/jobs/42?utm_source=linkedin"

    assert canonicalize_url(plain_url) == canonicalize_url(tracked_url)

    job = make_job(session, job_url=plain_url)
    session.commit()

    with pytest.raises(IntegrityError):
        make_job(session, job_url=tracked_url)
        session.commit()
    session.rollback()


def test_raw_job_url_is_retained_as_provenance(session: Session) -> None:
    raw = "https://example.com/jobs/7?utm_source=linkedin&trk=public"
    job = make_job(session, job_url=raw)
    session.commit()

    assert job.job_url == raw
    assert job.canonical_url == "https://example.com/jobs/7"


def test_job_url_is_no_longer_unique(session: Session) -> None:
    make_job(session, job_url="https://example.com/dup", canonical_url="https://example.com/dup-a")
    make_job(session, job_url="https://example.com/dup", canonical_url="https://example.com/dup-b")
    session.commit()


def test_invalid_crawl_status_is_rejected(session: Session) -> None:
    job = make_job(session, canonical_url="https://example.com/jobs/bad-status")
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE jobs SET crawl_status = 'not-a-status' WHERE id = :id"),
            {"id": job.id},
        )
        session.commit()
    session.rollback()


def test_valid_crawl_status_transitions_are_accepted(session: Session) -> None:
    job = make_job(session, canonical_url="https://example.com/jobs/ok-status")
    job.crawl_status = CrawlStatus.scraped
    session.commit()
    session.expire_all()

    reloaded = session.get(type(job), job.id)
    assert reloaded is not None
    assert reloaded.crawl_status == CrawlStatus.scraped


def test_job_can_exist_without_a_company(session: Session) -> None:
    job = make_job(session, company=None)
    session.commit()
    assert job.company_id is None


def test_job_can_be_attached_to_a_company(session: Session) -> None:
    company = make_company(session)
    job = make_job(session, company)
    session.commit()

    session.expire_all()
    reloaded = session.get(type(job), job.id)
    assert reloaded is not None
    assert reloaded.company is not None
    assert reloaded.company.id == company.id