#!/usr/bin/env bash
# Bring the fusion demo up: backend :8799 + vite :5200.
#
# This is the SEEDED DEMO, deliberately on its own ports so it can run beside
# `task dev` (:8081 + :3000, the everyday flow — see README "Quick Start").
# Its backend reads backend/.env like any other, so point DATABASE_URL at
# fusion_test to get the demo data.
#
# Assumes the repo-root `docker compose up -d` stack is running and fusion_test
# exists (else run scripts/dev/db-reset.sh first).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
bash "$ROOT/scripts/dev/backend.sh"
cd "$ROOT/frontend"
if ! curl -sf http://localhost:5200/ >/dev/null 2>&1; then
  nohup npx vite --port 5200 --host > /tmp/fusion-vite.log 2>&1 &
  for i in $(seq 1 15); do sleep 1; curl -sf http://localhost:5200/ >/dev/null 2>&1 && break; done
fi
curl -sf http://localhost:5200/ >/dev/null 2>&1 && echo "vite :5200 up" || { echo "vite failed"; tail -5 /tmp/fusion-vite.log; }
echo "demo up -> http://localhost:5200  (login: alice / demo12345)"
