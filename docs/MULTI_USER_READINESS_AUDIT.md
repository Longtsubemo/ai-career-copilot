# Multi-User Readiness Audit

- **Audit date:** 2026-10-12
- **Audited branch:** `feat/database-models`
- **Scope:** Backend authentication/authorization, candidate and private-data ownership, shared resources, DB constraints, and agent-run context. Frontend and styling intentionally out of scope.

## Executive verdict

**NOT READY** for multiple independent users.

The system is explicitly and consistently designed as single-user. There is no authentication, no session/token handling, and no ownership authorization on any route. "Who am I" is resolved as "the one candidate row that exists" (`app/services/candidates.py::current_candidate`), not from a credential. Several ownership columns exist on private entities, but no route enforces them, and one private entity (`applications`) has no owner column at all. This is expected given the current product direction, but it means multi-user support requires foundational work, not a patch.

## Findings

| Area | Finding | Severity | Evidence (file / symbol) |
|------|---------|----------|--------------------------|
| Authentication | No authentication of any kind. No login, registration, token, session, or middleware; `get_current_candidate` is the only "identity" and it returns the single candidate row. | Critical | `backend/app/api/deps.py:35` (`get_current_candidate`); `backend/app/services/candidates.py:21` (`current_candidate`); `backend/app/api/router.py` (no auth deps); grep for `auth/token/current_user/owner_id/user_id` returned nothing |
| Candidate profile ownership | `GET /api/candidates` returns every candidate row to any caller. | High | `backend/app/api/routes/candidates.py:18` (`list_candidates`) |
| Candidate selection | Match/cover-letter accept an explicit `candidate_id` from the client and validate only that it *exists*, never that the caller owns it. | Critical | `backend/app/api/routes/agents.py:67` (`_resolve_candidate_id`, existence check at `:74`) |
| Resume document selection | `_resolve_resume_id` accepts an explicit `document_id` and validates only existence; ownership/kind is deferred to agent preconditions, not the API. | High | `backend/app/api/routes/agents.py:85` (`_resolve_resume_id`, `:96`) |
| Match selection | `_resolve_match_id` accepts an explicit `match_id` and validates only existence. | High | `backend/app/api/routes/agents.py:107` (`_resolve_match_id`, `:123`) |
| Applications | The `Application` model has **no** `candidate_id`/owner column, so applications cannot be scoped to a user at all. `GET /api/applications` and `GET /api/applications/{id}` are global. | Critical | `backend/app/models/application.py:33` (only `job_id`); `backend/app/api/routes/applications.py:41` (`list_applications`), `:55` (`read_application`) |
| Private data (resumes/history) | Master resume + history are scoped implicitly to the single current candidate via `get_current_candidate`, not to an authenticated principal. | High | `backend/app/api/routes/resumes.py:42` (`read_master_resume`), `:54` (`read_master_resume_history`) |
| Private data (documents/matches) | `Document.candidate_id` and `Match.candidate_id` exist and cascade with the candidate, but no API route filters reads/writes by the caller's identity. | High | `backend/app/models/document.py:45`; `backend/app/models/match.py:48` |
| Agent runs | `AgentRun` carries `candidate_id`/`job_id`, but no read/list endpoint exposes runs, and a run is only returned to whoever triggered the endpoint. | Medium | `backend/app/models/agent_run.py:82`; `backend/app/api/agent_runner.py:76` (`agent_run_out`) |
| Agent queue draining | `run_agent_now` calls `claim_next(agent_name=...)`, which claims the oldest pending run for that agent **globally**. A user's request can execute another user's queued run of the same agent. | High | `backend/app/api/agent_runner.py:63`; `backend/app/agents/runtime/runtime.py` (`claim_next` filtered by `agent_name` only) |
| Shared resources | Jobs, companies, job analyses and discovery are global/shared by design; `JobAnalysis` is keyed only by `(job_id, prompt_version)` with no candidate scope. | Informational | `backend/app/api/routes/jobs.py:25` (list all jobs); `backend/app/models/job_analysis.py:30`; `backend/app/api/routes/agents.py:151` (`discover_jobs`) |
| Candidate singleton | Single-user invariant is enforced **only** in the service layer (`create_candidate` checks for an existing row); the DB has no uniqueness constraint, so a second candidate can be inserted out of band. | High | `backend/app/services/candidates.py:57`; `backend/app/models/candidate.py:17` (no constraint) |
| Idempotency keys | Discovery and job-analysis keys are global (`job_discovery:{role}:{location}:{experience}`, `job_analysis:{job_id}:{version}`); per-candidate keys exist only for matching/cover-letter. Cross-user collisions/reuse are possible. | Medium | `backend/app/api/routes/agents.py:162`, `:181`; `backend/app/agents/job_matching/contract.py` |

