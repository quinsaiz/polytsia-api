#!/bin/bash

set -e

log() { echo "[entrypoint] $*"; }

prepare_app() {
  log "Running Alembic migrations..."
  alembic upgrade head
}

wait-for-it --service "${POSTGRES_HOST}:${POSTGRES_PORT}" -- echo "[entrypoint] PostgreSQL is up"
if [ "$1" != "backend" ]; then
  wait-for-it --service "${REDIS_HOST}:${REDIS_PORT}" -- echo "[entrypoint] Redis is up"
fi

if [ "$1" = "backend" ]; then
  prepare_app
  log "Starting Uvicorn..."
  reload_args=()
  if [ "${RELOAD:-false}" = "true" ]; then
    reload_args+=(--reload)
  fi

  exec uvicorn src.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    "${reload_args[@]}"
elif [ "$1" = "celery_worker" ]; then
  log "Starting Celery Worker..."
  exec celery -A src.celery_app worker --loglevel=info
elif [ "$1" = "celery_beat" ]; then
  log "Starting Celery Beat..."
  exec celery -A src.celery_app beat --loglevel=info --schedule=/tmp/polytsia-celerybeat-schedule
elif [ "$1" = "bootstrap_recommendations" ]; then
  log "Queueing missing recommendation pools..."
  exec python -m src.recommendations.bootstrap
else
  log "Custom command detected, executing: $*"
  exec "$@"
fi
