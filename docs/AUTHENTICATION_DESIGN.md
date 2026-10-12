# Authentication Design & Migration Plan

- **Date:** 2026-10-12
- **Branch:** `feat/database-models` (substantial uncommitted changes preserved)
- **Status:** Design only — no code, model, config, frontend, test, or migration changes were made.
- **Companion doc:** `docs/MULTI_USER_READINESS_AUDIT.md` (current state: no auth, no ownership enforcement)

This document distinguishes **confirmed** current-code facts from **proposed** changes.

## 1. Recommended architecture

**Recommendation: server-side opaque sessions in an HttpOnly cookie, passwords hashed with Argon2id, single FastAPI dependency for identity.**

| Concern | Recommendation | Trade-off |
|---|---|---|
| Registration | `POST /api/auth/register` (email + password). May be disabled/invite-only until legacy backfill completes (§3). | Open registration is simplest but lets anyone create an account; for ~100 users, invite/bootstrap is safer during migration. |
| Login | `POST /api/auth/login` verifies password, creates a session row, sets a cookie. | — |
| Password storage | Argon2id via `argon2-cffi`, per-password salt, store only the encoded hash. | Adds one dependency vs. stdlib `hashlib.scrypt` (zero-dep, acceptable fallback). Never store plaintext or unsalted hashes. |
| Credentials transport | Opaque 256-bit random token, stored **hashed** (SHA-256) in a `sessions` table; delivered as `Set-Cookie: session=...; HttpOnly; Secure(prod); SameSite=Lax; Path=/`. | Cookie is immune to JS/XSS token theft, unlike `localStorage`. Requires CSRF mitigation (`SameSite=Lax` + a CSRF token for state-changing routes, or a custom `X-Requested-With`/origin check). |
| Identity per request | New dependency `get_current_user` reads the cookie, looks up the unexpired/unrevoked session, and returns the `User`. `get_candidate_for_user` derives the candidate. All routes depend on it. | Replaces the current `get_current_candidate` (`app/api/deps.py:35`), which returns the single candidate unconditionally. |
| React storage/sending | Token lives only in the HttpOnly cookie; Axios is configured `withCredentials: true` in `frontend/src/api/client.ts:17`. `AuthProvider` calls `GET /api/auth/me` on boot (mirrors `CandidateProvider` in `frontend/src/hooks/useCandidate.tsx`). | No token in JS. `CORS` must switch to `allow_credentials=True` with an explicit origin list (`app/main.py:49-56`; currently `allow_credentials=False`). |
| Logout / expiry | `POST /api/auth/logout` revokes the session row and clears the cookie. Sessions have an absolute expiry (e.g. 14 days) plus optional idle timeout. | A DB lookup per request is negligible at this scale; it enables instant revocation, which stateless JWT cannot. |
| 401 handling | Missing/expired/revoked credentials → `401`; authenticated-but-not-owner → `404` (do not reveal existence) or `403`. Frontend already maps 401/403 in `frontend/src/api/errors.ts:11-13`; an interceptor should clear auth state and redirect to login. | Distinguishing 401 vs 403 is useful; for cross-tenant reads prefer 404 to avoid resource-existence leaks. |

**Why not JWT only:** JWTs are stateless but hard to revoke and require key management/rotation; at 100 users the DB-backed session lookup is simpler and more secure. **Why not a third-party IdP (Auth0/Cognito):** unnecessary infrastructure for this stack and scale.

## 2. Proposed data model and relationships

Confirmed: `Candidate` (`app/models/candidate.py:17`) has no owner; `Document.candidate_id` (`app/models/document.py:45`) and `Match.candidate_id` (`app/models/match.py:48`) are mandatory owner columns; `AgentRun.candidate_id` (`app/models/agent_run.py:88`) is nullable; `Application` (`app/models/application.py`) has **no owner column**.

Proposed:

