# System architecture

This document records current system boundaries and important implementation
invariants. The root [README](../README.md) is the product and setup guide.

`PROJECT_SPECIFICATION.md`, [Authentication Design](AUTHENTICATION_DESIGN.md),
[Multi-User Readiness Audit](MULTI_USER_READINESS_AUDIT.md), and dated phase
reports are historical records. They may describe proposals or earlier
snapshots. Current code and tests are the authority for behavior.

## System shape

- `frontend/` is a React and TypeScript single-page application built with Vite.
- `backend/app/` is a synchronous FastAPI application. It owns request
  validation, local profile selection, persistence, provider clients, and
  registered agent handlers.
- PostgreSQL is the supported database. SQLAlchemy models live in
  `backend/app/models/`; Alembic migrations live in `backend/alembic/`.
- External integrations are Google Gemini, Firecrawl, and SearXNG.
- There is no task broker or background worker. Agent endpoints run persisted
  invocations synchronously in the request.
- The Tauri desktop runtime supervises a local PostgreSQL cluster and FastAPI
  on a loopback address. Vite proxies to the loopback API in development.

## Local identity, API access, and data scope

There is no registration, login, password, session, logout, or user-auth API.
`GET /api/candidates/me` retrieves the local profile and initializes it on
first use. A fresh installation uses a stable candidate UUID, so restarts do
not create another profile. Candidate name and other profile fields can remain
empty until the user fills them in through the existing Profile page.

The candidate row is the local identity. Resume documents, applications,
matches, and candidate-specific agent runs retain their candidate foreign keys;
routes obtain the selected candidate server-side and do not trust a
caller-supplied candidate ID. Job discovery and verification quotas use durable
`local` and `global` scopes.

The desktop API binds to `127.0.0.1` and requires a per-launch bearer
capability. On Windows the Tauri process creates 32 random bytes with
`BCryptGenRandom` and encodes them as 64 lowercase hexadecimal characters.
It sends the capability to its backend child once in a JSON line on the
child's standard input. It is not placed in arguments, environment variables,
logs, URLs, or persistent configuration. The backend validates the startup
message and keeps the capability in memory for that process.

The `api_capability` Tauri command returns the capability only when the caller
is the `main` window at the packaged `http://tauri.localhost` origin (or the
approved Vite origin in development) and the backend is ready. The React
bootstrap installs it as an Axios `Authorization: Bearer` header before
mounting API pages. The frontend keeps it in memory; it does not use local or
session storage. Closing the window clears the native copy and shuts down the
backend. Each new Tauri launch creates a different capability, so a value from
an earlier run cannot authenticate to the restarted backend.

`ApiCapabilityMiddleware` requires the bearer value for every `/api` route,
unknown API route, and `/health` route. It compares valid values in constant
time and returns a generic `401` with `Cache-Control: no-store` when the
capability is absent or invalid. Desktop mode disables the interactive API
schema pages. Readiness supervision supplies the capability itself when
checking `/health/ready`; callers without it cannot use health responses as an
unauthenticated probe. The local origin middleware remains defense in depth:
unexpected browser `Origin` values and cross-site fetches without an Origin
are rejected. CORS has no wildcard or credentials support; the packaged
WebView's exact origin and the two Vite development origins are explicitly
allowed. PostgreSQL also binds only to loopback. In every mode the API rejects requests
whose `Host` is not `localhost`, `127.0.0.1`, or `::1`, so a DNS-rebound name
cannot reach it as same-origin.

This capability blocks direct API requests from callers that do not possess
the current value; loopback and origin checks alone do not establish caller
identity. It does not protect against malicious code in the trusted application
WebView, a same-user attacker able to inspect or interfere with the app
process, a local administrator, or compromised application binaries or
dependencies. It is not account authentication and does not isolate a hostile
process running as the same Windows user. The detailed desktop threat model
and verification limits are in [Windows installer and Stage 4B verification](WINDOWS_INSTALLER.md)
and [the Stage 4B report](STAGE4B_VERIFICATION.md).

`GET /health` and `GET /health/ready` are protected in desktop mode; readiness
also checks the database. API failures are mapped in
`backend/app/api/errors.py`.

## Database and persistence

`backend/app/db/base.py` defines the SQLAlchemy base, naming convention, UUID
primary-key and timestamp mixins, and controlled-vocabulary helpers. Enums are
stored as VARCHAR-backed values with database check constraints. PostgreSQL
behavior is intentional: the schema and agents use JSONB, partial unique
indexes, `ON CONFLICT`, advisory locks, and `SELECT FOR UPDATE SKIP LOCKED`.

Important current constraints and relationships:

- `Candidate.is_local_profile` identifies the selected profile. A partial
  unique index allows at most one selected row. Existing candidate IDs remain
  stable across the authentication-removal migration.
- `Company.normalized_name` and `Job.canonical_url` are unique. `job_url`
  preserves the discovered source URL.
- Jobs keep crawl status and vacancy availability separate. Failed or
  inconclusive checks do not establish that a job is closed.
