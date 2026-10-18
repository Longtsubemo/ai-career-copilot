# Free hosted prototype setup

This package targets one Render web service: FastAPI serves the built React application, API, health check, and FastAPI documentation from one origin. Render supplies the public `onrender.com` URL and terminates HTTPS. The application listens on `0.0.0.0:$PORT`; no frontend runtime server or custom domain is needed.

The repository includes a minimal [`render.yaml`](../render.yaml) and a multi-stage [`Dockerfile`](../Dockerfile). The Blueprint selects the `main` branch and disables automatic deployment until the service is deliberately enabled in the Render dashboard. The dashboard prompts for the database URL and Firecrawl API key; do not commit their values. The Gemini key is intentionally omitted so an unpaid Gemini account is not silently enabled for resume processing.

## Configuration

Set these production environment variables in Render:

| Variable | Purpose |
| --- | --- |
| `CAREER_COPILOT_ENVIRONMENT` | Enables production safeguards. |
| `CAREER_COPILOT_DEBUG` | Must be disabled. |
| `CAREER_COPILOT_DATABASE_URL` | Neon PostgreSQL URL; supplied as a dashboard secret. |
| `CAREER_COPILOT_SEARCH_PROVIDER` | Choose `firecrawl` for hosted Search. |
| `CAREER_COPILOT_FIRECRAWL_BASE_URL` | Firecrawl API origin used for Search and Scrape. |
| `CAREER_COPILOT_FIRECRAWL_API_KEY` | Server-side Firecrawl credential; supplied as a dashboard secret. |
| `CAREER_COPILOT_SESSION_COOKIE_SECURE` | Must be enabled behind Render HTTPS. |
| `CAREER_COPILOT_CORS_ORIGINS` | Leave unset for same-origin access; production default is empty. |
| `CAREER_COPILOT_GEMINI_API_KEY` | Optional. Do not configure an unpaid key for public resume processing. |
| `CAREER_COPILOT_DISCOVERY_DAILY_USER_LIMIT` | Optional per-account daily cap shared by discovery and job verification; production default is 3. |
| `CAREER_COPILOT_DISCOVERY_DAILY_GLOBAL_LIMIT` | Optional global daily cap shared by discovery and job verification; production default is 20. |
| `CAREER_COPILOT_ENABLE_RESUME_AI` | Optional privacy gate; production default is disabled. |

Production settings reject debug mode, insecure session cookies, missing database URLs, PostgreSQL URLs without TLS, local development CORS origins, and incomplete search-provider settings. Selecting Firecrawl requires an explicit HTTPS base URL and API key; selecting SearXNG requires an explicit HTTPS service URL rather than the local development default. Neon provides standard PostgreSQL URLs; the application converts `postgresql://` to the installed `postgresql+psycopg://` driver. Use the pooled Neon endpoint for the web service to keep database connections bounded. Keep `sslmode=require` (or a stronger verification mode) in the URL. Use the direct endpoint for carefully controlled administrative operations if a migration or provider workflow requires it. The runtime uses SQLAlchemy's connection pool with `pool_pre_ping`; Alembic uses `NullPool`.

The same-origin setup leaves production CORS disabled by default. Auth cookies use HTTPS `Secure` and the existing `SameSite=Lax` policy; the current double-submit CSRF check and session-bound token validation remain in place. No additional Origin middleware was added. If a separate frontend origin is intentionally introduced later, review its credentialed CORS and CSRF threat model before adding it to `CAREER_COPILOT_CORS_ORIGINS`.

`POST /api/jobs/discover` and authenticated `POST /api/jobs/{job_id}/verify` share durable daily quota counters. Each discovery run reserves one unit; each verification attempt reserves one unit. The reservation updates a per-user counter and a global counter in one database transaction, using PostgreSQL unique-key upserts so concurrent web instances cannot exceed the limits. It commits before provider work, so failed searches/crawls still count. Verification attempts rejected later by the per-job cooldown also count because quota reservation precedes that check. Counters use UTC calendar days; older rows are pruned on the next reservation. Production defaults are 3 units per account and 20 across the service per UTC day. Local development defaults are 100 per account and 1,000 globally. Set `CAREER_COPILOT_DISCOVERY_DAILY_USER_LIMIT` and `CAREER_COPILOT_DISCOVERY_DAILY_GLOBAL_LIMIT` to explicitly override either environment's defaults. A rejected request receives a generic 429 response. No forwarded IP address is used for identity.

These are application-operation limits, not exact Firecrawl HTTP-request limits: one discovery run is one quota unit even though the bounded run may make multiple Search or Scrape requests. Review provider usage separately in the Firecrawl dashboard.

