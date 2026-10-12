# Phase 1G — Discovery Quality, Deduplication, Diagnostics and Freshness

## Summary

Job Discovery now separates individual vacancies from listing/search pages,
records a bounded, categorized diagnostic trail for every result it cannot
use, and tracks whether a discovered vacancy is actually open. Availability
is stored as evidence (`unknown` / `available` / `closed`) and `stale` is
derived at read time, so an old posting is never presented as confirmed
available and no scheduler or recurring crawl is required.

All of this is deterministic and offline in tests: no LLM, no scheduler, no
background worker, no live SearXNG/Firecrawl/Gemini.

## Files changed

### Backend

- `backend/app/agents/discovery/classify.py` — **new.** Pure URL/page quality
  classifier: `UrlQuality` (`individual` / `listing` / `unknown`),
  `classify_url`, `classify_page`, `resolve_quality`, `has_closure_evidence`,
  `crawl_http_status`, `sanitize_url`, `url_host`.
- `backend/app/agents/discovery/contract.py` — `DiscoveryIssueCategory`
  (11 values), `DiscoveryIssue`, `MAX_DISCOVERY_ISSUES = 25`, and eight new
  `DiscoveryResult` fields (counters plus bounded `issues`).
- `backend/app/agents/discovery/agent.py` — URL-quality skip (pre- and
  post-crawl), diagnostic counters/issues, `date_posted` upsert, and
  availability/`closed_at` writes. Failed crawls leave availability untouched.
- `backend/app/agents/discovery/extract.py` — `ExtractedJob.date_posted`,
  `parse_posted_date`, metadata (`datePosted`/`date_posted`/`datePublished`)
  and `Date posted:`/`Posted:` line parsing; reliable ISO dates only.
- `backend/app/agents/discovery/__init__.py` — re-exports the new public names.
- `backend/app/clients/firecrawl.py` — `FirecrawlError` gained keyword-only
  `status_code` and `kind`; `scrape()` now distinguishes status, timeout,
  network, API and shape failures.
- `backend/app/models/enums.py` — `AvailabilityStatus`.
- `backend/app/models/job.py` — `availability` (NOT NULL, default/index) and
  `closed_at`, plus the `ck_jobs_availability_vocabulary` check.
- `backend/app/services/freshness.py` — **new.** Pure `resolve_availability`
  and `freshness_threshold`; `FRESHNESS_DAYS_DEFAULT = 30`.
- `backend/app/core/config.py` — `CAREER_COPILOT_JOB_FRESHNESS_DAYS` (default
  30, `>= 1`).
- `backend/app/api/schemas.py` — `JobOut` exposes effective `availability`,
  `last_verified_at` and `closed_at`.
- `backend/app/api/routes/jobs.py` — `_job_out` resolves effective
  availability with the configured threshold.
- `backend/alembic/versions/20261012_0006_job_availability.py` — **new**
  migration (down_revision `20261012_0005`).
- Tests: `tests/test_discovery_classify.py` (new, 37), `tests/test_freshness.py`
  (new, 10), `tests/test_discovery_quality.py` (new, 16),
  `tests/api/test_api_jobs.py` (+4), `tests/test_discovery_agent.py` (invariant
  and exact-output updates), `tests/test_migrations.py` (expected constraint +
  data-preservation assertion).

### Frontend

- `frontend/src/types/api.ts` — `AvailabilityStatus`, `DiscoveryIssue`, new
  `Job` fields and extended `DiscoveryOutput`.
- `frontend/src/utils/labels.ts` — `AVAILABILITY_META`.
- `frontend/src/pages/JobsPage.tsx` — availability badge and a corrected
  discovery summary (real skip/duplicate/unreadable counters).
- `frontend/src/pages/JobDetailPage.tsx` — availability badge and last-verified
  / closed timestamps.
- `frontend/tests/labels.test.ts` — new availability label tests.

## URL quality classification

`classify_url` uses only http/https URLs and positive evidence; a page title is
never treated as individual-vacancy evidence.

- **individual**: a strong vacancy path segment (`jobs`, `job-details`,
  `positions`, `view`, `role`, `vacancy`, …) followed by a specific token; a
  weak segment (`careers`, `work`) followed by an id-like token (digits, UUID,
  hex, or a long digit-bearing slug); or a known individual query parameter
  (`gh_jid`, `currentJobId`, `job_id`, `jk`, `vjk`, …).
- **listing**: a listing path (`/search`, `/results`, `/browse`,
  `/category/…`, `/company/…`, trailing `jobs`/`careers`/`search`/…) or a
  search query key (`q`, `query`, `search`, `keywords`).
- **unknown**: everything else.

`classify_page` can only refine an unknown URL: `@type: JobPosting` or
structured hiring metadata makes it individual, explicit listing text makes it
listing. `resolve_quality` lets positive individual evidence win; a listing URL
is skipped **before** any crawl, and a page that turns out to be a listing is
skipped after the crawl. `job_url` is preserved verbatim as provenance.

