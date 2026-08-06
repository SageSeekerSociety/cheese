#!/usr/bin/env bash
# Recreate the fusion demo DB (fusion_test) from scratch: drop -> create ->
# alembic upgrade head -> seed demo project.
#
# WHY drop+recreate (not just `alembic upgrade`): alembic tracks a migration by
# its REVISION ID, not its content. If you EDIT a migration file after it has
# already been applied (same revision id), `upgrade head` sees the DB is already
# at that revision and does NOTHING — your edit is silently ignored. The only
# way to pick up an edited migration is to drop the DB and upgrade fresh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)/backend"
PG=cheesex-pg   # postgres docker container (maps host :5433)
docker exec -e PGPASSWORD=cheesex "$PG" psql -U cheesex -d postgres \
  -c "DROP DATABASE IF EXISTS fusion_test WITH (FORCE);" >/dev/null
docker exec -e PGPASSWORD=cheesex "$PG" psql -U cheesex -d postgres \
  -c "CREATE DATABASE fusion_test OWNER cheesex;" >/dev/null
cd "$ROOT"
env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
  .venv/bin/alembic upgrade head
env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
  PYTHONPATH=. .venv/bin/python scripts/seed_fusion_demo.py
echo "fusion_test reset + seeded. (backend restart picks it up: scripts/dev/backend.sh)"
