#!/usr/bin/env bash
# deploy.sh — Deploy or update the production backend
# Usage: ./deploy.sh [image_tag]
#   image_tag: git SHA or branch name (default: reads from .env.prod IMAGE_TAG)
#
# Steps:
#   1. Pull latest image
#   2. Run database migrations
#   3. Restart backend (infrastructure services untouched)
set -euo pipefail

cd "$(dirname "$0")"

IMAGE_TAG="${1:-}"
if [ -n "$IMAGE_TAG" ]; then
    echo "Deploying image tag: $IMAGE_TAG"
    export IMAGE_TAG
fi

echo "==> Pulling latest images..."
docker compose -f docker-compose.prod.yml pull backend

echo "==> Running database migrations..."
docker compose -f docker-compose.prod.yml run --rm backend \
    sh -c "uv run alembic upgrade head"

echo "==> Restarting backend..."
docker compose -f docker-compose.prod.yml up -d --no-deps backend

echo "==> Waiting for health check..."
sleep 5
if docker compose -f docker-compose.prod.yml exec backend curl -sf http://localhost:8081/healthz > /dev/null 2>&1; then
    echo "==> Deploy SUCCESS — backend is healthy"
else
    echo "==> WARNING: health check failed — check logs with:"
    echo "    docker compose -f docker-compose.prod.yml logs backend --tail 50"
    exit 1
fi

echo "$(date -Iseconds) $IMAGE_TAG" >> deploy.log
echo "==> Done"
