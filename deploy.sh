#!/usr/bin/env bash
set -euo pipefail

COMPOSE="docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml"

echo "==> Pulling latest..."
git pull

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
