# AI Career Copilot

AI Career Copilot is a local, single-user job-search workflow. It combines deterministic job discovery and vacancy checks with AI-assisted job analysis, resume matching, cover-letter drafting, and application tracking. It opens directly into one locally stored profile and does not require an account, password, sign-in, or logout.

The project emphasizes traceable data flow: discovery only considers registered employer career pages and supported ATS posting URLs, validates evidence from the crawled page, and records agent runs and provider-operation quotas in PostgreSQL.

## What it does

- **Discover jobs:** search with SearXNG or Firecrawl Search, admit only explicitly supported career-page/ATS sources, scrape candidate pages, and validate page-derived vacancy evidence before saving a job.
- **Track freshness:** verify saved posting availability on demand; failed checks preserve the last accepted job content and verification state.
- **Analyze jobs:** create structured job summaries and extract requirements with Google Gemini.
- **Match a resume:** compare a candidate profile and resume against a saved job analysis.
- **Draft cover letters:** produce a job-specific draft grounded in the saved job analysis, match, and resume evidence.
- **Manage application progress:** track application status and notes for a candidate's jobs.
- **Keep one local profile:** the API initializes the profile on first use and scopes profile, resume, match, and application data to it. Durable daily provider-operation quotas are stored locally.
- **Version resume uploads:** extract text locally from TXT, Markdown, PDF, or DOCX and retain immutable versions in PostgreSQL. OCR for scanned PDFs is not supported.

AI-assisted actions require a configured Gemini API key. Job discovery requires compatible search and Firecrawl scrape services. These integrations are never called by the automated tests; tests use fakes and mocked transports.

## Architecture and data flow

```mermaid
flowchart LR
    Desktop[Tauri desktop shell] -->|same origin; loopback only| API[FastAPI API]
    Browser[React + Vite development app] -->|Vite proxy to loopback| API
    API --> Local[Single local profile + origin checks]
    API --> DB[(PostgreSQL + Alembic schema)]
    API --> Runtime[Persisted synchronous agent runtime]
    Runtime --> Discovery[Discovery agent]
    Discovery --> Search{Configured search provider}
    Search --> SearXNG[SearXNG JSON search]
    Search --> FSearch[Firecrawl Search API]
    Discovery --> Gate[Exact-host source eligibility]
    Gate --> Scrape[Firecrawl Scrape API]
    Scrape --> Evidence[Page-derived vacancy validation]
    Evidence --> DB
    Runtime --> GeminiAgents[Job analysis, resume match, cover letter]
    GeminiAgents --> Gemini[Google Gemini API]
    API --> ResumeParser[Local TXT / Markdown / PDF / DOCX text extraction]
    ResumeParser --> DB
```

During development, Vite proxies API paths to the loopback FastAPI service. The Dockerfile remains a CI image-build check and binds the API to loopback inside the container; it is not a network deployment. Agent execution is synchronous in the request; there is no background worker or general-purpose web crawler.

## Tech stack

- **Backend:** Python 3.13, FastAPI, Pydantic Settings, SQLAlchemy 2, Alembic, Psycopg 3, HTTPX.
- **Frontend:** React, TypeScript, Vite, Tailwind CSS.
- **Persistence:** PostgreSQL; migrations are maintained with Alembic.
- **AI and integrations:** Google Gemini, SearXNG or Firecrawl Search, and Firecrawl Scrape.
- **Security:** loopback-only API, exact local browser origins, cross-site request rejection, Tauri capabilities, local profile scoping, and PostgreSQL-backed daily provider-operation quotas. The application has no user authentication and is not a hosted multi-user service.
- **Quality checks:** pytest, frontend TypeScript/Vite build, and a Docker image build in GitHub Actions.

## Windows desktop developer shell

The repository includes a Tauri 2 Windows shell connected to the managed
PostgreSQL 16.16 and FastAPI runtime. For local development, it starts the
Python supervisor from `backend/.venv`, keeps application data under
`%LOCALAPPDATA%\AI Career Copilot`, and uses a dynamic loopback port. The shell
does not install a Windows service, change `PATH`, or use the browser-development
database. See [the desktop runtime notes](docs/DESKTOP_RUNTIME.md) for setup,
isolation, and lifecycle details.

This is an experimental developer shell, not an installer or a ready-to-use
end-user application. Python is supplied by the repository's virtual environment, and no installer
or release bundle is produced. The browser-based setup below remains unchanged
and continues to use developer-managed PostgreSQL.

## Local setup (Windows PowerShell)

### Prerequisites

- Python 3.13 (the Docker build uses Python 3.13).
- Node.js 22 and npm.
- For browser development: PostgreSQL 16, either from the Docker example below or a developer-managed local server. The desktop developer shell uses its separate managed runtime.
- For the desktop shell: Rust (MSVC toolchain), Microsoft C++ Build Tools, WebView2 Runtime, and the backend `.venv` dependencies.

