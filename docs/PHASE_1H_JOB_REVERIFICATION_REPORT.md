# Phase 1H — Availability Filters and Manual Job Re-verification

## Summary

The jobs list can now be filtered by effective availability (available /
stale / closed / unknown), and a logged-in user can re-verify a single saved
job: the stored `job_url` is re-fetched through Firecrawl and the posting is
judged again with the existing discovery classification machinery. An
explicit check never invents availability — a failed or inconclusive check
preserves the previous state — and a per-job cooldown stops hammering the
scraper. All of it is deterministic and offline in tests: no LLM, no
scheduler, no background worker.

The re-verify action is user-triggered and synchronous, consistent with the
rest of the product (agent endpoints finish inside the request). No
background monitoring is introduced.

## Files changed

### Backend

- `backend/app/models/job.py` — added `last_checked_at`
  (`DateTime(timezone=True)`, nullable): when the source URL was last
  re-verified, **including failed checks**. Deliberately separate from
  `crawled_at` (the last *successful* verification), so a persistent
  last-checked timestamp and a cooldown survive failures and restarts.
- `backend/alembic/versions/20261012_0007_job_last_checked_at.py` — **new**
  migration (`down_revision 20261012_0006`); non-destructive nullable column,
  nothing backfilled.
- `backend/app/agents/discovery/classify.py` — `has_usable_content` is now a
  public shared helper (moved out of `agent.py`); re-exported from
  `app.agents.discovery`.
- `backend/app/core/config.py` — `job_verification_cooldown_seconds` (default
  60, `>= 0`; env `CAREER_COPILOT_JOB_VERIFICATION_COOLDOWN_SECONDS`, `0`
  disables the guard).
- `backend/app/services/job_verification.py` — **new.** `VerificationOutcome`
  (`verified_available` / `verified_closed` / `still_unknown` /
  `check_failed`), `VerificationResult`, `JobVerificationCooldownError`, and
  `verify_job_availability(session, job, *, firecrawl, cooldown_seconds=60,
  clock=utc_now)`.
- `backend/app/services/jobs.py` — `list_jobs` gains `availability`,
  `now` and `threshold_days`; new `availability_predicates` helper applies the
  filter **before** the total count and pagination.
- `backend/app/api/schemas.py` — `JobVerificationOut` (`job: JobOut`,
  `outcome`, `message`, `checked_at`).
- `backend/app/api/deps.py` — `get_firecrawl(request)` reading
  `app.state.firecrawl`.
- `backend/app/main.py` — one settings-derived `FirecrawlClient` shared by the
  discovery handler and stored on `app.state.firecrawl`.
- `backend/app/api/routes/jobs.py` — `_job_out(job, *, now=None)` shares a
  single `now` with the filter; the list endpoint accepts
  `availability: AvailabilityStatus | None`; new `POST /api/jobs/{job_id}/verify`
  (auth + CSRF, 404 for unknown job, cooldown, commits, `JobVerificationOut`).
- `backend/app/api/errors.py` — `JobVerificationCooldownError` → HTTP 429.
- Tests: `tests/test_job_verification.py` (**new**, 15),
  `tests/api/test_api_jobs.py` (+12).

### Frontend

- `frontend/src/types/api.ts` — `VerificationOutcome`, `JobVerificationResult`.
- `frontend/src/api/jobs.ts` — `availability` on `JobQuery`, `verifyJob()`.
- `frontend/src/utils/labels.ts` — `VERIFICATION_OUTCOME_META`.
- `frontend/src/pages/JobsPage.tsx` — availability filter Select, applied with
  the other filters, cleared by the same Clear path and included in
  `hasFilters`.
- `frontend/src/pages/JobDetailPage.tsx` — "Availability check" card with a
  manual Re-verify button, point-in-time note, outcome badge + message, and a
  silent job reload after a check so the availability badge and timestamps
  update.

## Availability filter semantics

`GET /api/jobs` accepts `availability: available | stale | closed | unknown`.
The filter uses the **effective** availability exactly as `_job_out`
computes it, so what you filter by is what you see:

| Filter value | Rows matched (SQL) |
| --- | --- |
| `available` | stored `available` and `crawled_at >= now - threshold` (a null `crawled_at` counts as fresh, matching `resolve_availability`) |
| `stale` | stored `available` and `crawled_at < now - threshold` |
| `closed` | stored `closed` |
| `unknown` | stored `unknown` |

The predicate is built from a single `now = utc_now()` shared with `_job_out`,
applied before the row count and `OFFSET`/`LIMIT`, so `total` reflects the
filter and pagination stays consistent. Invalid values are rejected by
FastAPI (422). Existing list behaviour (no filter) is unchanged, and clearing
the filter simply omits the query parameter.

## Re-verification design

### Endpoint

