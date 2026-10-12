# AGENTS.md

## Repo state

- Python backend + a React frontend in `frontend/` (Vite, TypeScript, Tailwind v4, react-router, Axios) — built from an empty dir, uncommitted. `docs/` is still an empty untracked dir.
- Current branch `feat/database-models` has **uncommitted work** (alembic scaffolding + the 3 models). `main` has almost nothing. Do not assume `main` reflects the codebase.
- `backend/.venv/` is checked into the working tree (gitignored) — Python **3.13**, though `PROJECT_SPECIFICATION.md` claims 3.12. Trust the venv.

## Commands

Everything must run **from `backend/`** — `app` is imported as a top-level package and `alembic.ini` uses `script_location = alembic` (both relative).

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload          # http://127.0.0.1:8000/health
alembic upgrade head
alembic revision --autogenerate -m "..."   # then ALWAYS review the generated file
alembic downgrade -1
alembic -x database_url="postgresql+psycopg://..." upgrade head   # one-off DB override, see env.py
```

No `pyproject.toml`, no linter/formatter config, no CI, no `.github/`. `backend/requirements.txt` is the only dependency manifest — adding a dep means editing it (and reinstalling) manually.

Frontend commands run **from `frontend/`**:

```powershell
cd frontend
npm install          # first time / after changing package.json
npm run dev          # Vite dev server, http://localhost:5173
npm run build        # tsc -b && vite build — the type check, run this before claiming done
npm run preview      # serve the production build
```

There is **no frontend test framework** — `npm run build` (strict TS: `noUnusedLocals`, `verbatimModuleSyntax`) is the frontend gate. Backend behavior is covered by pytest.

## Tests

- **pytest** is the test framework. The suite lives under `backend/tests/` (`conftest.py`, `factories.py`, `fake_clients.py`, plus one module per model/area); `backend/tests/api/` holds the HTTP suite (`conftest.py`, `support.py`, one module per endpoint group).
- Current suite: **626 tests, all passing.** Run them **from `backend/`** with the venv active: `pytest` (configured by `backend/pytest.ini`, `testpaths = tests`, `pythonpath = .`).
- Tests run against a real **PostgreSQL** database, not SQLite. The existing container is **`career-copilot-db`** (`postgres:16-alpine`, port 5432) — reuse it; don't add another DB service. Fixtures create/drop throwaway databases with a `_test` / `_test_migrated` suffix, so they never touch `career_copilot`.
- Schema is managed with **Alembic**, also run from `backend/`. `tests/test_migrations.py` upgrades a scratch database, runs a downgrade/upgrade round trip and diffs migration output against the models — so `alembic check` and the test suite must both stay green after any model or migration edit.

## Config & env

- Settings live in `backend/app/core/config.py`, prefix `CAREER_COPILOT_`, `env_file=".env"` resolved **relative to CWD** → `.env` must be in `backend/`.
- **Two conflicting `.env.example` files.** `backend/.env.example` is current (`CAREER_COPILOT_*`, Postgres URL, Gemini). The **root `.env.example` is stale** (`GEMINI_API_KEY`, `DATABASE_URL=sqlite`, `APP_ENV`, `PLAYWRIGHT_*`) and matches an abandoned direction. Use the backend one.
- Gemini: `CAREER_COPILOT_GEMINI_API_KEY` (optional — the SDK client is built lazily, so the app boots and tests run without it) and `CAREER_COPILOT_GEMINI_MODEL` (default `gemini-3.8-flash`). Never hardcode keys or print them.
- CORS: `CAREER_COPILOT_CORS_ORIGINS` — a JSON list of allowed origins, default `["http://localhost:5173", "http://127.0.0.1:5173"]`; `create_app()` wires `CORSMiddleware` only when the list is non-empty (empty list disables CORS entirely). Credentials are never allowed.
- The frontend reads **`VITE_API_BASE_URL`** (default `http://127.0.0.1:8000`) — see `frontend/.env.example`. Vite inlines it at build time; it is not a `CAREER_COPILOT_*` setting.
- `get_settings()` is `@lru_cache`d and `app/db/session.py` calls it at **module import time** to build the engine. Changing settings in-process requires `get_settings.cache_clear()` and a fresh engine.

## Database

- Postgres only now (`postgresql+psycopg`, psycopg3). `_connect_args()` in `session.py` still has a SQLite branch and `backend/README.md` still shows a SQLite URL — both vestigial, ignore them.
- `app/db/base.py` is the extension point: `Base`, `naming_convention`, `UUIDPrimaryKeyMixin`, `TimestampMixin`. Model base-class order is `UUIDPrimaryKeyMixin, TimestampMixin, Base`.
- Alembic `env.py` gets its metadata via `import app.models` (`backend/app/models/__init__.py`). **A new model not exported there is invisible to autogenerate and will be proposed as a `DROP TABLE`.** Always add new models to that `__init__`.
- Migration revision IDs are date-prefixed (`20260727_0001`).
- Model/migration are hand-maintained in parallel — after editing a model, generate a revision and diff it against the model by hand; they drift silently.

## Current models (`backend/app/models/`)