From `frontend/`, install frontend dependencies and start the desktop shell:

```powershell
npm ci
npm run desktop:dev
```

The Tauri development command builds the frontend, starts Vite, and launches
the managed runtime. It does not require `backend/.env`; desktop settings are
isolated from developer environment variables. To use a disposable data root
for shell checks, set `AI_CAREER_COPILOT_TEST_DATA_DIR` to a new absolute
directory whose final name is `AI Career Copilot` before running the command.
- Search/crawl services for discovery, and a Gemini API key for AI-assisted analysis/matching/cover-letter actions. These provider services are not included in this repository's Dockerfile.

### 1. Start a disposable local PostgreSQL service with Docker

Skip this if you already have a local PostgreSQL server and database. This example creates a persistent Docker volume for local development data; the credentials are deliberately simple and are for local use only.

```powershell
docker volume create career-copilot-postgres
docker run --name career-copilot-postgres `
  --publish 127.0.0.1:5432:5432 `
  --env POSTGRES_DB=career_copilot `
  --env POSTGRES_USER=career_copilot `
  --env POSTGRES_PASSWORD=career_copilot `
  --mount source=career-copilot-postgres,target=/var/lib/postgresql/data `
  --detach postgres:16-alpine
```

If port `5432` is already in use, change the left side of `--publish` (for example, `5433:5432`) and use that host port in the database URL below. Do not use these example credentials outside a local machine.

### 2. Configure and run the backend

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `backend/.env` locally and add a database URL for your local PostgreSQL database. For the Docker command above:

```dotenv
CAREER_COPILOT_DATABASE_URL=postgresql+psycopg://career_copilot:career_copilot@localhost:5432/career_copilot
```

The environment file is ignored by Git. Keep real API keys in this local file or a process environment variable; never commit them.

With the shell still in `backend/`, apply the schema and start FastAPI:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The API and health check are available at `http://127.0.0.1:8000` and `http://127.0.0.1:8000/health`.

### 3. Run the frontend

Open another PowerShell window at the repository root:

```powershell
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

Open the Vite URL shown in the terminal (normally `http://localhost:5173`). The blank `VITE_API_BASE_URL` uses the Vite development proxy for API requests. For the production build, FastAPI serves the built frontend and API from one origin.

### 4. Configure providers when you want to use them

The checked-in `backend/.env.example` selects SearXNG search and uses local service defaults. The repository does not include a Compose stack for PostgreSQL, SearXNG, or Firecrawl, so discovery will need compatible services configured at:

- `CAREER_COPILOT_SEARXNG_BASE_URL` for the SearXNG `/search?format=json` API, when `CAREER_COPILOT_SEARCH_PROVIDER=searxng`.
- `CAREER_COPILOT_FIRECRAWL_BASE_URL` for Firecrawl's `/v2/search` and `/v2/scrape` APIs, when using `CAREER_COPILOT_SEARCH_PROVIDER=firecrawl` or scraping discovery candidates.
- `CAREER_COPILOT_FIRECRAWL_API_KEY` for authenticated Firecrawl services. It is required when Firecrawl Search is the selected search provider.
- `CAREER_COPILOT_GEMINI_API_KEY` for job analysis and the resume-based Gemini features.

For a hosted Firecrawl service, configure its base URL and API key in your local `backend/.env`; the key stays server-side. Check the provider's current plan, terms, and API compatibility before using it. The application will not bypass sign-in requirements, access controls, or bot protections on job sites.

See [backend/.env.example](backend/.env.example) and [frontend/.env.example](frontend/.env.example) for the environment-variable templates. The root `.env.example` is only a pointer to those files.

Important backend settings use the `CAREER_COPILOT_` prefix:

| Variable | Purpose |
| --- | --- |
| `CAREER_COPILOT_DATABASE_URL` | PostgreSQL SQLAlchemy URL; set locally in `backend/.env`. |
| `CAREER_COPILOT_SEARCH_PROVIDER` | `searxng` (default) or `firecrawl`. |
| `CAREER_COPILOT_SEARXNG_BASE_URL` | SearXNG service base URL. |
| `CAREER_COPILOT_FIRECRAWL_BASE_URL` | Firecrawl service base URL for search and scrape. |
| `CAREER_COPILOT_FIRECRAWL_API_KEY` | Server-side Firecrawl credential; required for Firecrawl Search. |
| `CAREER_COPILOT_GEMINI_API_KEY` | Server-side Gemini credential for AI agent calls. |
| `CAREER_COPILOT_GEMINI_MODEL` | Gemini model identifier. |
| `CAREER_COPILOT_ENABLE_RESUME_AI` | Enables resume upload and resume-based Gemini features; production defaults off. |
| `CAREER_COPILOT_DISCOVERY_DAILY_LOCAL_LIMIT` / `CAREER_COPILOT_DISCOVERY_DAILY_GLOBAL_LIMIT` | Per-installation and shared daily provider-operation limits. |
| `CAREER_COPILOT_JOB_FRESHNESS_DAYS` / `CAREER_COPILOT_JOB_VERIFICATION_COOLDOWN_SECONDS` | Job freshness and per-job verification cooldown. |
| `CAREER_COPILOT_CORS_ORIGINS` | Browser origins allowed by the API; production defaults to no cross-origin origins. |
| `CAREER_COPILOT_ENVIRONMENT` / `CAREER_COPILOT_DEBUG` / `CAREER_COPILOT_LOG_LEVEL` / `CAREER_COPILOT_APP_NAME` / `CAREER_COPILOT_APP_VERSION` | Runtime identity, environment, and logging behavior. |
| `VITE_API_BASE_URL` | Frontend build-time API base URL; blank for the Vite proxy or same-origin build. |