`POST /api/jobs/{job_id}/verify` — authenticated (session + CSRF), because it
**mutates a shared job row** (jobs are shared across users; only
resumes/applications are private). Jobs list/detail stay unauthenticated
reads. Returns `JobVerificationOut` with the updated `JobOut` so the caller
needs no second request.

### Evidence rules (`verify_job_availability`)

Only the stored `job.job_url` is ever fetched — a client-supplied URL is not
accepted (and the HTTP test proves no substituted URL is scraped). The check
is serialized with `session.refresh(job, with_for_update=True)` and a re-read
of `last_checked_at` after the lock, so concurrent checks serialize on one
row and the cooldown is decided on the current state.

| Outcome | When | Writes |
| --- | --- | --- |
| `verified_available` | page classified `individual`, `has_usable_content`, **no** `has_closure_evidence` | `availability=available`, `crawled_at=now`, `closed_at=None`, `crawl_status=scraped`, `last_checked_at=now` |
| `verified_closed` | HTTP 410, or an individual page with explicit closure phrases | `availability=closed`, `closed_at=now`, `crawled_at=now`, `crawl_status=scraped`, `last_checked_at=now` |
| `still_unknown` | page not classed as an individual vacancy (listing/unknown) | availability untouched; `crawl_status=scraped`, `last_checked_at=now` |
| `check_failed` | unfetchable saved URL (invalid scheme/host), any `FirecrawlError` (404/5xx/timeout/403/429/api/shape), or empty/unusable content | availability **and** `crawled_at` preserved; `crawl_status=failed`, `last_checked_at=now` |

Rules that guarantee honesty:

- A check can only degrade `available` → `closed` on direct closure evidence,
  never on a fetch failure. A failed check preserves the previous verification
  time too (`crawled_at` untouched), so a job cannot silently age from a blip.
- `still_unknown` and `check_failed` never flip availability; the message
  explains why and points out that the stored state was left as it was.
- "Available" is time-stamped as of the check ("Re-verified on the live
  page…"), an explicit point-in-time statement rather than a standing promise.

### Cooldown and concurrency

Repeated checks within `job_verification_cooldown_seconds` raise
`JobVerificationCooldownError` → **HTTP 429** with an estimated wait time. The
cooldown uses the persistent `last_checked_at`, so it survives process
restarts and works across all clients; `0` disables it. The row lock
(`with_for_update=True`) plus the post-lock re-read prevent two quick requests
from both passing the cooldown check, and a single update wins in the end. The
endpoint commits after the check; the service only flushes.

### Failure behaviour

A `check_failed` never aborts the request (it is a valid outcome returned with
the preserved row). A 429 is the only error status the endpoint itself
produces before scraping; 401/404 come from auth/job lookup. Network-level
Firecrawl failures are wrapped into the `check_failed` outcome through the
existing `FirecrawlError` status/kind taxonomy and never break the API shape.

## Migration and data preservation

- Revision `20261012_0007_job_last_checked_at` (`down_revision 20261012_0006`).
- Adds a single nullable column `jobs.last_checked_at`; `downgrade()` drops it.
- Non-destructive: every existing row keeps its data and starts with
  `last_checked_at = NULL` — nothing is backfilled from `crawled_at`, because
  an old crawl is evidence of discovery, not of a recent check.
- `alembic upgrade head` then `alembic check` → **No new upgrade operations
  detected** (the schema diff remains clean against the models).

## Verification

Backend (`backend/` with the venv active):

- `python -m pytest tests/test_job_verification.py tests/api/test_api_jobs.py -q`
  → **42 passed**.
- Full suite: `python -m pytest -q` → **841 passed**.
- `python -m alembic upgrade head` then `python -m alembic check` →
  **No new upgrade operations detected.**

Frontend (`frontend/`):

- `npm test` → **45 passed**.
- `npm run build` (`tsc -b && vite build`) → **success**.

## Limitations

- Re-verification is **manual** — the spec intentionally adds no scheduler or
  background monitor. `stale` remains a read-time computation and only the
  freshness/age of `available` jobs, not a signal of closure.
- The check inherits discovery's honesty constraints: `unknown` pages stay
  unknown, closure is only recognised from 410 / explicit phrases, and
  token-overlap/classification limitations still apply.
- The cooldown is applied to **all** outcomes, including `check_failed`, which
  suppresses immediate re-tries after a failure. This is the intended
  rate-limit, but it is worth calling out for operators who expect to retry
  instantly.
- The verification timestamp lives on the shared job row, so a check performed
  by one user is visible to all; there is no per-user audit trail of checks.

## Next milestone (proposed)

Automatic monitoring without a scheduler is not possible in the current
synchronous model. Two natural next steps: an "open and recently verified"
smart default, and re-verifying *all* stale jobs in one authenticated request
(a bounded loop over `stale` rows, still inside the request, still honest
per-job, re-using this service).