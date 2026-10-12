# Phase 1E — Assisted Application Workflow Report

## Summary

Phase 1E is **complete**. The product now supports an **assisted** application
workflow: it prepares materials, opens the employer's original posting, and lets
the user submit externally before honestly recording the outcome. It is **not**
an auto-apply bot and never claims a submission on the user's behalf.

- **Server-owned `applied_at`.** The client can no longer supply a timestamp on
  create or update; the server stamps `applied_at` only when the status moves
  into `applied` and preserves it afterwards. A payload that still sends
  `applied_at` is a `422` (`extra="forbid"`).
- **Explicit confirmation.** Moving an application to **Applied** — via the
  status selector or the "Mark as applied" shortcut, on the job detail page or
  the applications list — opens an inline confirmation that the user must tick
  before the change is sent.
- **Duplicate prevention.** A job-linked `POST /api/applications` is idempotent:
  the first track answers `201`, a repeat answers `200` with the existing
  record, and a transaction-scoped advisory lock keeps two racing requests down
  to one row.
- **Honest messaging.** The job detail page and the applications page explain
  the three-step workflow (prepare → apply on the employer's site → record) and
  state plainly that this app never submits for you.
- Backend suite green: **725 passed** (Phase 1D baseline: 708; **+17 new**).
- Frontend: `npm run build` clean, `npm test` **30 passed** (+5 new).
- `alembic check`: **no new upgrade operations** — no schema change this phase.

## What changed

### Part A — Assisted tracking and duplicate prevention

No model or migration change.

#### 1. Service

- `backend/app/services/applications.py`:
  - New `find_application_for_job(session, *, candidate_id, job_id)` returns the
    candidate's oldest record for a job (deterministic `created_at, id`
    tie-break).
  - New `_lock_job_tracking(...)` takes a transaction-scoped
    `pg_advisory_xact_lock(hashtext(namespace), hashtext(key))` over namespace
    `application:job` and key `"{candidate_id}:{job_id}"` — the same pattern the
    agent runtime uses for idempotency.
  - New `track_application(...) -> (Application, created)`: acquires the lock,
    finds-or-creates, and returns the existing row with `created=False` on a
    repeat. The caller owns the transaction; `flush` only.

#### 2. Route

- `backend/app/api/routes/applications.py` — `POST /api/applications`:
  - A job-linked body goes through `track_application`. The route injects
    `Response` and sets `response.status_code = 200` when the record already
    existed, so the API distinguishes "created" (`201`) from "already tracking"
    (`200`) without a second endpoint.
  - A body without a job keeps the original one-shot create path.

#### 3. Frontend API

- `frontend/src/api/applications.ts` — `createApplication` now returns
  `{ application, created }`, deriving `created` from `response.status === 201`.

### Part B — Server-owned `applied_at`

#### 4. Schema

- `backend/app/api/schemas.py`:
  - `ApplicationCreateRequest` and `ApplicationUpdateRequest` **drop**
    `applied_at`. The update validator now requires "at least one of status or
    notes". Because both models are `extra="forbid"`, a client that still sends
    a timestamp gets a `422`.

#### 5. Service

- `backend/app/services/applications.py`:
  - `_UPDATABLE_FIELDS` is now `{"status", "notes"}` — a service-level write to
    `applied_at` is a programming error.
  - New `_utcnow()`.
  - `create_application(..., now=None)` stamps `applied_at` **only** when the
    initial status is `applied`.
  - `update_application(..., now=None)` stamps `applied_at` on a transition
    **into** `applied` from any other status; preserves it on a no-op
    `applied → applied`, on a notes-only edit, and on any move **away** from
    `applied` (the submission is historical and must survive later stages).
  - `now` exists solely so tests can pin the clock.

#### 6. Frontend types

- `frontend/src/types/api.ts` — removed `applied_at` from
  `ApplicationCreateRequest` / `ApplicationUpdateRequest`; `Application` still
  reads `applied_at` from the response.

### Part C — Confirmed, honest workflow UI

#### 7. Pure helpers

