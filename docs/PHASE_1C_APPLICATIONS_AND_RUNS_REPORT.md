# Phase 1C — Application Ownership & Agent-Run Isolation Report

## Summary

Phase 1C is **complete**. Two cross-account leaks are closed:

- **Application tracking is now private.** `applications.candidate_id` is a
  required, indexed, `ON DELETE CASCADE` foreign key to `candidates.id`
  (model + migration `20261012_0005`). Every application read/write route is
  scoped to the authenticated account's candidate; a row owned by another
  account answers exactly like an unknown id (404).
- **An agent request can no longer touch another account's run.** The HTTP
  surface claims its run **by id** (`AgentRuntime.claim_run`) instead of the
  shared queue's `claim_next`, so a request can only ever execute, complete or
  return the run it created or resumed — never a foreign pending run, its
  output or its error.
- The migration backfills existing applications **only when ownership is
  unambiguous** (exactly one candidate) and aborts with an actionable error
  otherwise; it never hands legacy rows to whoever registers first.
- The full backend suite is green: **697 passed** (Phase 1B baseline: 681).
- `alembic check` reports **no new upgrade operations**; the dev database is at
  `20261012_0005 (head)` with all smoke-test data preserved.

## What changed

### Part A — Application ownership

#### 1. Model

- `backend/app/models/application.py`
  - New `candidate_id: Mapped[UUID]` — `ForeignKey("candidates.id",
    ondelete="CASCADE")`, `nullable=False`, `index=True`.
  - New `candidate` relationship (`back_populates="applications"`).
- `backend/app/models/candidate.py`
  - New `applications` relationship (`back_populates="candidate"`,
    `cascade="all, delete-orphan"`, `passive_deletes=True`), mirroring
    `documents` and `matches`.

#### 2. Migration `20261012_0005`

- `backend/alembic/versions/20261012_0005_application_owner_required.py`
  (revises `20261012_0004`).
  - **Preflight first, before any DDL.** Counts `applications`; if any exist it
    counts `candidates` and:
    - 0 candidates → `RuntimeError` ("no candidate profile … to own them"),
    - >1 candidates → `RuntimeError` ("ownership is ambiguous"),
    - exactly 1 → the single candidate is the unambiguous owner.
  - Adds `candidate_id` (nullable), backfills it from the single candidate,
    then creates the index and cascade FK and alters the column to NOT NULL.
  - Downgrade drops the FK, index and column.
  - Because the preflight raises before the first write, an aborted upgrade
    changes nothing.

#### 3. Service

- `backend/app/services/applications.py`
  - `get_application(session, id, *, candidate_id)` — a row owned by another
    candidate raises `ApplicationNotFoundError` (indistinguishable from an
    unknown id).
  - `list_applications(session, *, candidate_id, status, limit, offset)` — the
    owner is an unconditional filter, so totals and pages only ever count the
    caller's rows.
  - `create_application(session, *, candidate_id, ...)` — the owner is a
    required argument; the client cannot choose it.
  - `update_application(..., *, candidate_id)` — verifies ownership via
    `get_application` before writing.

#### 4. Routes

- `backend/app/api/routes/applications.py`
  - All four endpoints (`POST`, `GET` list, `GET /{id}`, `PATCH /{id}`) depend
    on `get_current_candidate`, so they are 401 without a session and 404
    without a profile, and the session-bound CSRF check applies to the unsafe
    methods.
  - The owner passed to the service is always `candidate.id`; the request body
    cannot name it (`ApplicationCreateRequest` forbids extra fields), so
    `POST` with a `candidate_id` is a 422.

### Part B — Agent-run isolation

#### 5. Runtime

- `backend/app/agents/runtime/runtime.py`
  - New `claim_run(run_id)`: `SELECT … WHERE id = :id AND status = 'pending'
    FOR UPDATE` (blocking, no `SKIP LOCKED`). A run that is already running,
    succeeded, failed or cancelled yields `None` instead of being executed
    twice.
  - Extracted `_apply_claim(run)` (transition to `running`; `started_at` on the
    first claim, else `attempt_count += 1`; flush) and shared it with
    `claim_next`, so both claim paths have identical bookkeeping.
  - `claim_next`/`run_next` remain for the generic runtime and its tests; the
    HTTP surface no longer uses them.

#### 6. Request runner

- `backend/app/api/agent_runner.py`
  - `run_agent_now` now does `create_run` → commit → `claim_run(run_id)` → (if
    claimed) commit → `execute` → commit. It returns the reserved run when the
    claim yields `None` (already succeeded / in flight / settled elsewhere).
  - Consequence: a request **never drains the queue**. A pending run left by a
    concurrent duplicate is not opportunistically executed; isolation takes
    priority over emptying the queue. Retry semantics are unchanged — a
    retryable failure still leaves the run `pending` for a later attempt, and
    the request executes its run at most once.

