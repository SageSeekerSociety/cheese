#!/usr/bin/env bash
# redeploy — bring the RUNNING platform up to the code in this working tree.
# The missing last link of the bootstrap loop (自举): after 芝士's accepted work
# is bridged back to this repo, run this and the platform IS the new version.
#
# Repo tooling, same layer as dogfood-bridge.sh — product code never calls it.
# When the platform grows a real 发布/release concept (spec §6.3 预留), this
# script's interface stays and its internals become "build images + swap".
#
# Usage:
#   scripts/redeploy.sh            # deps + migrate + restart backend (+ frontend if down)
#
# Idempotent: safe to run twice in a row. Logs to tmp_redeploy.log (and stdout).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/tmp_redeploy.log"
PORT=8099
FRONTEND_PORT=5173

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
die() { log "FAIL: $*"; exit 1; }

log "=== redeploy start (HEAD $(git -C "$ROOT" rev-parse --short HEAD)) ==="

# 1. Backend deps — no-op when the lockfile hasn't changed.
log "uv sync (backend deps)"
(cd "$ROOT/backend" && uv sync --frozen >>"$LOG" 2>&1) || die "uv sync failed (see $LOG)"

# 2. DB migrations — no-op when already at head.
log "alembic upgrade head"
(cd "$ROOT/backend" && uv run alembic upgrade head >>"$LOG" 2>&1) \
  || die "alembic upgrade failed (see $LOG)"

# 3. Drain: wait for in-flight agent turns so the restart never kills 芝士
#    mid-work (turn state lives in the backend process). Bounded wait — after
#    120s we restart anyway and say so.
if curl -s -m 2 "http://localhost:$PORT/health" >/dev/null; then
  for i in $(seq 1 120); do
    ACTIVE="$(curl -s -m 2 "http://localhost:$PORT/health" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("active_turns", 0))' \
      2>/dev/null || echo 0)"
    [[ "$ACTIVE" == "0" ]] && { log "drain: no active turns"; break; }
    [[ $i -eq 1 ]] && log "drain: $ACTIVE active turn(s), waiting (max 120s)…"
    [[ $i -eq 120 ]] && log "drain: still $ACTIVE active after 120s — restarting anyway"
    sleep 1
  done
fi

# 4. Restart the backend.
log "restart backend on :$PORT"
pkill -f "uvicorn app.main:app.*--port $PORT" 2>/dev/null || true
sleep 1
# start_new_session detaches the daemon COMPLETELY (own session + process
# group, stdio to the log): a caller that pipes this script or kills our
# process group on timeout can no longer take the backend down with it.
python3 - "$ROOT" "$PORT" <<'PYEOF'
import subprocess, sys
root, port = sys.argv[1], sys.argv[2]
subprocess.Popen(
    ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port],
    cwd=f"{root}/backend",
    stdout=open(f"{root}/tmp_backend.log", "a"),
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
PYEOF

# 5. Health check — a redeploy that leaves the platform dead must fail loudly.
for i in $(seq 1 20); do
  sleep 1
  if curl -s -m 2 -o /dev/null "http://localhost:$PORT/api/projects"; then
    log "backend healthy after ${i}s"
    break
  fi
  [[ $i -eq 20 ]] && die "backend not healthy after 20s (tail $ROOT/tmp_backend.log)"
done

# 6. Frontend: vite dev hot-reloads on its own; just make sure it's running.
if ! curl -s -m 2 -o /dev/null "http://localhost:$FRONTEND_PORT"; then
  log "frontend down — starting vite dev server"
  python3 - "$ROOT" <<'PYEOF'
import subprocess, sys
root = sys.argv[1]
subprocess.Popen(
    ["npm", "run", "dev"],
    cwd=f"{root}/frontend",
    stdout=open(f"{root}/tmp_frontend.log", "a"),
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
PYEOF
  sleep 3
  curl -s -m 2 -o /dev/null "http://localhost:$FRONTEND_PORT" \
    || log "WARN: frontend still not answering (check $ROOT/tmp_frontend.log)"
else
  log "frontend already up (vite hot-reloads by itself)"
fi

log "=== redeploy done: platform now runs $(git -C "$ROOT" rev-parse --short HEAD) ==="
