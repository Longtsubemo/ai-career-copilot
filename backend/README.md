# AI Career Copilot Backend

FastAPI backend foundation for the AI Career Copilot project.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
uvicorn app.main:app --reload
```

The health endpoint is available at:

```text
GET http://127.0.0.1:8000/health
```

## Configuration

Settings are loaded from environment variables with the `CAREER_COPILOT_`
prefix. For example:

```text
CAREER_COPILOT_DATABASE_URL=postgresql+psycopg://career_copilot:career_copilot@127.0.0.1:5432/career_copilot
CAREER_COPILOT_LOG_LEVEL=INFO
```

PostgreSQL is required; SQLite is not a supported backend. For local
development, configure a developer-managed PostgreSQL database in `backend/.env`
and run Alembic explicitly before starting the API. See the root README for
the Docker-based development example.

The application has one local profile and no account, password, session, or
login API. The first profile request initializes a stable profile; after that,
profile, resume, application, and agent data use that candidate ID. The API
binds to loopback and rejects unexpected browser origins. It is not a shared
or network-facing service.

When upgrading a database with one candidate, the migration marks that row as
the local profile. If several candidates exist, it preserves all profiles and
their records and leaves profile selection unset. Start the backend once to
see the selection instructions, or list and select a profile from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m app.cli.select_local_profile --list
.\.venv\Scripts\python.exe -m app.cli.select_local_profile --candidate-id <ID>
```

The desktop runtime makes a pre-migration backup before upgrades. The new
local-profile migration keeps candidate IDs and application records, removes
the obsolete account/session tables, and aggregates old per-account discovery
quota counts into the local installation scope. Its downgrade intentionally
requires restoring a pre-upgrade backup because password and session data are
not recreated.

The Windows desktop developer shell is documented in
[`docs/DESKTOP_RUNTIME.md`](../docs/DESKTOP_RUNTIME.md). It keeps PostgreSQL
data under the user's Local AppData directory and keeps desktop configuration
separate from development `.env` settings. The Tauri shell uses the existing
backend virtual environment and does not produce an installer or portable
release bundle.
