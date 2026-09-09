#!/usr/bin/env bash
set -euo pipefail

# Select only Cheese backends explicitly participating in this timer.
# Database operation locks arbitrate overlapping deployment generations.
containers=$(docker ps --filter label=li.zhifei.cheese.room-cleanup=true --format '{{.ID}}')
if [ -z "$containers" ]; then
  echo "$(date -u +%FT%TZ) no room-cleanup backend is running" >&2
  exit 1
fi
failed=0
for container in $containers; do
  if ! docker exec "$container" /app/.venv/bin/python -m app.domain.topic.cleanup_trigger; then
    echo "$(date -u +%FT%TZ) cleanup trigger failed container=$container" >&2
    failed=1
  fi
done
exit "$failed"
