#!/usr/bin/env bash
set -euo pipefail

COMPOSE="docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml"

echo "==> Pulling latest..."
git pull

# The nginx build container has no `.git` of its own (only `web/` is copied
# in), so the sha has to be computed here, on the host, and threaded through
# as a build arg — see nginx/Dockerfile and docker-compose.prod.yml.
export VITE_GIT_SHA="$(git rev-parse --short HEAD)"

echo "==> Building images..."
$COMPOSE build

echo "==> Starting services..."
$COMPOSE up -d

echo "==> Restarting workers to pick up code changes..."
$COMPOSE restart worker api

echo "==> Running migrations..."
$COMPOSE exec api alembic upgrade head

echo "==> Done."
$COMPOSE ps