Two migrations: `20260727_0001_create_core_tables` (Company, Job, Application) and `20261003_0002_align_schema_with_agentic_architecture` (Candidate, AgentRun, JobAnalysis, Document, Match). All UUID PKs; cascade deletes where noted below.

- `Company` → `Job` → `Application`. `Job.canonical_url` is `unique` — discovery's dedupe/upsert key. `Job.job_url` stores the raw search-result URL and is also `unique`; `canonicalize_url` strips `www.`, tracking params and fragments so equivalent listings collapse onto one row.
- `Job.salary_min_ctc` / `salary_max_ctc` / `salary_text` exist but **salary and currency filtering were removed from scope**. Don't build on them; expect them dropped.
- `Application.job_id` is **not** unique (a job may be applied to more than once); it stores `resume_version` (a plain string) and `cover_letter` as loose `Text`. `Job.experience_level` is an unconstrained `String`, not an enum.
- `JobAnalysis`: `job_id` FK CASCADE + partial unique index `uq_job_analyses_job_prompt_succeeded` on `(job_id, prompt_version) WHERE status = 'succeeded'`; `status` is a VARCHAR-backed `RunStatus`; `analysis` is JSONB declared `JSONB(none_as_null=True)` so Python `None` binds as SQL NULL (otherwise SQLAlchemy writes `jsonb 'null'`, which `IS NOT NULL` matches and the matching agent's "usable payload" filter would miss it); `model`/`prompt_version` strings.
- `AgentRun`: partial unique index on `idempotency_key WHERE status = 'succeeded'` (global across agents — keys must be namespaced); claim index on `(status, created_at)`; optional `job_id`/`candidate_id` FKs are `SET NULL` so runs survive their subject.
- `Candidate` has **no** uniqueness on `name` (multiple profiles of the same person are allowed). `Document` kinds are `master_resume` / `tailored_resume` / `cover_letter` (`DocumentKind`); source + job/application/run links are `SET NULL`. **Master resumes are immutable source evidence:** an upload never updates a row — it appends a new `master_resume` document chained to the previous one's `source_document_id`, so every version stays comparable. `Match` has mandatory `resume_document_id` + `job_analysis_id` (scores must be reproducible), `Numeric(5,2)` score 0–100, and a unique `(job_id, resume_document_id, prompt_version)` index.
- Enums in `enums.py` are `StrEnum`s enforced by DB check constraints (`vocabulary_check`): `CrawlStatus`, `RunStatus`, `DocumentKind`, `ApplicationStatus`.

## Agent runtime (`backend/app/agents/runtime/`)

A deterministic, reusable lifecycle manager for the `agent_runs` table. No LLM, no queue, no worker framework — `AgentRuntime(session, ...)` is a plain service the caller drives.

- **Modules:** `exceptions.py`, `state.py` (transition table), `registry.py` (handler registry), `context.py` (`RunContext`), `runtime.py` (`AgentRuntime`). Public names re-exported from `app.agents.runtime`.
- **Lifecycle:** `create_run` → `claim_next` → `execute` → `complete` / `fail` (retry-aware) / `cancel`; `run_next` = claim + execute. The runtime **flushes but never commits** — the caller owns the transaction.
- **State machine:** `state.py` `ALLOWED_TRANSITIONS` over the `RunStatus` vocabulary (`pending, running, succeeded, failed, cancelled`). Every status write goes through `apply_transition`; terminal runs can never restart.
- **Claiming:** `SELECT ... FOR UPDATE SKIP LOCKED` ordered by `created_at, id`. Sets `running`; `started_at` only on the first claim; `attempt_count` increments on every claim after the first (attempt = 1-based ordinal of the current attempt).
- **Retry policy:** `fail(run, error, retryable=True)` → back to `pending` while `retryable and attempt_count < max_attempts`, else terminal `failed`. `DuplicateSuccessError` is always non-retryable.
- **Idempotency:** a transaction-scoped advisory lock (`pg_advisory_xact_lock` over `agent_run:idempotency` + key) is taken by **both** `create_run` and `complete`. `create_run` then returns the existing run (succeeded > oldest running > oldest pending; failed/cancelled keys get a fresh run). `complete` re-checks under the lock and settles a duplicate as `failed` instead of racing the partial unique index (`uq_agent_runs_idempotency_succeeded`), which remains the backstop for writers that bypass the runtime.
- **Handlers:** registered on a `HandlerRegistry` (`register_handler`); output must be a `Mapping` and JSON round-trip cleanly (`allow_nan=False`). Handler exceptions are logged, the full traceback is written to `agent_runs.error`, and `execute` returns the run rather than raising. `execute` raises `InvalidRunStateError` if the run is not `running`.
- **Do not use `session.begin_nested()` for error recovery here.** In SQLAlchemy 2.0.51 a failed flush inside `begin_nested()` deactivates the parent `SessionTransaction` (`PendingRollbackError` on next use) — savepoints cannot back a catch-and-continue path. The advisory-lock re-check pattern above avoids the need.
- **Tests:** `tests/test_agent_runtime.py` (lifecycle, transitions, idempotency, retry, handler execution, cancellation) and `tests/test_agent_runtime_concurrency.py` (SKIP LOCKED exclusivity, concurrent duplicate creation, completion-vs-cancellation race).

## Discovery Agent (`backend/app/agents/discovery/`)

The first product agent, registered on the runtime as `job_discovery`. Fully deterministic — no LLM anywhere in it.

- **Modules:** `contract.py` (`DiscoveryRequest`, `DiscoveryResult`, `DiscoveryInputError`, `discovery_idempotency_key`), `query.py` (`build_search_query` → `"{role} {location} jobs {experience}"`), `extract.py` (pure extraction/filtering), `agent.py` (`DiscoveryAgent`, `register_discovery_handler`). Public names re-exported from `app.agents.discovery`.
- **Handler contract:** `handle(payload, context)` validates via `DiscoveryRequest.from_payload` (trim, collapse whitespace, reject blank/missing/extra fields → `DiscoveryInputError`; the runtime treats it as a normal retryable failure). `discover(session, request)` is the direct entry point used by tests.
- **Two strict phases:** phase 1 validates + searches + canonicalizes/dedupes candidate URLs; phase 2 crawls every candidate and extracts/filters locally. **No database work happens during any HTTP call** — the runtime's open transaction stays idle (no reads, no writes, no autoflush). Phase 3 persists everything.
- **Persistence is upsert-only:** `pg_insert().on_conflict_do_update()` on `companies.normalized_name` (self-assign `SET name = companies.name` returns the surviving id) and `jobs.canonical_url` (`COALESCE(EXCLUDED.x, jobs.x)` for optional columns, `RETURNING (xmax = 0) AS inserted` distinguishes create vs update, `first_seen_at` preserved, `last_seen_at`/`updated_at` refreshed, `crawled_at` advances only on a successful crawl, latest attempt wins `crawl_status`). Never SELECT-then-INSERT, never `begin_nested()`, never a second migration.
- **Crawl semantics:** success → `crawl_status='scraped'` + `crawled_at`; failure or empty content → `failed` with `crawled_at` untouched (never fakes success) while the job still persists from search-derived fields. Candidate-level problems (crawl error, malformed/overlong URL, filter rejection) land in `errors[]` or the skip counters and never abort the run; only a total SearXNG failure raises, which the runtime retries.
- **Output:** `DiscoveryResult` dumped with `model_dump(mode="json")` — 8 keys `query, searched, candidates_found, jobs_created, jobs_updated, jobs_skipped, errors, job_ids`; invariant `created + updated + skipped == candidates_found`.
- **Idempotency key:** `job_discovery:{role}:{location}:{experience}` after trim/collapse/casefold — `discovery_idempotency_key(request)`.
- **Filtering** (`extract.py`): role = non-stopword token overlap with the title; location = token overlap only when the job provided one; experience = `parse_year_range` overlap only when both sides parse (`"1-3 years"`, `"5+"`, `"2 years"`). Unknown fields never reject. **Limitation:** token overlap only — `"Bengaluru"` does not match `"Bangalore"`.
- **Wiring:** `create_app()` builds a **fresh `HandlerRegistry` per app** at `app.state.agent_registry` and registers all four agents (`register_discovery_handler`, `register_job_analysis_handler`, `register_job_matching_handler`, `register_cover_letter_handler`); the process-wide `default_registry` is never touched. Without injected clients the factory builds settings-derived `SearXNGClient`/`FirecrawlClient` from `CAREER_COPILOT_SEARXNG_BASE_URL` / `CAREER_COPILOT_FIRECRAWL_*`.
- **Tests:** `tests/test_discovery_agent.py` (contract, extraction, filtering, DB behavior), `tests/test_discovery_runtime.py` (full create→claim→execute→complete, retry exhaustion, app-factory registry isolation, concurrent upserts against committed rows).

## Job Analysis Agent (`backend/app/agents/job_analysis/`)

The second product agent, registered on the runtime as `job_analysis`. Everything around Gemini (loading, validation, persistence) is deterministic — Job Matching calls Gemini the same way.

- **Modules:** `contract.py` (`JobAnalysisRequest`, `JobAnalysisResult`, `JobAnalysisInputError`, `JobNotFoundError`, `InsufficientJobContentError`, `job_analysis_idempotency_key`), `prompt.py` (`PROMPT_VERSION = "job-analysis-v1"`, `SYSTEM_INSTRUCTION`, `build_analysis_prompt`), `agent.py` (`JobAnalysisAgent`, `persist_analysis`, `register_job_analysis_handler`). Public names re-exported from `app.agents.job_analysis`.
- **Handler contract:** `handle(payload, context)` validates via `JobAnalysisRequest.from_payload` (single `job_id`, nil-UUID rejected, extra fields rejected → `JobAnalysisInputError`); `analyze(session, job_id)` is the direct entry point used by tests.
- **Execution order:** short-circuit on an existing `succeeded` row for `(job_id, prompt_version)` → `session.get(Job)` (`JobNotFoundError`) → reject blank title/description (`InsufficientJobContentError`, never sends junk to Gemini) → load `job.company` → build prompt → one Gemini call → persist. **All DB reads happen before the API call; the single write happens after** — the runtime's open transaction stays idle during Gemini (same honest pattern as Discovery; still no `begin_nested()`).
- **`JobAnalysisResult` is the structured-output schema** handed to Gemini (`extra="forbid"`, `summary` min length 1, `confidence` 0–1, cross-validated experience range, defaulted lists, nullable scalars). It deliberately **omits** `title`/`company`/`location`/`employment_type` — those are deterministic `Job` columns and LLM output must never restate them.
- **Persistence:** one `pg_insert(JobAnalysis)` with `status=RunStatus.succeeded` and `on_conflict_do_nothing(index_elements=[job_id, prompt_version], index_where=status == succeeded)` (note: `index_where`, not `where` — PostgreSQL partial-index conflict target). First writer wins; a conflicted duplicate falls back to SELECTing the stored winner. Nothing is written on any failure path — failed analyses never pollute the table.
- **Output envelope:** `{"job_id", "prompt_version", "model", "analysis"}` on both the fresh and short-circuit paths, so callers cannot tell which happened (determinism).
- **Idempotency key:** `job_analysis:{job_id}:{prompt_version}` — `job_analysis_idempotency_key(job_id, prompt_version)`.
- **Versioning:** prompt changes **must** bump `PROMPT_VERSION` (the constant is the contract); a new version creates a new run + a new row and never touches history; repeat analysis at the same version short-circuits with zero Gemini calls.
- **LLM output is data only:** it never controls retries, transitions or commits — validation failures surface as ordinary exceptions and the runtime applies its standard retry/failure policy (`JobNotFoundError`/`InsufficientJobContentError`/`JobAnalysisInputError` and `GeminiError` all retry up to `max_attempts`).
- **Wiring:** `register_job_analysis_handler(registry, *, gemini=None, prompt_version=..., agent_name=...)` — without an injected client it builds a settings-derived `GeminiClient` (lazy SDK, no credentials needed at boot). `create_app()` registers all four agents on the per-app registry.
- **Tests:** `tests/test_gemini_client.py` (15 tests driving the real client through its injected `api` seam — parsed path, text-JSON fallback, malformed/empty/schema failures, config assertions), `tests/test_job_analysis_agent.py` (contract, prompt, persistence, short-circuit, versioning, conflict), `tests/test_job_analysis_runtime.py` (full lifecycle, retry exhaustion, registry isolation). **No test opens a socket or needs `CAREER_COPILOT_GEMINI_API_KEY`.**

## Job Matching Agent (`backend/app/agents/job_matching/`)

The third product agent, registered on the runtime as `job_matching`. Evaluates candidate↔job fit from persisted evidence only.

- **Modules:** `contract.py` (`JobMatchingRequest`, `MatchResult`, `MatchEnvelope`, `MatchVerdict`, `MatchSignal`, the input/precondition errors, `job_matching_idempotency_key`), `prompt.py` (`PROMPT_VERSION = "job-matching-v1"`, `SYSTEM_INSTRUCTION`, `build_matching_prompt`), `agent.py` (`JobMatchingAgent`, `persist_match`, `register_job_matching_handler`). Public names re-exported from `app.agents.job_matching`.
- **Handler contract:** `handle(payload, context)` validates via `JobMatchingRequest.from_payload` (three UUIDs — job, candidate, resume document; nil UUIDs and extra fields rejected → `JobMatchingInputError`); `match(session, *, job_id, candidate_id, resume_document_id)` is the direct entry point used by tests.
- **Preconditions, in order:** job exists (`JobNotFoundError`) → candidate exists (`CandidateNotFoundError`) → document exists (`ResumeDocumentNotFoundError`) → document belongs to the candidate, is a `master_resume`/`tailored_resume`, and has non-blank content (`InvalidResumeDocumentError`) → candidate has at least one of summary/skills/headline/seniority/years (`InsufficientCandidateProfileError`; `name` + `location` alone are not evidence). All deterministic — checked before any Gemini call.
- **Analysis coupling:** short-circuits on an existing `matches` row for `(job_id, resume_document_id, prompt_version)`, else selects the **newest succeeded `JobAnalysis` with a non-NULL payload** (`created_at DESC, id DESC`) and stores its id in `job_analysis_id`. Never re-analyses a job, never mixes versions; missing/usable analysis → `JobAnalysisNotFoundError`.
- **Prompt:** `build_matching_prompt(...)` renders JOB EVIDENCE (job columns + `json.dumps` of the analysis) then CANDIDATE EVIDENCE (profile fields + the resume document's kind and content). Raw `Job.description` and URLs are deliberately excluded — the analysis payload is the distilled version.
- **Output:** `MatchEnvelope` (`job_id, candidate_id, resume_document_id, job_analysis_id, prompt_version, model, match`) dumped `model_dump(mode="json")`; `match` is `MatchResult` (`score` 0–100 rejected not clamped, `verdict` vocabulary, tri-state `*_match` signals with `unclear` for absent evidence, defaulted lists, `rationale` stripped of blankness, `confidence` 0–1). Fresh and short-circuit paths return byte-identical envelopes.
- **Persistence:** one `pg_insert(Match)` with `on_conflict_do_nothing(index_elements=[job_id, resume_document_id, prompt_version])` — **no `index_where`** (plain unique index, unlike `job_analyses`). `score` goes to the `Numeric(5,2)` column; `verdict` holds the JSONB detail (everything but the score). Conflict → SELECT the stored winner; `RuntimeError` only if the row vanished. Nothing written on any failure path.
- **Idempotency key:** `job_matching:{job_id}:{candidate_id}:{resume_document_id}:{prompt_version}` — `job_matching_idempotency_key(...)`.
- **LLM output is data only** — runtime applies the standard retry policy (`JobMatchingInputError`/`JobNotFoundError`/precondition errors/`GeminiError` all retry to `max_attempts`).
- **Wiring:** `register_job_matching_handler(registry, *, gemini=None, prompt_version=..., agent_name=...)`; `create_app()` registers all four agents on the per-app registry.
- **Tests:** `tests/test_job_matching_agent.py` (contract, prompt, preconditions, analysis selection, persistence/conflict, real-client seam), `tests/test_job_matching_runtime.py` (full lifecycle, retry exhaustion, prompt-version second row, app-factory registry isolation, concurrent duplicate matching → one row). No socket, no credential.

## Cover Letter Agent (`backend/app/agents/cover_letter/`)

The fourth product agent, registered on the runtime as `cover_letter`. Writes a job-specific cover letter from evidence that is already persisted — job, analysis, `Match`, and the uploaded master resume. It never invents claims outside that evidence and never re-runs Job Matching.

- **Modules:** `contract.py` (`CoverLetterRequest`, `CoverLetterResult`, `CoverLetterEnvelope`, the input/precondition errors, `cover_letter_idempotency_key`, `COVER_LETTER_AGENT_NAME = "cover_letter"`), `prompt.py` (`PROMPT_VERSION = "cover-letter-v1"`, `SYSTEM_INSTRUCTION`, `build_cover_letter_prompt`), `agent.py` (`CoverLetterAgent`, `persist_cover_letter`, `register_cover_letter_handler`). Public names re-exported from `app.agents.cover_letter`.
- **Handler contract:** `handle(payload, context)` validates via `CoverLetterRequest.from_payload` (four UUIDs — job, candidate, master resume document, match; nil UUIDs and extra fields rejected → `CoverLetterInputError`); `generate(session, *, job_id, candidate_id, master_resume_document_id, match_id)` is the direct entry point used by tests.
- **Preconditions, in order:** job exists (`JobNotFoundError`) → candidate exists (`CandidateNotFoundError`) → document exists / belongs to the candidate / `kind == master_resume` / non-blank content (`MasterResumeNotFoundError`, `InvalidMasterResumeError`) → match exists and its `job_id`/`candidate_id`/`resume_document_id` all agree with the request, and `match.verdict` re-validates as a `MatchResult` (`MatchNotFoundError`, `InvalidMatchError`) → `match.job_analysis_id` resolves to a `JobAnalysis` whose `analysis.job_id == job_id`, `status == succeeded` and payload is not `None` (`JobAnalysisNotFoundError`). All deterministic and checked before any Gemini call — a missing/mismatched prerequisite fails with **zero** documents written.
- **Match reuse:** the prompt is built from the stored `Match` (`score`, `verdict`, matched/missing required and preferred skills, strengths, gaps, rationale, confidence) — imported `from app.agents.job_matching.contract import MatchResult`. The `*_match` tri-state signals, `job_analysis_id`, `resume_document_id`, raw `Job.description` and every URL are deliberately excluded.
- **Prompt:** `build_cover_letter_prompt(...)` renders JOB EVIDENCE (job columns + `json.dumps` of the analysis payload) then CANDIDATE EVIDENCE (candidate profile fields + the master resume's content); the system instruction enforces grounding ("grounded only in the evidence above", missing-evidence hedging, no invented employers/dates/metrics, no generic filler) and requires only `{content, confidence}` back.
- **Output:** `CoverLetterEnvelope` (`job_id, candidate_id, master_resume_document_id, match_id, document_id, prompt_version, model, cover_letter`) dumped `model_dump(mode="json")`; `cover_letter` is `CoverLetterResult` (`content` non-blank and stripped, `confidence` 0–1 rejected not clamped). Deterministic — there is no short-circuit, so a repeat execution regenerates the letter content but always returns this same envelope shape.
- **Persistence:** one `Document` of `kind=cover_letter` via `session.add` + `flush` + `refresh` — `source_document_id` = the master resume (so Job Matching's document and this letter are chained), `application_id=None` (a letter is never auto-attached to an application row), `agent_run_id=context.run.id`, `content`, `model`, `prompt_version`; `job_id`/`candidate_id` carry over. `flush` only, never a commit, never `begin_nested()`. Nothing is written on any failure path.
- **Idempotency key:** `cover_letter:{job_id}:{candidate_id}:{master_resume_document_id}:{match_id}:{prompt_version}` — `cover_letter_idempotency_key(...)`. The `match_id` segment exists because `matches` is unique only on `(job_id, resume_document_id, prompt_version)`, so a different match must be a different key.
- **Duplicate prevention is the runtime's job only** (spec §14 — do not build a second mechanism): `AgentRuntime.create_run` + the `uq_agent_runs_idempotency_succeeded` partial index make a repeated request return the existing succeeded run with its stored envelope, so no second letter is generated. The agent itself has **no** short-circuit, which is consistent with the `Document` model's append-only history ("a regenerated ... cover letter is a new row pointing at the same `source_document_id`"). Consequence to remember: `documents` has no uniqueness constraint, so an out-of-band writer that bypasses the runtime can append extra letters — there is no DB backstop (unlike `job_analyses`/`matches`), and no migration was added for one.
- **LLM output is data only** — the runtime applies the standard retry policy (`CoverLetterInputError`/`JobNotFoundError`/prerequisite errors/`GeminiError` all retry to `max_attempts`, writing the traceback to `agent_runs.error`).
- **Wiring:** `register_cover_letter_handler(registry, *, gemini=None, prompt_version=..., agent_name=COVER_LETTER_AGENT_NAME)`; `create_app()` registers all four agents on the per-app registry.
- **Versioning:** bumping `PROMPT_VERSION` creates a new run (new idempotency key) and appends a second letter row; the v1 letter is never modified.
- **Tests:** `tests/test_cover_letter_agent.py` (contract, result schema, prompt grounding rules, registration, every precondition, persistence/envelope, regenerate-appends, no AgentRun on the direct path), `tests/test_cover_letter_runtime.py` (full create→claim→execute→complete, one-shot `run_next`, duplicate run reuse + stored-envelope reuse, invalid payload failure, retry-then-fail with zero documents, Gemini error retries, prompt-version second row, app-factory registry isolation, no `Application` created). No socket, no credential.

## Master Resume Upload (`backend/app/services/resume_upload.py`)

Resumes are **user-uploaded** — there is **no Resume Agent**. Uploading is deterministic application functionality, not an agent task: the service never calls Gemini and never creates an `AgentRun`.

- **Entry point:** `upload_master_resume(session, *, candidate_id, content, filename=None, content_type=None, max_bytes=MAX_RESUME_BYTES)` → `ResumeUploadResult` (`candidate_id, document_id, kind, filename, created_at`). Validates, extracts normalized text, then appends one `Document` of kind `master_resume`. Flushes, never commits — the caller owns the transaction.
- **Candidate association:** the candidate must already exist (`CandidateNotFoundError`); single-user, so no auto-create, no auth/multitenancy. Ownership is `Document.candidate_id`.
- **Formats (no new dependencies):** text only — `text/plain` and `text/markdown` (or an undeclared type whose bytes decode as UTF-8; `utf-8-sig` BOM stripped, `\r\n`→`\n`, stripped). Bytes are sniffed (`%PDF-`, zip `PK\x03\x04`) so a binary resume is **rejected, never stored as text** (`UnsupportedResumeFormatError`). PDF/DOCX extraction stays out of scope — the upload endpoint is text-only; minimum deps would be `pypdf` + `python-docx`.
- **Deterministic validation, in order:** candidate exists → non-empty bytes → non-blank filename if given → size ≤ `max_bytes` (default 2 MiB, `OversizedResumeError`) → extract → extracted text non-blank (`EmptyResumeError`).
- **Immutability/versioning:** a new upload appends a fresh row whose `source_document_id` points at the previous master resume (newest by `created_at DESC, id DESC`); the old row is never touched. `kind='master_resume'`, `model`/`prompt_version`/`job_id`/`agent_run_id` all null on uploaded rows. Filename is echoed in the result but **not persisted** (no column; deliberately no migration).
- **Consumption:** Job Matching reads the uploaded master resume unchanged — `tests/test_resume_upload.py` proves `JobMatchingAgent.match(...)` returns a `MatchEnvelope` from an uploaded document.
- **Tests:** `tests/test_resume_upload.py` (missing candidate, empty/oversized/unsupported/binary content, blank filename, persistence + metadata, second-upload immutability/version chain, no AgentRun, Job Matching compatibility).

## HTTP API layer (`backend/app/api/`)

The product workflow surface: FastAPI routers under `/api/` exposing candidates, resume upload, jobs, the four agents and application tracking. **Synchronous and single-user** — no auth, no queue, no background worker: an agent endpoint creates, claims, executes and completes its run inside the request and returns the finished `AgentRun`. CORS is enabled for the Vite dev origins (see Config & env).

- **Modules:** `schemas.py` (request/response models), `errors.py` (`recorded_failure`, `register_exception_handlers`), `deps.py` (`get_registry`, `get_current_candidate`, the `NO_CANDIDATE_DETAIL`/`NO_MASTER_RESUME_DETAIL` constants), `agent_runner.py` (`run_agent_now`, `agent_run_out`, `agent_run_response`), `routes/{candidates,resumes,jobs,agents,applications}.py`, mounted by `app/api/router.py` (health first).
- **Endpoints:** `GET/POST /api/candidates`, `GET /api/candidates/me`; `POST/GET /api/resumes/master`, `GET /api/resumes/master/history`; `GET /api/jobs` (`location`, `experience_level`, `is_active`, `source`, `limit` 1–100, `offset`) and `GET /api/jobs/{job_id}`; `POST /api/jobs/discover`, `POST /api/jobs/{job_id}/analyze`, `/match`, `/cover-letter`; `POST/GET /api/applications`, `GET/PATCH /api/applications/{id}` (PATCH is partial via `model_fields_set` — `{}`, a null value and unknown fields are all 422). `GET /health` stays outside `/api`.
- **`JobOut.company_name`** is a route-populated denormalized field (`_job_out()` in `routes/jobs.py`; the list query eager-loads `Job.company` via `selectinload`). Jobs are joined to companies only through the response schema — the frontend never fetches a company separately.
- **Transactions:** `get_db()` only closes, so **routes commit explicitly**; `run_agent_now` commits internally at three points (after `create_run`, after the claim, after `execute`). Never `begin_nested()`.
- **Route preconditions** resolve the single-user defaults *before* a run exists: an explicit id must exist (404), otherwise the current candidate (404), the current master resume (409) and — for a letter — the newest `Match` for job+candidate+resume (409 pointing at `POST /api/jobs/{id}/match`). Ownership/kind/analysis checks remain the agents' preconditions and surface as run failures, so a bad request never burns a Gemini call it cannot use.
- **Error mapping (`errors.py`):** a recorded run failure maps by the exception-class name appearing **last** in `agent_runs.error` to 400/404/409/503 as `{"detail": {"message", "run"}}` (the run row is included); unrecognized or empty failures become the opaque `{"detail": "Internal server error"}`. Domain exceptions raised directly in a route (upload validation, duplicate candidate, not-found) get their own handlers (413/415/400/404/409) as `{"detail": "<message>"}`; everything else falls to the catch-all 500. **Starlette's `ServerErrorMiddleware` re-raises after sending the 500**, so a test asserting that body needs `TestClient(..., raise_server_exceptions=False)` (the `make_client` seam exposes it).
- **Uploads:** `POST /api/resumes/master` is multipart (`python-multipart` added to `requirements.txt`), reads at most `MAX_RESUME_BYTES + 1` bytes into memory, and stays text-only — PDF/DOCX still fail as 415.
- **Wiring:** `create_app(*, searxng=None, firecrawl=None, gemini=None)` injects the three clients and registers all four handlers on the per-app registry, so an injected fake reaches the agent without touching settings.
- **Tests:** `tests/api/` — 79 tests over `support.py` builders (`build_job`, `build_full_world`, `add_analysis`, `add_match`) plus `SchemaScriptedGeminiClient` (answers by schema type, so one client drives analysis → matching → cover letter). Covers every endpoint group, the error shapes, CORS (`tests/api/test_api_cors.py`), the `company_name` field, and two full workflows: profile → resume → discover → analyze → match → letter → application, and an idempotent re-run (same run ids, same outputs, counts unchanged). The suite overrides `app.dependency_overrides[get_db]` with the savepoint session and **opens no socket**.

## Clients (`backend/app/clients/`)

- `searxng.py`: `SearXNGClient.search()` → `GET {base}/search?q=...&format=json&pageno=N`. Strict container validation (`results` list required), malformed per-entry hits dropped, `SearXNGError` on transport/HTTP/JSON/shape failure. `build_search_url` / `parse_search_response` are pure and testable without a client.
- `firecrawl.py`: `FirecrawlClient.scrape()` → `POST {base}/v2/scrape` with `{"url", "formats": ["markdown"]}`, optional `Authorization: Bearer`, envelope `{success, data:{markdown|content|text, metadata}}`. `FirecrawlError` on transport/HTTP/JSON/`success=false`/bad shape. `parse_scrape_response` is pure.
- `gemini.py`: `GeminiClient(model, *, api_key=None, api=None)` → `client.models.generate_content(model=..., contents=prompt, config=GenerateContentConfig(system_instruction=..., response_mime_type="application/json", response_schema=Schema))`, returning a validated Pydantic instance or raising `GeminiError(RuntimeError)`. **The SDK client is built lazily on first call** — construction never needs credentials, so `create_app()` and all tests boot keyless. The SDK silently swallows schema violations (leaving `.text` raw), so the client re-validates `.text` itself. `DEFAULT_MODEL = "gemini-3.8-flash"`. An injectable `api` seam (anything with `generate_content`) lets `tests/test_gemini_client.py` run fully offline — the suite never imports a real SDK client.
- SearXNG/Firecrawl accept an injectable `httpx.BaseTransport` — tests drive them with `httpx.MockTransport`, so **the suite never opens a socket** and needs no live SearXNG/Firecrawl. `httpx>=0.27,<1.0` and `google-genai>=1.0.0,<2.0.0` are in `requirements.txt`.
- Settings default to `http://localhost:8080` / `http://localhost:3002` (leftover containers `retrieval-searxng`, `retrieval-firecrawl-api-1` from another project) — nothing in the tests connects to them.

## Frontend (`frontend/`)

A Vite + React 19 + TypeScript + Tailwind v4 SPA talking **only** to the FastAPI backend. Single-user, no auth, no state-management library — plain hooks + Axios.

- **Commands:** see `## Commands` (`npm run dev`, `npm run build`). `npm run build` (`tsc -b && vite build`) is the only gate — there is no test runner or linter.
- **Stack:** `react-router-dom` v7 (BrowserRouter in `main.tsx`, layout route with `<Outlet/>`), `axios`, Tailwind v4 via `@tailwindcss/vite` (`@import "tailwindcss";` in `src/index.css`, no config file), no CSS-in-JS, no component library.
- **API access:** every call goes through `src/api/client.ts` (base URL `import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000"`); `src/api/errors.ts` converts any failure into an `ApiError` with a humanized `message` (handles string detail, `{message, run}` run failures, and FastAPI 422 arrays) — pages show `ErrorAlert`, they never parse raw responses. One module per endpoint group in `src/api/`.
- **Types:** `src/types/api.ts` mirrors the backend contract (`AgentRun`, `DiscoveryOutput`, `JobAnalysisOutput`, `MatchEnvelope`, `CoverLetterEnvelope`, `APPLICATION_STATUSES`, …). Frontend type-only imports must use `import type`.
- **Flow:** `CandidateProvider` (`src/hooks/useCandidate.tsx`) calls `GET /api/candidates/me` once; `404` → `OnboardingPage`, otherwise `<Routes>` under `AppLayout` (sidebar shell). Routes: `/` dashboard, `/jobs`, `/jobs/:jobId`, `/applications`, `/resume`, `/profile`, `*`.
- **Job detail page** wires the four agent endpoints (analyze → match → cover letter) plus application tracking; each card also renders the recorded `AgentRun` error and recovery action. Agent calls are synchronous — the UI disables the buttons while a run is in flight.
- **Resume upload** sends `FormData` with the content type forced client-side (`.md`/`.markdown` → `text/markdown`, else `text/plain`) so the backend's byte sniffing returns honest 413/415 errors; client-side checks are only size (2 MiB) and empty file. PDF/DOCX are still rejected — no parser in the stack.
- **House rules:** no inline comments anywhere (same as the backend), no hardcoded hosts outside `VITE_API_BASE_URL`, no secrets in the frontend, no new runtime dependency without asking.
- `frontend/.gitignore` covers `node_modules`, `dist`, `.env*` (the **root** `.gitignore` is stale and does not).

## Product direction (supersedes `PROJECT_SPECIFICATION.md`)

Agentic system for job discovery, job analysis, matching, tailored resume + cover letter, application tracking. **Removed:** interview preparation, salary filtering, currency filtering. Search constraints are only **role, location, experience**. Stack target: FastAPI + Postgres + SQLAlchemy + Alembic + Pydantic + Gemini, with SearXNG for search and Firecrawl for scraping.

Resumes are **user-uploaded master documents**, never generated, rewritten or summarized by the system — there is no Resume Agent. Job Matching consumes the uploaded master resume directly; the Cover Letter agent reads the same evidence together with the stored `Match`.

`PROJECT_SPECIFICATION.md` and `README.md` still describe the old plan (SQLite, Playwright/LinkedIn login, interview prep, analytics). They are stale — the code and this file win. Update them when scope changes rather than reading them for direction.

## Not implemented yet

None of this exists in code — don't go looking for it, and don't assume a module handles it:

- **Job Discovery**, **Job Analysis**, **Job Matching** and **Cover Letter** are implemented (`backend/app/agents/discovery/`, `app/agents/job_analysis/`, `app/agents/job_matching/`, `app/agents/cover_letter/`) and run on the agent runtime, each behind its own HTTP endpoint. **There is no Resume Agent** — resumes are uploaded through `app/services/resume_upload.py`. Nothing chains the agents automatically: a client (or a test) calls `discover` → `analyze` → `match` → `cover-letter` in turn, and there is no pipeline, scheduler or orchestrator.
- No SearXNG/Firecrawl live service in tests. SearXNG and Firecrawl are reached through the first-party `httpx` clients in `app/clients/` (`firecrawl-py` is not used); Gemini through the first-party wrapper over `google-genai`.
- No `app/schemas/`, no `app/repositories/`; `app/services/` has `normalization.py`, `resume_upload.py`, `candidates.py`, `jobs.py`, `applications.py`. No background worker, queue or scheduler — every agent endpoint finishes its run inside the request. No auth, no multitenancy, no PDF/DOCX parsing. CORS exists for the Vite dev origins only.
- The frontend is a **UI over the synchronous API** — no background jobs, optimistic updates, offline mode or pagination beyond the list endpoints' `limit`/`offset` (applications and jobs pages do paginate).
- OpenCode is a **development-time** tool only (this file, the dev loop). It is not a runtime dependency and must never appear in `requirements.txt` or `app/`.

## Conventions

- `from __future__ import annotations` plus `TYPE_CHECKING`-guarded relationship imports to break circular imports (`job.py` ↔ `application.py`). Follow this for new relationships.
- Docstrings on classes and module-level functions; **no inline comments** anywhere.
- Types everywhere via `Mapped[...]` / `mapped_column`.