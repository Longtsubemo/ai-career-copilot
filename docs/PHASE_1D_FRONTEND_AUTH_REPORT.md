# Phase 1D — Frontend Authentication & Profile Editing Report

## Summary

Phase 1D is **complete**. The React SPA can now authenticate against the
session-cookie API and complete its profile:

- **Auth UI.** `AuthProvider` restores the session from `GET /api/auth/me` on
  boot and exposes `login` / `register` / `logout`. New `LoginPage` and
  `RegisterPage` sit on public routes; every application route is gated behind
  the authenticated state.
- **CSRF + credentials.** The shared Axios client sends `withCredentials: true`
  and attaches the `X-CSRF-Token` header (read from the readable
  `career_copilot_csrf` cookie) to every unsafe request. A `401` clears local
  auth state and returns the user to login.
- **Profile editing.** New `PATCH /api/candidates/me` plus an onboarding /
  profile-editor screen let the account fill in the matching evidence
  (headline, location, years, seniority, skills, summary). Saving onboards a
  name-only account and re-saves from the profile page.
- Backend suite green: **708 passed** (Phase 1C baseline: 697; +11 new).
- Frontend: `npm run build` clean, and a new framework-free
  `npm test` suite (**25 passed**).
- `alembic check`: **no new upgrade operations** — no schema change this phase.

## What changed

### Part A — Backend profile editing

No model or migration change; the phase only adds a write path to an existing
table.

#### 1. Schema

- `backend/app/api/schemas.py` — new `CandidateUpdateRequest`:
  - Declares only the editable fields (`name`, `headline`, `location`,
    `years_of_experience` `ge=0 le=100`, `seniority`, `skills`, `summary`), so
    identity/ownership columns (`id`, `user_id`, timestamps) are **not** part of
    the contract — sending one is an `extra="forbid"` 422.
  - `name` is trimmed/collapsed and may not be blank; an all-empty body and an
    explicit `name: null` are both rejected (`model_validator`).

#### 2. Service

- `backend/app/services/candidates.py` — new `EDITABLE_FIELDS` frozenset and
  `update_candidate(session, candidate, *, updates)`:
  - Applies only whitelisted keys, `flush` + `refresh`, leaves omitted fields
    untouched. The caller owns the transaction. Ownership is never writable.

#### 3. Route

- `backend/app/api/routes/candidates.py` — new `PATCH /api/candidates/me`:
  - Depends on `get_current_candidate`, so the target is always the session's
    own profile (401 without a session, 404 without a profile) and the
    session-bound CSRF check applies.
  - Builds `updates` from `body.model_fields_set` (true partial update), calls
    the service and commits.

### Part B — Frontend authentication

#### 4. Shared client

- `frontend/src/api/session.ts` (new) — pure helpers `readCookie`,
  `isUnsafeMethod`, `csrfHeader` plus the cookie/header name constants.
- `frontend/src/api/client.ts` — `withCredentials: true`; a request interceptor
  that adds `X-CSRF-Token` for unsafe methods; a response interceptor that, on
  `401`, dispatches a `UNAUTHORIZED_EVENT` window event and rejects with the
  existing `ApiError`.
- `frontend/src/api/auth.ts` (new) — `register`, `login`, `logout`,
  `getCurrentUser`.
- `frontend/src/types/api.ts` — `User`, `RegisterRequest`, `LoginRequest`,
  `CandidateUpdateRequest`. `frontend/src/vite-env.d.ts` —
  `VITE_CSRF_COOKIE_NAME`.

#### 5. Auth state & routing

- `frontend/src/hooks/useAuth.tsx` (new) — `AuthProvider` with status
  `loading | authenticated | unauthenticated`; restores from `/me`; a failed
  restore with a non-401 error surfaces a retryable error screen. Listens for
  `UNAUTHORIZED_EVENT` to force sign-out.
- `frontend/src/utils/authRouting.ts` (new) — pure `isAuthRoute` /
  `authRedirect` decision used by the shell.
