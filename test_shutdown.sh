#!/usr/bin/env bash
# Shut down what ./test.sh started: the frontend dev server and the Docker
# stack (worker, api, databases). See "Shutting down" in testing_readme.md.
#
#   ./test_shutdown.sh         stop containers; data kept, restart is instant
#   ./test_shutdown.sh --down  also remove containers (after editing
#                              docker-compose.yml); data kept
#   ./test_shutdown.sh --wipe  also delete volumes: every user, paper and
#                              project in the local database. Asks first.
set -euo pipefail

cd "$(dirname "$0")"

mode="${1:-stop}"
case "$mode" in
  stop|--down|--wipe) ;;
  *) echo "usage: $0 [--down | --wipe]" >&2; exit 2 ;;
esac

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# npm doesn't pass signals on to the Vite process it spawns, so stop the
# frontend by whatever is listening on its port, not by process name.
step "Stopping the frontend"
pids=$(lsof -ti:5173 -sTCP:LISTEN || true)
if [[ -n $pids ]]; then
  echo "$pids" | xargs kill
else
  echo "Not running."
fi

case "$mode" in
  stop)
    step "Stopping containers (data kept)"
    docker compose stop
    ;;
  --down)
    step "Removing containers (data kept)"
    docker compose down
    ;;
  --wipe)
    read -r -p "Delete ALL local data (database, graph, uploaded PDFs)? Type 'wipe' to confirm: " answer
    if [[ $answer != wipe ]]; then
      echo "Cancelled; nothing deleted."
      exit 1
    fi
    step "Removing containers and volumes"
    docker compose down -v
    ;;
esac

step "Checking for leftovers"
sleep 1
still_up=$(docker compose ps --status running -q)
ports=$(lsof -ti:5173,8000,5432,7687,6379,8070,9000 -sTCP:LISTEN || true)
if [[ -z $still_up && -z $ports ]]; then
  echo "All stopped."
else
  [[ -n $still_up ]] && docker compose ps --status running
  [[ -n $ports ]] && { echo "Still listening:"; lsof -i:5173,8000,5432,7687,6379,8070,9000 -sTCP:LISTEN; }
  exit 1
fi
