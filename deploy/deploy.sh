#!/usr/bin/env bash
# deploy.sh — Deploy or update production (backend + frontend)
# Usage: ./deploy.sh [image_tag]
#   image_tag: git SHA or branch name (default: reads from .env.prod IMAGE_TAG)
#
# Steps:
#   1. Pull latest images (backend + frontend)
#   2. Run database migrations
#   3. Restart backend and frontend (infrastructure untouched)
#   4. Health check
set -euo pipefail

cd "$(dirname "$0")"

IMAGE_TAG="${1:-}"
if [ -n "$IMAGE_TAG" ]; then
    echo "Deploying image tag: $IMAGE_TAG"
    export IMAGE_TAG
fi

echo "==> Pulling latest images..."
docker compose -f docker-compose.prod.yml pull backend frontend

echo "==> Pulling agent sandbox image..."
# Sibling-container image for 芝士 turns; referenced by SANDBOX_IMAGE in the
# compose file. Pulled onto the HOST dockerd (that's where turns run).
docker pull "ghcr.io/sageseekersociety/cheese/sandbox:${IMAGE_TAG:-main}" \
    || echo "  WARNING: sandbox image pull failed — agent turns will be unavailable"

echo "==> Ensuring workspace root..."
# Must exist host-side before compose bind-mounts it (same path in-container).
mkdir -p /opt/cheese-data/workspaces

echo "==> Running database migrations..."
# Plain alembic from the image's venv (on PATH) — the production image ships
# no pyproject, so `uv run` has no project to resolve.
docker compose -f docker-compose.prod.yml run --rm backend \
    sh -c "alembic upgrade head"

echo "==> Restarting backend + frontend..."
docker compose -f docker-compose.prod.yml up -d --no-deps backend frontend

echo "==> Waiting for health check..."
sleep 5

FAIL=0
if docker compose -f docker-compose.prod.yml exec backend curl -sf http://localhost:8081/healthz > /dev/null 2>&1; then
    echo "  Backend: healthy"
else
    echo "  Backend: FAILED"
    FAIL=1
fi

if docker compose -f docker-compose.prod.yml exec frontend curl -sf http://localhost:80 > /dev/null 2>&1; then
    echo "  Frontend: healthy"
else
    echo "  Frontend: FAILED"
    FAIL=1
fi

if [ "$FAIL" -gt 0 ]; then
    echo "==> WARNING: health check failed — check logs:"
    echo "    docker compose -f docker-compose.prod.yml logs backend frontend --tail 50"
    exit 1
fi

echo "$(date -Iseconds) ${IMAGE_TAG:-latest}" >> deploy.log
echo "==> Deploy SUCCESS"
