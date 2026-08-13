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
# The repo-root docker-compose.yml's dev Postgres (host :5432). There used to be
# a second compose under backend/ publishing its own PG as `cheesex-pg` on :5433
# — which collided with the root stack's TEST database on that same port, and
# ran stock postgres where the app wants ParadeDB's pg_search. One server now;
# the demo lives in its own database beside the dev one.
PG="${DEV_PG_CONTAINER:-cheese_py_postgres}"
docker exec -e PGPASSWORD=postgres "$PG" psql -U postgres -d postgres \
  -c "DROP DATABASE IF EXISTS fusion_test WITH (FORCE);" >/dev/null
docker exec -e PGPASSWORD=postgres "$PG" psql -U postgres -d postgres \
  -c "CREATE DATABASE fusion_test OWNER postgres;" >/dev/null
cd "$ROOT"
env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
  .venv/bin/alembic upgrade head
env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
  PYTHONPATH=. .venv/bin/python scripts/seed_fusion_demo.py
echo "fusion_test reset + seeded. (backend restart picks it up: scripts/dev/backend.sh)"
