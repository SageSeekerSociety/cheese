#!/usr/bin/env bash
# on-dogfood-push — the 采纳即上线 hook. The platform pushes accepted dogfood
# work to dogfood/<topic> and runs this script detached; we merge it into main,
# run the full checks, and redeploy. Red checks roll the merge back and stop.
#
# Invoked as: scripts/on-dogfood-push.sh <branch>
# Logs to tmp_dogfood_push.log (the platform appends our stdout there too).
# Serialized via a lock so two accepts can't merge concurrently.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${1:?usage: on-dogfood-push.sh <branch>}"
LOCK="$ROOT/.git/dogfood-push.lock"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$BRANCH] $*"; }

# mkdir is atomic (macOS has no flock). A lock whose owner died is stale — take
# it over. Wait up to 600s for a live owner to finish.
acquire_lock() {
  local i
  for i in $(seq 1 600); do
    if mkdir "$LOCK" 2>/dev/null; then
      echo $$ >"$LOCK/pid"
      trap 'rm -rf "$LOCK"' EXIT
      return 0
    fi
    local owner
    owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
    if [[ -n "$owner" ]] && ! kill -0 "$owner" 2>/dev/null; then
      rm -rf "$LOCK"
      continue
    fi
    sleep 1
  done
  return 1
}
if ! acquire_lock; then
  log "another push-back is still running after 600s — giving up"
  exit 1
fi

cd "$ROOT"
log "=== push-back start ==="

# A dirty dev tree means a human is mid-work here: merging would entangle
# their edits. Stop and leave the branch for them.
if [[ -n "$(git status --porcelain)" ]]; then
  log "SKIP: dev working tree is dirty — resolve manually: git merge $BRANCH"
  exit 0
fi

PRE="$(git rev-parse HEAD)"
CUR="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CUR" != "main" ]]; then
  log "SKIP: dev repo is on '$CUR', not main — merge $BRANCH manually"
  exit 0
fi

if ! git merge --no-ff -q -m "dogfood: merge accepted work ($BRANCH)" "$BRANCH"; then
  git merge --abort || true
  log "CONFLICT: $BRANCH does not merge cleanly — resolve manually"
  exit 1
fi
log "merged $BRANCH ($(git rev-parse --short HEAD))"

# Frontend deps first if the lockfile moved, so checks run against them.
if git diff --name-only "$PRE"..HEAD | grep -q "frontend/package-lock.json"; then
  log "package-lock changed — npm ci"
  (cd frontend && npm ci --silent) || { git reset --hard "$PRE"; log "ROLLBACK: npm ci failed"; exit 1; }
  # A RUNNING vite dev server keeps a stale module graph after deps change and
  # serves "Failed to resolve import" forever (bit us with @tiptap/extension-table).
  # Clear its cache and restart it.
  log "restarting vite (dep graph changed)"
  pkill -f "vite" || true
  rm -rf frontend/node_modules/.vite
  (cd frontend && mkdir -p ../logs && nohup npm run dev >../logs/vite.log 2>&1 &)
fi

run_checks() {
  (cd backend && uv run ruff check app tests) || return 1
  (cd backend && uv run pytest -q) || return 1
  (cd frontend && npx vue-tsc --noEmit) || return 1
  (cd frontend && npm test --silent) || return 1
}

log "running checks…"
if ! run_checks >>/dev/null 2>&1; then
  git reset --hard "$PRE"
  log "ROLLBACK: checks failed — main restored to ${PRE:0:8}; branch $BRANCH kept for debugging"
  exit 1
fi
log "checks green"

scripts/redeploy.sh
log "=== push-back done: platform now runs $(git rev-parse --short HEAD) ==="