- An `Application` belongs to one candidate; its job reference can be cleared
  while title/company snapshots preserve the application record.
- `Document` rows are append-only application evidence. Master-resume uploads
  link the new version to the previous one rather than changing old content.
- `AgentRun` uses a partial unique index on successful idempotency keys. Keys
  are global across agents and therefore remain namespaced. Job/candidate
  references use `SET NULL` so run history can survive subject deletion.
- `JobAnalysis` permits one successful result per `(job_id, prompt_version)`.
  `Match` records the resume document and analysis used.
- `DiscoveryDailyUsage` stores UTC-day operation counts by `local` or `global`
  scope so limits survive restarts and are shared by local processes.

Migration `20261115_0009_local_profile` marks a sole existing candidate as the
local profile. If multiple candidates exist, it preserves them and all related
records, marks none selected, and requires the user to choose explicitly with
`python -m app.cli.select_local_profile`. It does not merge profiles. Existing
per-account quota counts are summed into the local daily count. Once the
candidate foreign key is removed, the migration drops obsolete user and
session tables. The downgrade refuses to recreate removed password/session
data; restore the runtime's pre-upgrade database backup to return to the
previous schema. The migration is covered with fresh, representative, and
already-migrated isolated PostgreSQL databases.

When adding a model, export it from `backend/app/models/__init__.py`; Alembic
imports that module to populate metadata. Review generated migrations.

## Persisted agent runtime

The reusable runtime is in `backend/app/agents/runtime/`. It provides run
creation, claiming, execution, completion, retry-aware failure, cancellation,
and a deterministic state machine. Terminal statuses cannot restart.

- The runtime flushes but never commits. The API caller owns the transaction.
- A request claims only the run it created or resumed; it does not claim a
  different pending run.
- Retryable failures return to pending until `max_attempts`; terminal failures
  and duplicate-success errors do not retry.
- Run creation and completion serialize idempotency decisions with
  transaction-scoped PostgreSQL advisory locks. The partial unique index is
  the database backstop.
- Handler output must be JSON-serializable. Persisted failure messages are
  sanitized before being returned to the client.

`backend/app/api/agent_runner.py` commits after run creation, after claim, and
after execution. This keeps provider calls separate from persistence writes.

## Agent evidence and external services

Handlers are registered per app in `backend/app/main.py`; tests can inject fake
clients without sharing a global registry.

- Discovery is deterministic. Search results are pointers, not vacancy
  evidence. The source registry admits reviewed direct-job URLs; scraped page
  evidence is validated before a job is saved. An identical search reuses the
  run from the same UTC day and searches again on a later day. Replaying a
  succeeded run uses no daily discovery quota.
- Job Analysis reads saved job fields and asks Gemini for structured analysis.
- Job Matching verifies that the resume belongs to the local candidate and
  requires a usable saved job analysis. It records the evidence used and does
  not re-analyze the job.
- Cover Letter requires the saved job, profile, resume, match, and successful
  analysis. Generated text is a new document sourced from the master resume.

Analysis, matching, and cover-letter prompt versions are persisted with each
result. Bump the relevant prompt version when prompt behavior changes. Model
outputs are data, never control instructions, and generated claims must stay
grounded in persisted profile, job, and resume evidence. Scraped job text is
untrusted: each system instruction tells the model to treat evidence as data
and ignore instructions inside it, and the matching and cover-letter builders
collapse line breaks in one-line fields so a job title or company name cannot
start a forged evidence block.

## Resume handling

`backend/app/services/resume_upload.py` accepts TXT, Markdown, PDF, and DOCX up
to 2 MiB and extracts text locally. Scanned PDFs have no OCR support. Uploading
does not itself call Gemini. When resume-based Gemini features are enabled,
selected resume and profile evidence is sent to the configured provider for
matching or cover-letter actions. Keep the feature gate and server-side
credentials intact.

## Frontend and desktop boundary

The frontend keeps API calls in `frontend/src/api/`, types in
`frontend/src/types/`, and page state in hooks. Startup retrieves the local
profile and renders the dashboard directly; there is no authentication route
or redirect. The API client sends no cookies or CSRF tokens.

`backend/app/desktop_runtime/` supervises the managed PostgreSQL and FastAPI
processes. `frontend/src-tauri/` owns the Tauri WebView and supervisor
lifecycle. The shell waits for a validated ready message, assigns the runtime
API URL before the React app mounts, then requests graceful shutdown when the
window closes. Before an Alembic upgrade it stores a timestamped database
backup under the user's local data directory.

The packaged desktop WebView stays at `http://tauri.localhost`; its Axios
client calls the loopback API with the in-memory capability. Tauri development
uses the exact Vite origin and local CORS allowance. Browser development and
the desktop runtime use separate databases and configuration.

## Documentation map

- [Project overview and setup](../README.md)
- [Backend setup and migration notes](../backend/README.md)
- [Experimental desktop runtime](DESKTOP_RUNTIME.md)
- [Historical authentication design](AUTHENTICATION_DESIGN.md)
- [Historical multi-user audit](MULTI_USER_READINESS_AUDIT.md)