Resume upload, resume-based matching, and cover-letter generation are guarded server-side by `CAREER_COPILOT_ENABLE_RESUME_AI`. It defaults to enabled in local development for existing workflows and disabled in production unless explicitly set to `true`. Render does not set this flag. Enabling it requires a separate, explicit privacy/data-use decision; the flag alone is not consent or approval. Job analysis uses public job data and remains available to authenticated users while resume AI is disabled.

Firecrawl Search sends `POST /v2/search`; Scrape remains `POST /v2/scrape`. Both use the configured base URL and the API key stays in the server process. Search result descriptions remain search metadata only: the existing source registry, scrape client, and page-derived vacancy validator still decide whether a posting can be saved. Review current Firecrawl plan limits and usage in its dashboard before enabling discovery. See the [Firecrawl Search API reference](https://docs.firecrawl.dev/api-reference/endpoint/search).

## Local build and checks

The production image pins Python 3.13.7 and Node 22.19.0 and uses the checked-in npm lockfile. Run the same checks locally with those runtimes:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Set CAREER_COPILOT_DATABASE_URL and TEST_DATABASE_URL to the same
# dedicated disposable PostgreSQL instance before running database tests.
.\.venv\Scripts\python.exe -m pytest -ra
cd ..
cd frontend
npm ci
npm run build
cd ..
docker build -t ai-career-copilot:ci .
```

The backend test fixtures create and drop databases and tables. Never point either database variable at the development, Neon, or production database; the test PostgreSQL role needs permission to create databases. The GitHub Actions workflow owns its own disposable PostgreSQL service and sets both variables to that service. Discovery, Firecrawl, SearXNG, and Gemini tests use fake clients or mocked transports and do not need production API keys. Migration `20261016_0008` adds the minimal `discovery_daily_usage` table required for durable atomic per-day user and global counters; no migration was executed against a hosted or development database as part of its implementation.

For local Vite development, `VITE_API_BASE_URL` is blank by default and Vite proxies `/api`, `/health`, and API documentation paths to the local FastAPI process. Set an explicit `VITE_API_BASE_URL` only when intentionally using another API origin.

The Docker image leaves `VITE_API_BASE_URL` unset so its production bundle uses same-origin relative paths, serves `frontend/dist` through FastAPI, and runs as a non-root user. It does not copy `.env` files, tests, Git metadata, local environments, or Node dependencies into the runtime layer. Migrations are never run at application startup. Run `alembic upgrade head` once as a reviewed, controlled deployment operation with the intended database target verified; do not run it from the web-service start command.

## Continuous integration

The [GitHub Actions workflow](../.github/workflows/ci.yml) runs for pull requests targeting `main` and pushes to `main`. It installs backend dependencies and runs the full pytest suite on Python 3.13.7, builds the frontend with Node 22.19.0 and `npm ci`, then builds the production Docker image without pushing it to a registry.

Database-backed tests use a `postgres:16-alpine` service with a readiness health check. Both `TEST_DATABASE_URL` and `CAREER_COPILOT_DATABASE_URL` point to that disposable service; its credentials are CI-only values, and the workflow does not use production secrets. The fixtures may create/drop test databases and tables there. The job has no Neon connection, deployment hook, Render credential, or external-service API key.

The Docker build uses only repository inputs, including the committed npm lockfile and backend requirements. `.dockerignore` excludes credentials, Git metadata, virtual environments, `node_modules`, test files, and generated frontend output that the build creates itself. The image is built in GitHub Actions because a local Docker daemon is unavailable in this environment. The build downloads dependencies from package registries, but does not start the application container or call SearXNG, Firecrawl, Gemini, or job-site endpoints.

Passing CI verifies the checked-in code and image build only. It does not prove the application is deployed successfully, validate Render or Neon dashboard configuration, or make real-resume processing ready for public use.

## Prototype limitations and data policy

Render free web services spin down after inactivity and may take about a minute to wake. The filesystem is ephemeral and there is no persistent disk on the free plan; this application stores uploaded resume text in PostgreSQL, not local files, but do not treat the service filesystem as durable storage. Free services have usage limits and may be suspended for unusually high outbound traffic. Neon free compute can suspend while idle; plan for a cold database connection and review current storage, compute, backup, and restore limits. Free-tier quotas and terms can change. See Render's [free plan limits](https://render.com/docs/free) and Neon's [connection guidance](https://neon.com/docs/manage/endpoints/).

Do not expose real-resume processing publicly until the data-use and privacy decision is resolved. Google's [Gemini API terms](https://ai.google.dev/gemini-api/terms) state that unpaid-service content may be used to improve products and may be reviewed by people. Keep the Gemini key unset and use synthetic resume content for prototype testing unless an approved data-processing arrangement and user-facing policy are in place. This phase does not add consent screens or change Gemini calls.

The free deployment is a prototype, not a production reliability or backup plan. Maintain a tested export/restore procedure, monitor Render/Neon/Firecrawl quotas, and review the privacy policy before inviting users to submit personal information.