Closure is only asserted on strong evidence: HTTP 410, or explicit phrases
("no longer accepting applications", "position has been filled", …). Bot
protection is never inferred.

## Deduplication and concurrency (audit)

The existing dedupe keys are unchanged and required no new uniqueness rules:

- `companies.normalized_name` — `INSERT … ON CONFLICT (normalized_name) DO
  UPDATE SET name = name RETURNING id`.
- `jobs.canonical_url` (+ `uq_jobs_canonical_url`) — `INSERT … ON CONFLICT
  (canonical_url) DO UPDATE` with `RETURNING (xmax = 0) AS inserted`.

Both run inside the caller's single transaction and are safe under concurrent
discovery (`tests/test_discovery_runtime.py`). No job is deleted or merged and
no new migration was needed for dedupe. Within one result set, duplicate
canonical URLs are counted (`duplicates_skipped`) and dropped before crawling.

## Diagnostics and failure categories

`DiscoveryResult` retains its original eight keys and adds:
`duplicates_skipped`, `scrape_attempts`, `extractions_succeeded`,
`listings_skipped`, `invalid_results`, `malformed_results`, `scrape_failures`,
and `issues`.

Invariant (asserted by tests):

```
jobs_created + jobs_updated + jobs_skipped
  + listings_skipped + invalid_results == candidates_found
```

Malformed results are not candidates and are counted separately.

Issue categories are factual: `search_failure`, `network_error`, `timeout`,
`http_error` (with `status`), `access_denied` (401/403/429), `empty_extraction`,
`invalid_url`, `listing_page`, `validation_rejected`, `duplicate`,
`scrape_failure`. Every issue URL is sanitized to scheme + host + path (query
and fragment stripped, bounded to 300 chars) and the list is capped at 25 while
counters stay exact.

## Freshness and availability

| Stored `availability` | Meaning | Written when |
| --- | --- | --- |
| `unknown` | never credibly verified | new row, or a failed crawl on a new job |
| `available` | successful individual-page crawl, no closure evidence | crawl success |
| `closed` | HTTP 410 or explicit closure phrases | crawl success with closure evidence |
| `stale` | *derived at read time* — an `available` job older than the threshold | never persisted |

`crawled_at` doubles as the last verification timestamp and advances only on a
successful crawl, so failed re-crawls preserve both the previous verification
time and the previous availability. An API response computes the effective
value with `resolve_availability(..., threshold_days=job_freshness_days)`; a
failed crawl can never close a vacancy, and a vague date is never invented.
`date_posted` is persisted only from a reliable ISO `YYYY-MM-DD` source.

## Migration and data preservation

- Revision `20261012_0006_add_job_availability` (`down_revision 20261012_0005`).
- Adds `availability` (server default `unknown`, NOT NULL), `closed_at`
  (nullable) and `ix_jobs_availability`; creates
  `ck_jobs_availability_vocabulary`.
- Non-destructive: every existing row keeps its data and receives `unknown`;
  nothing is backfilled from `crawled_at`. `downgrade()` reverses all four
  operations.
- `tests/test_migrations.py` upgrades a scratch database, runs a
  downgrade/upgrade round trip, diffs against the models and asserts a
  pre-milestone job survives with `availability = 'unknown'` and
  `closed_at = NULL`.

## Verification

Backend (`backend/` with the venv active):

- Focused: `python -m pytest tests/test_discovery_classify.py
  tests/test_freshness.py tests/test_discovery_quality.py
  tests/api/test_api_jobs.py tests/test_migrations.py -q` → **86 passed**.
- Full suite: `python -m pytest -q` → **814 passed**.
- `python -m alembic upgrade head` then `python -m alembic check` →
  **No new upgrade operations detected.**

Frontend (`frontend/`):

- `npm test` → **45 passed**.
- `npm run build` (`tsc -b && vite build`) → **success**.

## Limitations

- Role/location matching is still token overlap; `"Bengaluru"` does not match
  `"Bangalore"`. URL classification is pattern-based and can misclassify
  unusual career sites; uncertain URLs stay `unknown` (never available) and are
  never dropped.
- `stale` is purely age-based; it is not evidence that a posting closed.
- Closure detection is conservative — only 410 and explicit phrases. A silently
  removed posting stays `available` until it ages into `stale`.
- Max 25 issue details per run; counters remain exact beyond that.
- Still no pipeline/scheduler: a client (or test) still drives
  discover → analyze → match → cover-letter; freshness is re-derived on read.

## Next milestone (proposed)

Add a read-side availability filter (e.g. hide `closed`, optionally hide
`stale`) and an explicit "re-verify this job" action that re-runs discovery for
one candidate URL, reusing the freshness and classification machinery without
introducing a background scheduler.