## Tests

New:

- `backend/tests/test_application_owner_migration.py` — revision `0005`:
  backfills every row when one candidate exists and yields a NOT NULL column;
  aborts ("ambiguous") with two candidates; aborts ("no candidate profile")
  with none; is a no-op with no applications; downgrade removes the column.
- `backend/tests/api/test_api_run_isolation.py` — an authenticated match request
  leaves a seeded foreign pending run **pending**, with no output/error and an
  unchanged attempt count, and returns its own run rather than the foreign id.
- `backend/tests/test_agent_runtime.py` — five `claim_run` tests: claims only
  the named run (a sibling stays pending), `None` for an unknown id / an
  already-claimed run / a terminal run, and `attempt_count` increments on a
  retry.

Updated:

- `backend/tests/api/test_api_applications.py` — every test authenticates;
  added "requires authentication" and "cannot choose an owner" (422).
- `backend/tests/api/test_api_ownership.py` — two-account application scoping:
  each account sees only its own rows, a cross-owner `GET`/`PATCH` is a 404,
  the rejected patch changes nothing, and each row records the caller's owner.
- `backend/tests/factories.py` — `make_application_to_job` now attaches a
  candidate (creating one by default) via the new `candidate`/`candidate_id`
  overrides.
- `backend/tests/test_application.py` — direct `Application(...)` constructions
  supply `candidate_id`.
- `backend/tests/test_migrations.py` — the seeded fixture advances to `0003`
  and inserts one user/candidate before head, so `0005` has an unambiguous
  owner; the upgrade assertion now checks `application.candidate_id`.

## Cross-account verification

A second account cannot list, read or patch the first account's applications
(404, indistinguishable from an unknown id) and cannot name an owner on create
(422). An authenticated request that runs job matching leaves a seeded foreign
pending run untouched — no status change, no output, no error, no attempt
increment — while the caller's own run executes and succeeds.

## Migration & data preservation (dev database)

- Pre-upgrade state: revision `20261012_0004`; 2 applications, 1 candidate
  (`5d9f9f21-f812-4eff-9dc6-4ac0d6ad1b91`).
- `alembic upgrade head` → `Running upgrade 20261012_0004 -> 20261012_0005,
  require an application owner` (unambiguous → both rows backfilled).
- Verified after upgrade: revision `20261012_0005 (head)`; both applications
  now owned by the candidate:
  - `5099f5f3-277a-4a58-8379-e448671f53bd` (job `b0112376…`, status `applied`),
  - `be7ddcb7-e760-4541-b48a-6884867fbec4` (job `be9897f9…`, status `draft`).
- Counts unchanged — jobs 17, job_analyses 2, matches 1, applications 2,
  documents 2, agent_runs 9, candidates 1, users 1. No reset, drop or
  reassignment.

## Commands run

```
cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 697 passed
.\.venv\Scripts\python.exe -m alembic current    # 20261012_0004 -> then 20261012_0005 (head)
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check      # No new upgrade operations detected.
```

Frontend was not changed, so `npm run build` was not required.

## Boundaries honored

- No frontend auth/onboarding work.
- Shared job discovery and job analysis were not redesigned; only the claim
  mechanism changed, and their behavior is preserved (their idempotency keys
  stay shared/per-job, never per-user).
- No commits, staging, resets, stashes or discards; unrelated working-tree
  changes preserved.
- No destructive database operations; no servers or containers restarted.
- No external Gemini/Firecrawl/SearXNG calls.
- No passwords, hashes, tokens or resume contents printed.
- `AGENTS.md`, `README.md` and `PROJECT_SPECIFICATION.md` were not modified.

## Unresolved risks (not fixed in this phase)

1. **Frontend has no authentication UI.** Private endpoints (now including
   applications) require a session + CSRF header the SPA does not send, so the
   UI is non-functional against this API until an auth/onboarding flow lands.
2. **No profile-enrichment path.** Registration still creates a name-only
   candidate; newly registered accounts cannot match until profile evidence can
   be added.
3. **`POST /api/candidates` is effectively unreachable for registered users**
   (registration already creates a profile), so it 409s.
4. **Cover-letter documents have no DB uniqueness backstop** (unlike
   `job_analyses`/`matches`); duplicate prevention relies solely on the
   runtime's idempotency key.
5. **Shared-queue `claim_next` still exists.** No HTTP path uses it, but any
   future worker built on it must partition by owner before it can run
   multi-user work safely.
6. **The queue is no longer drained by requests.** A pending run created by a
   caller that then loses a race is left for a worker; without a worker it
   stays pending until retried by a later request for the same run. This is a
   deliberate trade for isolation.
7. **Documentation drift.** `AGENTS.md`, `PROJECT_SPECIFICATION.md` and
   `README.md` still describe the pre-auth, single-user behaviour.