- **`users`** (new): `id` UUID PK, `email` (unique, case-insensitive — store lowercased or `citext`), `password_hash`, `is_active`, `created_at`, `updated_at`. Reuse `UUIDPrimaryKeyMixin`, `TimestampMixin`, `Base` (`app/db/base.py`).
- **`sessions`** (new): `id` UUID PK, `user_id` FK → `users` `ON DELETE CASCADE`, `token_hash` (unique), `expires_at`, `revoked_at`, `created_at`, optional `user_agent`/`ip`.
- **`candidates.user_id`** (new): FK → `users`, **unique**, `ON DELETE CASCADE`. **One candidate profile per user** for now; the unique FK can be dropped later to allow multiple profiles. This is why `create_candidate`'s current "only one profile exists" rule (`app/services/candidates.py:57`) can be retired.
- **`applications.candidate_id`** (new): FK → `candidates`, `ON DELETE CASCADE`, indexed, backfilled (see §3). Preferred over `user_id` for consistency with the other private entities, which all key off `candidate_id`.
- **Shared resources stay unowned:** `jobs`, `companies`, `job_analyses` (keyed by `job_id` only, `app/models/job_analysis.py:41`) remain global by design.

**Deriving identity (not trusting the client):**
- Replace `get_current_candidate` (`app/api/deps.py:35`) with `get_candidate_for_user(user=Depends(get_current_user))`.
- In `_resolve_candidate_id` (`app/api/routes/agents.py:67`), an explicit `candidate_id` must equal the authenticated user's candidate id, else `404`. Best: ignore client-supplied `candidate_id` entirely and use the authenticated candidate.
- `_resolve_resume_id` (`agents.py:85`) and `_resolve_match_id` (`agents.py:107`) must verify the resolved `Document`/`Match` belongs to the authenticated candidate before use.
- Application routes (`app/api/routes/applications.py`) must filter list/read/update by the authenticated candidate's id.

## 3. Legacy-data migration and preservation strategy

Use **expand → backfill → validate → contract**. Never assign the existing private data to the first person who registers.

**Establishing the legitimate owner (recommended): a one-time, operator-run ownership bootstrap command.**
1. Deploy schema with owner columns **nullable** (no constraints yet).
2. Operator creates the real owner account out-of-band via a local management command (e.g. `python -m app.cli.create_owner --email ...`), not the public registration path. This is an auditable, local action performed by whoever controls the deployment.
3. Run a backfill that binds the existing `Candidate` to that user (`candidates.user_id`) and, transitively, all private rows.
4. Only after backfill, enable public registration (if desired) and enforce `NOT NULL`/uniqueness.

**Per-entity backfill:**
- **Candidate:** the single existing row → the bootstrap user. (Also governs document/match/run ownership transitively.)
- **Master resume + history (`documents`, `kind='master_resume'`):** already carry `candidate_id`; no row change needed — ownership flows from the candidate. Verify counts pre/post migration.
- **Matches (`matches.candidate_id`):** already mandatory; flows from candidate.
- **Cover letters (`documents`, `kind='cover_letter'`):** flow from `candidate_id`; also verify their `source_document_id` chain stays intact.
- **Applications:** add `candidate_id` and backfill to the bootstrap candidate **only when unambiguous** — i.e. `job_id` is present and the deployment has exactly one candidate. Rows with `job_id IS NULL` and no derivable owner are **ambiguous**: leave `candidate_id` NULL, emit them in the migration report, and let the operator decide manually. Do not guess.
- **Agent runs (`agent_runs.candidate_id`):** runs that already have a `candidate_id` need no change. Global runs (e.g. discovery, which passes no `candidate_id` at `app/api/routes/agents.py:158-164`) stay NULL — they are shared/system runs, not private data. Do not force an owner.

**Safety rules:** backup before migrating; migration is idempotent and reports counts of assigned vs. quarantined rows; no destructive column drops in the same revision as the backfill; keep `candidates.user_id` nullable until every candidate is mapped.

## 4. Ordered implementation plan

**Phase A — Auth foundations (no behavior lock-down yet)**
- Models: `users`, `sessions`. Migration `0003_users_sessions`.
- Services: `app/services/auth.py` (hash/verify, create/revoke session), `app/services/users.py`.
- Deps: `get_current_user`; keep `get_current_candidate` temporarily.
- Routes: `app/api/routes/auth.py` (register/login/logout/me).
- Config: `app/core/config.py` — session TTL; CORS `allow_credentials=True` (origin list already configurable).
- Risk: cookies across `localhost` vs `127.0.0.1` (different sites) — standardize dev origin.

