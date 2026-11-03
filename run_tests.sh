#!/usr/bin/env bash
set -e

COMPOSE_FILE="${1:-docker-compose.test.yml}"

# Ensure the testing pipeline is pulled down when the tests finish (success or error)
cleanup() {
  local exit_code=$?
  echo ""
  echo "=== Pulling down testing pipeline ==="
  docker compose -f "$COMPOSE_FILE" down --volumes --remove-orphans
  exit $exit_code
}

trap cleanup EXIT INT TERM

echo "=== Building testing pipeline image ==="
docker compose -f "$COMPOSE_FILE" build lint

echo "=== Running Pylint testing pipeline ==="
docker compose -f "$COMPOSE_FILE" up --abort-on-container-exit --exit-code-from lint

