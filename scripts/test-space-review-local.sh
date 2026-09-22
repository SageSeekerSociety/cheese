#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export CHEESEX_DEV_DB_DIR="$PWD/tmp/space-review-db"
export CHEESEX_DEV_PG_PORT=55439
export CHEESEX_DEV_REDIS_PORT=56389
export CHEESE_CI_SLOT=space_review
eval "$(bash .claude/scripts/dev-db.sh start)"
cd backend
.venv/bin/python -m pytest "$@"