## Confirmed issues vs. concerns needing runtime verification

**Confirmed from code:**
- Zero authentication and zero ownership authorization (routes rely on existence checks only).
- `applications` has no owner column; list/read are unscoped.
- Explicit client-supplied `candidate_id`, `document_id`, `match_id` are accepted on ownership-free existence checks.
- The single-candidate invariant is application-level, not a DB constraint.
- Global agent queue draining by `agent_name`.

**Requires runtime verification (not proven exploitable from static code alone):**
- Whether any deployment boundary (reverse proxy, network isolation, single-tenant host) currently compensates for the missing auth. No such layer was inspected.
- Exact concurrency behavior of global `claim_next` under simultaneous multi-user requests (would need a concurrency test with two candidates).

No vulnerability is claimed as exploitable without an authenticated identity boundary; the findings above describe the absence of that boundary.

## How two users should be isolated

Every private entity must gain an owner and every route must derive that owner from the credential, never from request input:

- **Resumes/history:** `Document.candidate_id` already exists; routes must filter by the authenticated candidate's id and reject cross-owner `document_id` (agents' ownership precondition should be enforced at the API too).
- **Matches:** `Match.candidate_id` exists; reads and explicit `match_id` must be checked against the caller.
- **Cover letters:** stored as `Document(kind=cover_letter)` with `candidate_id`; scope by owner.
- **Applications:** add `candidate_id` (FK to `candidates`), backfill, and scope all list/read/update queries by the authenticated candidate; agents do not create applications so no side effect to guard.
- **Agent runs:** `AgentRun.candidate_id` exists; add an owner-scoped read endpoint and change queue claiming to filter by candidate (or by tenant) so a request cannot execute another user's pending run.
- **Shared jobs/analyses:** jobs and job analyses are intentionally shared (discovery is global); keep them global but ensure per-user artifacts (matches, letters, applications) never leak job-specific private content across owners.

## Prioritized remediation plan

**P0 — security blockers (must land before any multi-tenant use):**
1. Introduce authentication (login/registration, token or session) and a `get_current_user` dependency; remove reliance on `current_candidate`.
2. Add ownership authorization to every route that accepts a resource UUID (`candidate_id`, `document_id`, `match_id`, `application_id`).
3. Add `candidate_id` to `applications` (owner column + FK + migration) and scope all application queries.
4. Scope `AgentRuntime.claim_next` by owner/candidate so runs cannot execute across tenants.

**P1 — necessary before public beta:**
5. Enforce the candidate-singleton (or full multi-candidate) invariant at the DB level with a uniqueness/isolation constraint.
6. Add an owner-scoped agent-run read endpoint; ensure `AgentRun.output`/`error` are only returned to the owner.
7. Namespace idempotency keys by owner where global keys currently collide (discovery, job analysis).
8. Audit the error/exception handlers to ensure they never leak another tenant's run payload.

**P2 — later improvements:**
9. Tenant-aware rate limiting and audit logging.
10. Row-level security or a repository layer that centralizes owner filtering to prevent regressions.
11. Define tenancy for shared discovery results (dedupe across users vs. per-user discovery scope).

## What this audit did NOT verify

- No runtime exploitation was performed; no requests were sent, no servers or containers started.
- No authentication proxies, reverse proxies, or deployment/network isolation layers were inspected.
- The frontend was not audited for auth/token handling or client-side ownership assumptions.
- No database inspection or mutation; findings on constraints come from models and the two migration files present (`20260727_0001`, `20261003_0002`), not from live schema introspection.
- Agent runtime internals were read only to the extent needed to confirm `claim_next` filtering; full runtime concurrency semantics were not re-derived.
