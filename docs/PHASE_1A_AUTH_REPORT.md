# Phase 1A — Authentication Foundation Report

- **Date:** 2026-10-12
- **Branch:** `feat/database-models`
- **Scope:** Backend identity foundation only. Multi-user *authorization* was **not** implemented and is **not** claimed.

## Files changed

New:
- `backend/app/models/user.py` — `User` model.
- `backend/app/models/auth_session.py` — `AuthSession` (table `sessions`).
- `backend/app/services/auth.py` — password hashing, session create/lookup/expiry/revocation, registration, authentication.
- `backend/app/api/csrf.py` — `CSRFMiddleware`.
- `backend/app/api/routes/auth.py` — register/login/logout/me.
- `backend/app/cli/__init__.py`, `backend/app/cli/bootstrap_legacy_owner.py` — one-time legacy-owner command.
- `backend/alembic/versions/20261012_0003_add_authentication.py` — migration.
- `backend/tests/test_auth_service.py`, `backend/tests/test_auth_bootstrap.py`, `backend/tests/api/test_auth_api.py` — focused tests.

Modified:
- `backend/app/models/candidate.py` — added nullable, unique `user_id` FK → `users.id (CASCADE)` and `user` relationship.
- `backend/app/models/__init__.py` — export `User`, `AuthSession`.
- `backend/app/core/config.py` — `session_cookie_name`, `csrf_cookie_name`, `session_ttl_seconds`, `session_cookie_secure`, `session_cookie_samesite`.
- `backend/app/api/deps.py` — added `get_current_session`, `get_current_user`, `NOT_AUTHENTICATED_DETAIL`; kept `get_current_candidate` for not-yet-migrated routes.
- `backend/app/api/schemas.py` — `RegisterRequest`, `LoginRequest`, `UserOut`.
- `backend/app/api/errors.py` — map `DuplicateEmailError` → 409, `InvalidCredentialsError` → 401, `InactiveUserError` → 403.
- `backend/app/api/router.py` — mount auth router.
- `backend/app/main.py` — add `CSRFMiddleware`; CORS `allow_credentials=True`.
- `backend/requirements.txt` — `argon2-cffi`.
- `backend/.env.example` — auth cookie settings (no secrets).
- `backend/tests/test_migrations.py` — expected tables now include `users`, `sessions`.

## Models, endpoints and session behaviour

- **`User`:** `id`, normalized unique `email` (lower-cased/trimmed, `String(320)`), `password_hash`, `is_active`, timestamps. One `candidate` (uselist=False); many `sessions`.
- **`AuthSession`:** `user_id` (CASCADE), `token_hash` (unique, SHA-256 hex), `csrf_token_hash`, `expires_at`, `revoked_at`, timestamps. Only hashes are stored.
- **`Candidate.user_id`:** nullable + unique so the one-profile-per-user rule is a DB guarantee while a legacy profile can stay unassigned. Multiple NULLs are permitted (no accidental first-registrant claim).
- **Endpoints:** `POST /api/auth/register` (201; writes user + candidate in one transaction; auto-login), `POST /api/auth/login` (200), `POST /api/auth/logout` (204; revokes), `GET /api/auth/me` (200/401).
- **Hashing:** Argon2id (`argon2-cffi`); malformed stored hashes verify as `False`; unknown-email logins still run a dummy verify for timing parity.
- **Sessions:** opaque `secrets.token_urlsafe(32)`, stored as SHA-256; lookup rejects unknown/revoked/expired identically (401); 7-day TTL; logout sets `revoked_at`.
- **Cookies:** session cookie `HttpOnly`, `SameSite=Lax`, `Secure` configurable (default off for local http); readable CSRF cookie. `SameSite`/`Secure` come from settings.
- **CSRF:** `CSRFMiddleware` enforces `X-CSRF-Token` == CSRF cookie on unsafe methods **only when a session cookie is present**, `secrets.compare_digest`, else 403. Unauthenticated/non-browser requests are untouched.

## Migration

- **Name:** `20261012_0003_add_authentication` (revision `20261012_0003`, down `20261003_0002`).
- **Status:** created and verified; **not applied to the live dev database.** `alembic check` therefore reports `Target database is not up to date.` — the DB is still at `20261003_0002`, not schema drift. `tests/test_migrations.py` upgraded a throwaway DB to head and passed `compare_metadata` (no drift), the expected-tables check, and a downgrade/upgrade round trip (8 passed). Apply with `alembic upgrade head` when ready.

## Focused test totals

- `tests/test_auth_service.py` + `tests/test_auth_bootstrap.py` + `tests/api/test_auth_api.py` + `tests/api/test_api_cors.py` → **36 passed** (33 auth + 3 CORS regression).
- `tests/test_migrations.py` → **8 passed**.
- No other tests were run (per boundary). The full suite was **not** run.

Coverage added: registration, duplicate email (case-insensitive), valid/invalid passwords, inactive accounts, login, cookie flags, `/me` auth, two-user distinct sessions, CSRF enforcement, logout revocation, session expiry, session hash storage, one-candidate-per-user, and every bootstrap safeguard.

## Legacy-data bootstrap safeguards

`python -m app.cli.bootstrap_legacy_owner --email <owner> [--name] [--password] [--yes]`:

- Requires an **explicit** `--email`; never infers the owner.
- **Requires confirmation** (`--yes` or an interactive `y`); the service refuses (`confirm=False`) for every write path.
- **Reuses** the single unassigned legacy candidate when exactly one exists; links it to the owner.
- **Refuses** when more than one unassigned candidate exists (ambiguous), when the named account already owns a candidate, or when a new account lacks a password — it never guesses.
- **Never assigns by registration order:** a candidate owned by another user is never treated as legacy; the command creates a new profile for the new owner instead of touching existing owned data.
- Manual only: not imported by the app and not run on startup.

## Known remaining security gaps (not fixed in this phase)

- **No authorization yet:** `get_current_candidate` still returns the single candidate; `resumes`, agent (`match`/`cover-letter`) and candidate-selection routes trust client-supplied ids. Any authenticated user can still act on any candidate.
- **Applications have no owner column**; list/read/update remain global.
- **Agent-run isolation not done:** queue drains by `agent_name` only.
- **Frontend unchanged:** no login UI, no `withCredentials`, no 401 redirect.
- CSRF is double-submit only (stored `csrf_token_hash` not yet re-verified in routes); no rate limiting/lockout, email verification, or password reset.
- Cookie `Secure` defaults off (must be enabled for HTTPS deployments).
- Session hashes are unsalted SHA-256, acceptable only because tokens are high-entropy random values.
- The auth migration and legacy bootstrap have **not** been applied to the live database.

## Exact next recommended task

**Phase 1B — Candidate ownership enforcement.** Replace `get_current_candidate` with the authenticated user's candidate, make `candidates.user_id` NOT NULL, and scope the resume, matching and cover-letter routes (and their explicit `candidate_id`/`document_id`/`match_id` resolution) to the authenticated user. Prerequisite: run `alembic upgrade head`, then `python -m app.cli.bootstrap_legacy_owner` against the existing database so the legacy candidate is assigned before any NOT NULL enforcement.