- `frontend/src/utils/applicationWorkflow.ts` (new):
  - `APPLICATION_WORKFLOW_STEPS` (prepare → apply externally → record).
  - `requiresAppliedConfirmation(current, next)` — true exactly when moving
    into `applied` from anything else.
  - `hasRecordedSubmission(appliedAt)`.

#### 8. Components

- `frontend/src/components/job/AppliedConfirmation.tsx` (new) — inline
  (no modal) confirmation with a required checkbox; the confirm button stays
  disabled until it is ticked.
- `frontend/src/components/job/ApplicationWorkflowGuide.tsx` (new) — the
  three-step explanation plus an `rel="noopener noreferrer"` link to the
  original posting.
- `frontend/src/components/job/ApplicationCard.tsx` — intercepts a status
  change into `applied` behind `AppliedConfirmation`; the "Mark as applied"
  shortcut opens the same confirmation; shows the server-recorded applied time
  and states it is set by the server. `onMarkApplied` and the client-side
  `applied_at` update are gone.
- `frontend/src/pages/JobDetailPage.tsx` — removes the client-timestamp
  `appliedRun`; tracks via the new `createApplication` shape; renders the
  `ApplicationWorkflowGuide`.
- `frontend/src/pages/ApplicationsPage.tsx` — row-level `AppliedConfirmation`
  on the "Mark applied" button and on selecting Applied; "already track this
  job" messaging on a `200`; updated page description.

## Security / correctness notes

- Ownership is unchanged: the candidate comes from the session, and a foreign
  application id still answers as unknown (`404`). The new idempotent path keys
  on `(candidate_id, job_id)` from the session, never from the body.
- No new write path can set `applied_at` directly; the advisory lock is the
  only concurrency control and there is still no unique index on
  `applications(job_id)` (the runtime-style lock is the guarantee, matching the
  spec's "duplicate prevention is not a second mechanism" principle).
- No migration: the change is contract + behaviour only, so `alembic check`
  stays clean.

## Tests added

### Backend (+17)

- `backend/tests/api/test_api_applications.py` (+12): draft stays null; applied
  create stamps a timestamp; `applied_at` rejected on create and update;
  transition into applied stamps it; notes edit and repeated applied preserve
  it; leaving applied preserves it; advancing without applying keeps it null;
  partial update preserves job snapshot + `applied_at`; tracking twice yields
  one row (`201` then `200`); re-tracking never regresses an applied record.
- `backend/tests/api/test_api_assisted_application.py` (new, +4): generating a
  cover letter tracks nothing; reading a job records nothing; tracking after
  preparing materials is still a draft; marking applied is an explicit,
  separate step.
- `backend/tests/test_application_tracking_concurrency.py` (new, +1): two real
  PostgreSQL connections racing `track_application` produce one row and the same
  id, with the second blocking on the advisory lock.

### Frontend (+5)

- `frontend/tests/applicationWorkflow.test.ts`: `requiresAppliedConfirmation`
  (into applied yes, already applied no, away no), `hasRecordedSubmission`, and
  the workflow-step copy.

## Verification

Run from `backend/` with the venv active and from `frontend/`:

```powershell
# backend
.\.venv\Scripts\python.exe -m pytest -q          # 725 passed
.\.venv\Scripts\python.exe -m alembic check      # no new upgrade operations

# frontend
npm test                                         # 30 passed
npm run build                                    # tsc -b && vite build, clean
```

## Manual smoke test (no browser automation available)

1. Sign in and open a job; confirm the "How applying works here" guide links to
   the original posting in a new tab.
2. Track the job → a **Draft** application appears; `applied_at` is "—".
3. Track the same job again → no second record, an "already track this job"
   notice.
4. Choose **Applied** in the status selector → the confirmation checkbox
   appears; confirming records an applied time, cancelling changes nothing.
5. Add notes and save → the applied time is unchanged.
6. Move to **Interview** → the applied time is preserved.
7. On the applications page, repeat steps 3–6 with the "Mark applied" shortcut.

## Not in scope (unchanged)

- No browser automation, no auto-submit, no external application APIs.
- No schema/migration change; no new runtime dependency.
- Application tracking remains a private, single-user record over shared jobs.
