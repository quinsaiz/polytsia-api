#!/bin/bash

set -e

log() { echo "[entrypoint] $*"; }

prepare_app() {
  log "Running Alembic migrations..."
  alembic upgrade head
}

wait-for-it --service "${POSTGRES_HOST}:${POSTGRES_PORT}" -- echo "[entrypoint] PostgreSQL is up"
wait-for-it --service "${REDIS_HOST}:${REDIS_PORT}" -- echo "[entrypoint] Redis is up"

if [ "$1" = "backend" ]; then
  prepare_app
  log "Starting Uvicorn..."

  exec uvicorn src.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    ${RELOAD:+--reload}
else
  log "No valid argument provided, defaulting to infinite sleep..."
  exec sleep infinity
fi
