# Phase 1B — Candidate & Private-Resource Ownership Report

## Summary

Phase 1B is **complete**. Private resources are now scoped to the
authenticated account:

- `get_current_candidate` resolves the candidate owned by the session's user
  (401 when unauthenticated, 404 when the account has no profile) instead of
  returning the first candidate in the table.
- `candidates.user_id` is **NOT NULL** (model + migration `20261012_0004`), so
  no ownerless profile can exist.
- Resume, matching and cover-letter routes require authentication and reject
  any cross-account `candidate_id`, `resume_document_id` or `match_id` with a
  404 **before** an agent run is created.
- CSRF now has a second, session-bound check: the submitted token must hash to
  the value stored on the session, so a forged but self-consistent cookie/header
  pair cannot ride an existing session.
- The full backend suite is green: **681 passed** (baseline before this phase:
  666 collected, of which 1 pre-existing failure — the metadata `EXPECTED_TABLES`
  gap, now fixed).
- `alembic check` reports **no new upgrade operations**; the dev database is at
  `20261012_0004 (head)` with all legacy data preserved.

## What changed

### 1. Authenticated candidate dependency

- `backend/app/api/deps.py`
  - `get_current_candidate` now depends on `get_current_user` and looks up the
    candidate by `Candidate.user_id == user.id`; missing profile → 404
    `NO_CANDIDATE_DETAIL`; no session → 401. No fallback to "the only candidate".
  - `get_current_session` verifies the CSRF token against the stored session
    hash on unsafe methods (`session_bound_token_is_valid`).
  - New `get_current_user`, `NOT_AUTHENTICATED_DETAIL` exported.
- `backend/app/services/candidates.py`
  - `candidate_for_user(session, user_id)` is the only owner lookup.
  - `create_candidate(..., user_id=...)` creates for the given owner and raises
    `DuplicateCandidateError` (→ 409) when the account already owns one.
  - Removed `current_candidate` and `candidate_count` (single-user leftovers).
- `backend/app/api/routes/candidates.py`
  - `GET /api/candidates` returns only the caller's profile (0 or 1 rows).
  - `GET /api/candidates/me` returns the caller's profile (404 if none).
  - `POST /api/candidates` creates for the authenticated account only; a second
    profile is a 409. The body cannot name an owner.

### 2. `candidates.user_id` NOT NULL

- `backend/app/models/candidate.py`: `user_id: Mapped[UUID]`, `nullable=False`
  (the FK, unique constraint and index are unchanged).
- `backend/alembic/versions/20261012_0004_candidate_owner_required.py`
  (revises `20261012_0003`): a preflight counts `candidates WHERE user_id IS
  NULL` and **aborts with an actionable error** if any remain, then alters the
  column to `NOT NULL`. Downgrade relaxes it again.

### 3. Resume operations

- `backend/app/api/routes/resumes.py` was already wired to
  `get_current_candidate`; it now inherits the authenticated, owner-scoped
  resolution for free. Both current-resume and history reads are owner-scoped.

### 4. Matching & cover letter

- `backend/app/api/routes/agents.py`
  - Both routes depend on `get_current_candidate`, so they are 401 without a
    session and 404 without a profile.
  - `_resolve_candidate_id` returns the authenticated candidate's id and treats
    any other explicit id as 404.
  - `_resolve_resume_id` requires an explicit document to exist and be owned by
    the authenticated candidate (404 otherwise); the document's *kind* remains
    the agent's precondition (409), so existing semantics are preserved.
  - `_resolve_match_id` requires an explicit match to exist, belong to the
    authenticated candidate and name the same job (404 otherwise).
  - Shell job ownership is checked **before** `run_agent_now`, so a cross-user
    id never creates an agent run.
- `discover`, `analyze`, `GET /api/jobs` and the applications routes were left
  intentionally unchanged (shared/unauthenticated; application ownership is the
  next phase).

### 5. Session-bound CSRF

- `backend/app/api/csrf.py`
  - Kept the double-submit middleware (`_tokens_match`), added
    `session_bound_token_is_valid(request, auth_session)` which hashes the
    header token and compares it to `auth_session.csrf_token_hash` with
    `hmac.compare_digest`.
  - `get_current_session` enforces it on unsafe methods (403
    `CSRF_ERROR_DETAIL`).

### 6. Tests

