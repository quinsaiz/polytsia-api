#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="polytsia-test-$(date +%s)-$$"
compose=(docker compose -f "$repo_dir/docker-compose.test.yml" -p "$project")
compose_pid=

cleanup() {
  test_status=$?
  trap - EXIT INT TERM
  if [[ -n "$compose_pid" ]]; then
    if kill -0 "$compose_pid" 2>/dev/null; then
      kill -TERM "$compose_pid" 2>/dev/null || true
    fi
    wait "$compose_pid" 2>/dev/null || true
  fi
  if ! "${compose[@]}" down --volumes --remove-orphans --rmi local; then
    if ((test_status == 0)); then
      test_status=1
    fi
  fi
  exit "$test_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${compose[@]}" up --build --quiet-build --no-attach test-db --no-attach test-redis \
  --exit-code-from tests &
compose_pid=$!
if wait "$compose_pid"; then
  compose_pid=
  exit 0
else
  test_status=$?
  compose_pid=
  exit "$test_status"
fi
