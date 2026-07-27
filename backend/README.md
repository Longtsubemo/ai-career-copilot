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
CAREER_COPILOT_DATABASE_URL=sqlite:///./career_copilot.db
CAREER_COPILOT_LOG_LEVEL=INFO
```
