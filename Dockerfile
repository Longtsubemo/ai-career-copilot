# Build the static React application using the committed npm lockfile.
FROM node:22.19.0-bookworm-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Keep the runtime image limited to Python, backend dependencies, and built assets.
FROM python:3.13.7-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend \
    CAREER_COPILOT_ENVIRONMENT=production \
    PORT=10000

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/backend/requirements.txt \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app

COPY backend/app/ /app/backend/app/
COPY backend/alembic/ /app/backend/alembic/
COPY backend/alembic.ini /app/backend/alembic.ini
COPY --from=frontend-build /build/frontend/dist/ /app/frontend/dist/

WORKDIR /app/backend
USER 10001:10001
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
