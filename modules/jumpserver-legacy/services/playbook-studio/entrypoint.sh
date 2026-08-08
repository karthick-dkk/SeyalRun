#!/bin/sh
set -e

echo "[entrypoint] Running Alembic migrations..."
python -m alembic upgrade head

echo "[entrypoint] Starting Playbook Studio on port ${PS_PORT:-8005}..."
LOG_LEVEL=$(echo "${PS_LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')
exec uvicorn app.main:app \
    --host "${PS_HOST:-0.0.0.0}" \
    --port "${PS_PORT:-8005}" \
    --log-level "${LOG_LEVEL}" \
    --no-access-log
