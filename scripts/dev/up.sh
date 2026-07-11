#!/usr/bin/env bash
# Bring the fusion demo up: backend :8799 + vite :5200. Assumes docker PG
# (cheesex-pg) + redis are already running and fusion_test exists (else run
# scripts/dev/db-reset.sh first).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
bash "$ROOT/scripts/dev/backend.sh"
cd "$ROOT/frontend"
if ! curl -sf http://localhost:5200/ >/dev/null 2>&1; then
  nohup npx vite --port 5200 --host > /tmp/fusion-vite.log 2>&1 &
  for i in $(seq 1 15); do sleep 1; curl -sf http://localhost:5200/ >/dev/null 2>&1 && break; done
fi
curl -sf http://localhost:5200/ >/dev/null 2>&1 && echo "vite :5200 up" || { echo "vite failed"; tail -5 /tmp/fusion-vite.log; }
echo "demo up -> http://localhost:5200  (login: alice / cheese2025)"