**Phase B — Candidate linkage + ownership enforcement**
- Migration `0004_candidate_owner` (nullable `candidates.user_id`; later contract to NOT NULL/unique after backfill).
- Rewrite `get_current_candidate` → user-scoped; update `resumes.py` and `agents.py` resolvers.
- Risk: existing tests assume the singleton (`app/services/candidates.py`).

**Phase C — Application ownership**
- Migration `0005_application_owner` (`applications.candidate_id`, backfill, scope queries).
- Update `app/api/routes/applications.py` + `app/services/applications.py`.

**Phase D — Agent-run isolation**
- Owner-scope `AgentRuntime.claim_next` (currently by `agent_name` only, exploited/executed via `app/api/agent_runner.py:63`).
- Namespace idempotency keys by owner for global keys (discovery `agents.py:162`, job analysis `agents.py:181`).
- Add an owner-scoped run read endpoint.

**Phase E — Legacy migration execution**
- Run bootstrap + backfill against the real DB; validate counts; then enforce constraints.

**Phase F — Frontend auth**
- `AuthProvider`, login/register pages, Axios `withCredentials`, 401 interceptor + logout (`frontend/src/api/client.ts`, `errors.ts`, hook pattern in `frontend/src/hooks/useCandidate.tsx`).

**Phase G — Cross-user tests**
- Two-user isolation suite (backend `tests/api/`), migration/backfill tests (`tests/test_migrations.py` pattern), and a two-user happy-path matching/cover-letter flow.

## 5. Acceptance criteria (concrete tests)

1. **Two users register/login:** User A and User B each register, log in, and receive distinct sessions; `GET /api/auth/me` returns the correct user for each.
2. **Unauthenticated rejection:** every private route returns `401` with no/expired/revoked cookie.
3. **Cross-user read denial:** A cannot `GET`/`PATCH` B's resume, match, cover letter (document), application, or agent run (404/403), including via client-supplied `candidate_id`/`document_id`/`match_id`/`application_id`.
4. **Own-resume workflows:** A's `POST /api/jobs/{id}/match` and `/cover-letter` use A's own master resume and produce artifacts owned by A; B's artifacts are unaffected.
5. **Legacy preservation:** after migration, row counts for candidates/resumes/matches/cover letters/applications/agent runs are unchanged; every pre-existing private row is owned by the bootstrap user; ambiguous applications are reported, not silently assigned; `alembic check` stays clean.
6. **Shared resources:** both users can list/read the same jobs and the same job analyses; jobs/analyses have no per-user duplication.

## 6. Decisions requiring your approval

1. **Auth mechanism:** HttpOnly cookie sessions (recommended) vs. bearer token held in memory. Cookie needs CSRF handling and `allow_credentials=True`.
2. **Password hashing:** add `argon2-cffi` (recommended) vs. stdlib `hashlib.scrypt` (no new dependency).
3. **Candidate cardinality:** one candidate profile per user now (recommended) vs. model multiple profiles immediately.
4. **Application owner column:** `applications.candidate_id` (recommended, consistent) vs. `applications.user_id`.
5. **Legacy owner bootstrap:** operator-run local command (recommended) vs. a first-run claim token. **This is the highest-risk decision** — it determines who receives all existing private data.
6. **Registration policy:** open registration vs. invite/bootstrap-only (until backfill completes).
7. **Session lifetime / refresh:** single long-lived session vs. short access + refresh rotation.
8. **Confirm shared-resource design:** jobs, companies, and job analyses remain global (no owner), and global agent runs (discovery) stay owner-less.

## Migration risks (summary)

- **Owner misassignment is irreversible-looking:** binding legacy data to the wrong account exposes the previous owner's resume, matches, and letters. Mitigate with the operator bootstrap + verified backup + row-count report.
- **Ambiguous applications:** rows without `job_id`/derivable candidate must be quarantined, not guessed.
- **Constraint timing:** applying `NOT NULL`/unique before a complete backfill will fail or block deploy — keep expand/contract separate revisions.
- **Cross-origin cookies:** `localhost` vs `127.0.0.1` are different sites; standardize origins or use the bearer alternative.
- **Existing test assumptions:** the singleton-candidate service/test behavior (`app/services/candidates.py`, `tests/`) will need updating in Phase B.