New:
- `backend/tests/api/test_api_ownership.py` — cross-user reads, cross-user
  `candidate_id` / `resume_document_id` / `match_id` rejection with zero runs
  written, unauthenticated match → 401, forged self-consistent CSRF → 403,
  genuine CSRF authorises, and "no match of your own" → 409.
- `backend/tests/test_candidate_owner_migration.py` — migration `0004` aborts
  with "no owner" on an ownerless row, succeeds when every candidate is owned
  and yields a NOT NULL column, and downgrade relaxes it.

Updated:
- `tests/factories.py` — `make_user`, `TEST_PASSWORD`; `make_candidate` now
  attaches an owner account (or an explicitly passed user/user_id) so the rest
  of the suite keeps working.
- `tests/api/support.py` — `login`, `register`, `make_user_client` helpers that
  log in and mirror the CSRF cookie into the `X-CSRF-Token` header.
- API suites (`test_api_candidates`, `test_api_resumes`, `test_api_matching`,
  `test_api_cover_letter`, `test_api_workflow`, `test_api_errors`) authenticate
  their clients; `test_api_errors` targets `candidate_for_user` for the
  opaque-500 check and exercises 422 with an authenticated client.
- `tests/test_auth_bootstrap.py` — rewritten for the post-0004 reality (an
  ownerless candidate can no longer exist, so the reuse path is unreachable;
  covered by the migration test instead).
- `tests/test_metadata.py` — `EXPECTED_TABLES` now includes `users` and
  `sessions` (the pre-existing failure).

## Cross-user verification

A second account cannot read another account's profile or resume, cannot pass
another account's `candidate_id`, `resume_document_id` or `match_id` to the
agent routes, and receives the same 404/409 it would for an unknown id. Each
rejection is asserted to write **zero** `agent_runs`/`matches`/`documents`. A
forged CSRF pair that agrees with itself but not with the stored session hash is
rejected 403.

## Migration & data preservation (dev database)

- Preflight before upgrade: exactly one candidate
  `5d9f9f21-f812-4eff-9dc6-4ac0d6ad1b91`, already owned by
  `83d79a31-d4a7-4adb-aae6-54ec593e5cbd` (`longtsu59@gmail.com`).
- `alembic upgrade head` → `Running upgrade 20261012_0003 -> 20261012_0004`.
- Verified after upgrade: revision `20261012_0004`, `candidates.user_id`
  `is_nullable = NO`, the single candidate still owned by the same account, and
  counts unchanged — jobs 17, job_analyses 2, matches 1, applications 2,
  documents 2, agent_runs 9. No reset, drop or data reassignment.

## Commands run

```
cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 681 passed
.\.venv\Scripts\python.exe -m alembic check      # No new upgrade operations detected.
.\.venv\Scripts\python.exe -m alembic current    # 20261012_0003 -> then 20261012_0004 (head)
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Frontend was not changed, so `npm run build` was not required.

## Boundaries honored

- No application ownership work (deferred to the next phase).
- No redesign of shared job discovery, analysis or the agent runtime.
- No commits, staging, resets, stashes or discards; unrelated working-tree
  changes preserved.
- No destructive database operations; no servers or containers restarted.
- No external Gemini/Firecrawl/SearXNG calls.
- No passwords, hashes, tokens or resume contents printed.

## Unresolved risks (not fixed in this phase)

1. **Global agent queue claiming.** `run_agent_now` still claims the oldest
   pending run across *all* users, so ownership is enforced at the route but a
   pending run is not partitioned per account. Multi-user execution is **not**
   safe until claiming is account-aware. Do not claim multi-user security yet.
2. **Frontend has no authentication UI.** Every private endpoint now requires a
   session + CSRF header, so the current SPA (which sends neither) is
   non-functional against this API until an auth/onboarding flow lands.
3. **No profile-enrichment path.** Registration creates a name-only candidate;
   there is no endpoint to add summary/skills/etc. afterward, and Job Matching
   requires that evidence. Newly registered accounts cannot match until this is
   addressed (the seeded/legacy profile is unaffected).
4. **`POST /api/candidates` is effectively unreachable for registered users**
   (registration already creates a profile), so onboarding via that endpoint
   will 409. Consider folding profile fields into registration.
5. **Cover-letter documents have no DB uniqueness backstop** (unlike
   `job_analyses`/`matches`); duplicate prevention relies solely on the runtime's
   idempotency key.
6. **Documentation drift.** `AGENTS.md`, `PROJECT_SPECIFICATION.md` and
   `README.md` still describe the pre-auth, single-user behaviour and were not
   updated in this phase.