## Database migrations and tests

Run backend commands from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Before applying this migration to an existing development database, take a
PostgreSQL backup. The desktop runtime creates a pre-upgrade backup
automatically. The local-profile migration removes obsolete account and
session tables after preserving candidate-linked records; restoring the backup
is the supported way to recover those removed authentication records.

The backend test suite uses PostgreSQL-specific behavior. **Tests can create databases and drop/recreate tables**, and migration tests create a separate scratch database. Use a disposable PostgreSQL instance and a test-only role/database that may be destroyed; never point `TEST_DATABASE_URL` at a production or valuable development database.

Example for the disposable local PostgreSQL container above (set this only in the test PowerShell process):

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://career_copilot:career_copilot@localhost:5432/career_copilot_test"
.\.venv\Scripts\python.exe -m pytest -ra
Remove-Item Env:TEST_DATABASE_URL
```

The test role must be allowed to create the migration scratch database. `TEST_DATABASE_URL` must name a disposable database on the same disposable PostgreSQL instance; the fixture derives its migration-test database name from it. Tests mock provider transports and use fake clients, so they do not need real Gemini, Firecrawl, or SearXNG credentials.

Run frontend checks from `frontend/`:

```powershell
npm run build
npm test
```

## Docker image

The root `Dockerfile` builds the React bundle and runs the FastAPI app in a non-root container. It does not provision PostgreSQL or search/crawl providers. To build the app image:

```powershell
docker build -t ai-career-copilot:local .
```

The image build is retained as a packaging check. The API binds to loopback inside the container and is not a supported network or hosted deployment. The desktop runtime is the intended application boundary.

## Security and privacy

- Never commit `.env` files, provider keys, database credentials, real resumes, database dumps, or user uploads. Use synthetic data in issues and tests.
- The local API has no user authentication. It binds to loopback, permits only the supported local development browser origins, and rejects unexpected browser origins and cross-site requests. This is a local application boundary, not a security boundary between processes running as the same Windows user; do not expose the API on a network or use it as a shared service.
- Resume uploads are parsed locally and stored as extracted text in PostgreSQL. Uploading does not itself call Gemini. Job matching and cover-letter generation send resume-derived content to the configured Gemini service; job analysis sends job information. Review your provider's data handling and use non-sensitive test resumes when evaluating the project.
- Discovery sends the search query to the configured search provider and candidate pages to the configured crawl provider. Saved job/resume content remains in your configured database.
- Daily provider-operation quotas protect discovery and explicit job verification. They are application-level budgets, not guarantees about a provider's billing or request accounting.
- The source registry is intentionally conservative. Unsupported employer sites are skipped; recognition of an ATS host alone does not establish that every URL is a vacancy.
- This is a local portfolio application, not a production security certification. Protect the Windows account and local data directory, configure secrets, and keep backups appropriately.

## Limitations and troubleshooting

- No provider services are started by the app. If discovery fails, verify PostgreSQL, the configured search provider, Firecrawl Scrape, and their network reachability from the backend process.
- If the frontend loads but API requests fail, check that FastAPI is running on port `8000`; Vite proxies `/api`, `/health`, and API docs paths there.
- If Alembic cannot connect, check `CAREER_COPILOT_DATABASE_URL` in `backend/.env` and confirm the database exists and is reachable.
- If an AI action reports a provider/configuration error, set `CAREER_COPILOT_GEMINI_API_KEY`; resume-based features can also be disabled with `CAREER_COPILOT_ENABLE_RESUME_AI`.
- Scanned/image-only PDFs need OCR, which is not included. Resume uploads are limited to 2 MiB.
- Agent calls run synchronously in the API request. There is no queue, scheduled discovery, automatic same-site traversal, interview preparation feature, or analytics dashboard.

## Demo

No screenshots or hosted demo are currently included. Follow the local setup above to run the application.

## License

This project is licensed under the [MIT License](LICENSE). It applies to project material the copyright holder is entitled to license. Third-party dependencies and other externally sourced material retain their own licenses and terms; generated or user-provided content is not claimed by this notice.

## Project documentation

- [Project specification](PROJECT_SPECIFICATION.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
