#!/usr/bin/env bash
# Bring up / update the standing data-plane pair (#551). Run BY HAND, never
# from a deploy pipeline — these services exist precisely so that deploys
# cannot touch them.
#
# First-time cutover order (each step verifiable, each reversible):
#   1. Copy this directory to the box (e.g. ~/ops/llm-tunnel/) and write .env:
#        LLM_TUNNEL_IMAGE=<the backend image ref currently running>
#        BACKEND_ENV_FILE=/home/nictheboy/cheese-backend-py/backend/.env
#   2. bash up.sh llm-tunnel      # tunnel only; verify:
#        curl -fsS http://127.0.0.1:8091/healthz
#        curl -si http://127.0.0.1:8091/llm/tunnel | head -1   # 403 = route alive
#   3. Free :8081: add BACKEND_PORT=18081 to ~/ops/deploy.env, then rerun
#      deploy/deploy-docker.sh <current sha>. Backend now answers on :18081.
#   4. bash up.sh                 # api-front takes :8081; verify:
#        curl -fsS http://127.0.0.1:8081/healthz               # via nginx → backend
#        curl -si  http://127.0.0.1:8081/llm/tunnel | head -1  # 403 via nginx → tunnel
#      then watch a real machine turn's hooks land.
#   Rollback = remove BACKEND_PORT from deploy.env, redeploy,
#   `docker compose -p cheese-dataplane down`.
set -euo pipefail
cd "$(dirname "$0")"
# Seed the backend switch on a box that has never had one: point it at the
# compose backend's published port. deploy-docker.sh flips it from then on and
# always leaves it pointing back here, so an existing file is never overwritten.
ACTIVE_DIR="${ACTIVE_BACKEND_DIR:-./active}"
mkdir -p "$ACTIVE_DIR"
if [ ! -f "$ACTIVE_DIR/backend.conf" ]; then
  printf 'upstream backend_active { server 127.0.0.1:%s; }\n' "${BACKEND_PORT:-18081}" \
    > "$ACTIVE_DIR/backend.conf"
fi
exec docker compose -p cheese-dataplane --env-file .env -f compose.yml up -d "$@"
