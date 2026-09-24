FROM python:3.12-slim

# Railway injects $PORT. uvicorn listens on it at runtime.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for psycopg3 (libpq) and httpx TLS
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for cache layer
COPY apps/api/requirements.txt ./apps/api/requirements.txt
RUN pip install --no-cache-dir -r apps/api/requirements.txt

# Copy application source (alembic + app + alembic.ini at root)
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY apps/api ./apps/api

# Make `app.*` importable from /app/apps/api
ENV PYTHONPATH=/app/apps/api

EXPOSE 8000

# Railway sets $PORT. Alembic upgrades schema, then uvicorn serves the API.
# Show env + resolved settings.database_url for diagnostics.
CMD sh -c 'echo "[start] DATABASE_URL len=$(echo -n "$DATABASE_URL" | wc -c) head=$(echo -n "$DATABASE_URL" | head -c 25)" && python -c "import sys; sys.path.insert(0, \"/app/apps/api\"); from app.core.config import settings; print(\"[start] settings.database_url scheme=\", settings.database_url.split(\":\")[0])" && alembic -c alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'
