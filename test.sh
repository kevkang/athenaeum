#!/usr/bin/env bash
# Run Athenaeum's local tests: the automated checks CI runs, then the full
# stack for manual testing in a browser. See testing_readme.md.
#
#   ./test.sh               checks, then start the stack and the frontend
#   ./test.sh --checks-only checks only (needs just Postgres)
#   ./test.sh --stack-only  start the stack and the frontend, skip checks
#
# Stop everything with ./test_shutdown.sh.
set -euo pipefail

cd "$(dirname "$0")"

run_checks=1
run_stack=1
case "${1:-}" in
  --checks-only) run_stack=0 ;;
  --stack-only) run_checks=0 ;;
  "") ;;
  *) echo "usage: $0 [--checks-only | --stack-only]" >&2; exit 2 ;;
esac

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

tmp="${TMPDIR:-/tmp}"
VITE_LOG="${tmp%/}/athenaeum-vite.log"

# ── Databases: needed by both the checks and the app ────────────────────────

# The same two services CI provides: Postgres for nearly every DB-backed
# test, Neo4j for the graph-sync test. Nothing else is needed for the checks.
step "Starting Postgres and Neo4j"
docker compose up -d postgres neo4j
until docker compose exec -T postgres pg_isready -U athenaeum >/dev/null 2>&1; do
  sleep 1
done
# Neo4j takes several seconds to accept queries after its container starts.
until docker compose exec -T neo4j cypher-shell -u neo4j -p athenaeum "RETURN 1" >/dev/null 2>&1; do
  sleep 2
done

step "Applying migrations"
.venv/bin/python -m alembic upgrade head

# ── Automated checks, in CI's order, then the frontend's ────────────────────

if [[ $run_checks == 1 ]]; then
  step "Backend: ruff";   .venv/bin/ruff check .
  step "Backend: mypy";   .venv/bin/mypy src
  step "Backend: pytest"; .venv/bin/python -m pytest -q
  step "Frontend: typecheck"; (cd web && npx tsc -b --noEmit)
  step "Frontend: lint";      (cd web && npm run lint)
  step "All checks passed"
fi

# ── The full stack, for testing in a browser ────────────────────────────────

if [[ $run_stack == 1 ]]; then
  # --build picks up dependency and Dockerfile changes; it's a no-op otherwise.
  step "Starting the full stack"
  docker compose up -d --build

  # The api reloads on code changes, but the worker doesn't: without a restart
  # it keeps running whatever code it started with, and Nucleate runs use it.
  step "Restarting the worker so it runs the current code"
  docker compose restart worker

  if lsof -ti:5173 -sTCP:LISTEN >/dev/null 2>&1; then
    step "Frontend already running on port 5173"
  else
    step "Starting the frontend (log: $VITE_LOG)"
    # The redirect covers the whole group and exec leaves no subshell behind,
    # so nothing keeps this script's stdout open once it exits: a caller
    # reading that output (a pipe, a CI log) would otherwise wait forever.
    (cd web && exec nohup npm run dev) >"$VITE_LOG" 2>&1 </dev/null &
    for _ in $(seq 30); do
      lsof -ti:5173 -sTCP:LISTEN >/dev/null 2>&1 && break
      sleep 1
    done
    lsof -ti:5173 -sTCP:LISTEN >/dev/null 2>&1 \
      || { echo "Frontend didn't start; see $VITE_LOG" >&2; exit 1; }
  fi

  step "Ready"
  docker compose ps --format 'table {{.Service}}\t{{.Status}}'
  cat <<EOF

  App:         http://localhost:5173
  Worker log:  docker compose logs -f worker
  Stop:        ./test_shutdown.sh
EOF
fi
