#!/bin/bash

set -e

log() { echo "[entrypoint] $*"; }

uv run wait-for-it --service "${POSTGRES_HOST}:${POSTGRES_PORT}" -- echo "[entrypoint] PostgreSQL is up"
uv run wait-for-it --service "${REDIS_HOST}:${REDIS_PORT}" -- echo "[entrypoint] Redis is up"

if [ "$1" = "backend" ]; then
  log "Starting Uvicorn..."

  exec uv run uvicorn src.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    ${RELOAD:+--reload}
else
  log "No valid argument provided, defaulting to infinite sleep..."
  exec sleep infinity
fi