- `frontend/src/App.tsx` — `App` wraps `AuthProvider`; `Shell` picks the public
  routes vs `AuthenticatedApp`; `AuthenticatedShell` owns the candidate gate:
  a missing-evidence profile is redirected to `/onboarding` (with a "Skip for
  now" escape that stops nagging).
- `frontend/src/pages/LoginPage.tsx`, `RegisterPage.tsx` (new) and
  `frontend/src/components/auth/AuthLayout.tsx` (new).
- `frontend/src/layouts/AppLayout.tsx` — shows the account email and a
  "Sign out" action (desktop sidebar and mobile menu).

#### 6. Profile editing

- `frontend/src/utils/profile.ts` (new) — `hasProfileEvidence` /
  `isProfileIncomplete` mirror the backend's "candidate evidence" rule
  (summary/headline/seniority/skills/years; `0` counts).
- `frontend/src/api/candidates.ts` — `updateCurrentCandidate` (PATCH `/me`).
- `frontend/src/hooks/useCandidate.tsx` — adds `updateProfile`, refreshing the
  in-context candidate from the response.
- `frontend/src/components/CandidateForm.tsx` — accepts `initial` and
  `submitLabel` for edit mode.
- `frontend/src/pages/OnboardingPage.tsx` — updates an existing (name-only)
  profile or creates one if absent; adds "Skip for now".
- `frontend/src/pages/ProfilePage.tsx` — inline edit form, saves and returns to
  the detail view.
- `frontend/src/pages/DashboardPage.tsx` — an "incomplete profile" banner
  linking to `/profile` (visible after a skip).

#### 7. Dev configuration

- `frontend/.env.example` now recommends `http://localhost:8000` and documents
  `VITE_CSRF_COOKIE_NAME`. **SameSite=Lax caveat:** cookies are same-site only
  when the SPA and API share a hostname, so both must run on `localhost`
  (`http://localhost:5173` ↔ `http://localhost:8000`), not `127.0.0.1`.
- `frontend/package.json` — `"test": "node --test \"tests/**/*.test.ts\""`.

## Tests

Backend (new, in `backend/tests/api/test_api_candidates.py`):

- Requires authentication (401) and the CSRF header (403 when deleted).
- Edits every editable field; a `GET /me` round-trip matches.
- Partial update preserves omitted fields.
- `name` trims/collapses; blank or `null` name is 422; empty body is 422.
- Ownership/identity fields (`id`, `user_id`, `candidate_id`, `created_at`) are
  422 and change nothing.
- Out-of-range experience (`-1`, `101`) is 422.
- Never creates a second candidate; only the caller's profile changes.

Frontend (new, `frontend/tests/`, run with `node --test`):

- `session.test.ts` — cookie parsing/decoding, safe vs unsafe methods, CSRF
  header selection including alternative cookie names.
- `routing.test.ts` — `authRedirect` for loading/authenticated/unauthenticated
  across auth and app paths.
- `profile.test.ts` — evidence detection, whitespace/empty-list handling,
  zero-years-as-evidence.

## Commands run

```
cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 708 passed
.\.venv\Scripts\python.exe -m alembic check      # No new upgrade operations detected.

cd ..\frontend
npm run build                                    # tsc -b && vite build — clean
npm test                                         # node --test — 25 passed
```

## Boundaries honored

- No JWT, no bearer tokens; the session stays an HttpOnly cookie and the CSRF
  token stays a readable cookie, mirrored into the header — nothing is placed
  in `localStorage` or `sessionStorage`.
- No DB reset/drop/deletion and no destructive operations; no servers or
  containers restarted; no external Gemini/Firecrawl/SearXNG calls.
- No commits, staging, resets or stashes; unrelated working-tree changes
  preserved.
- No new runtime dependencies; tests use the Node built-in test runner.
- No passwords, tokens or resume contents logged.
- Registration continues to create the single profile — onboarding **updates**
  it rather than calling `POST /api/candidates` (which stays a 409 for a
  registered user).

## Unresolved risks / follow-ups

1. **Host/port coupling.** The `SameSite=Lax` session only crosses ports when
   the hostname matches; the UI is non-functional if the SPA is opened on
   `127.0.0.1` while the API is on `localhost` (or vice versa). Documented in
   `frontend/.env.example`; no code-level guard.
2. **Logout is best-effort on the client.** A failed `POST /api/auth/logout`
   still clears local state, so a transient error can leave a server-side
   session alive until it expires.
3. **No CSRF-token auto-refresh.** The readable cookie is read per request; if
   the session rotates the CSRF token the next unsafe request refreshes it
   automatically, but a lost/expired CSRF cookie without a session simply yields
   the backend's 403 (no client retry).
4. **No frontend test coverage for components/providers.** Only the pure
   helpers are unit-tested; React behaviour relies on `npm run build`
   (type-check) and manual use, since the project has no React test runner.
5. **`POST /api/candidates` remains unreachable for registered users** (409),
   by design from Phase 1A/1B — the UI never calls it.
6. **Documentation drift.** `AGENTS.md`, `README.md` and
   `PROJECT_SPECIFICATION.md` still describe the pre-auth frontend; only
   `frontend/.env.example` was updated.
