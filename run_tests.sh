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

echo "=== Building testing pipeline images ==="
docker compose -f "$COMPOSE_FILE" build lint test

echo "=== Running Pylint quality gate (target: 10.00/10) ==="
docker compose -f "$COMPOSE_FILE" run --rm lint

echo ""
echo "=== Running Pytest suite (Unit + BDD + E2E, target: >= 90% coverage) ==="
docker compose -f "$COMPOSE_FILE" run --rm test
