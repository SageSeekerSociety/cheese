#!/usr/bin/env bash
# A release of a commit from before the two slots per service — a revert, or a
# manual dispatch of an older SHA — runs that commit's deploy-docker.sh, which
# installs its own app-router.conf. This runs the real script of the last such
# commit, with its own fakes, against the app-router state the slots leave, and
# checks that the box's :8080 and :80 stay served.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# The last commit before the two slots. Pinned: once they are on main, main is
# no longer such a commit.
OLD_REF="${PRE_SLOT_REF:-b01c3165581faa90b2db93b5a776ed1b779b4240}"
REAL_PYTHON="$(command -v python3)"
fail() { echo "FAIL: $*" >&2; exit 1; }

git -C "$ROOT" cat-file -e "$OLD_REF^{commit}" 2>/dev/null \
  || git -C "$ROOT" fetch -q --depth=1 origin "$OLD_REF"
mkdir -p "$ROOT/.tmp"
work="$(mktemp -d "$ROOT/.tmp/pre-slot.XXXXXX")"
trap 'rm -rf "$work"' EXIT
git -C "$ROOT" archive "$OLD_REF" deploy | tar -x -C "$work"
OLD="$work/deploy"

# Runs the old script against an active directory the slots left with
# backend.conf on $1 and frontend.conf on $2, carrying the box's ports.
old_release() {
  local run="$1" backend="$2" frontend="$3"
  mkdir -p "$run/active"
  printf 'FRONTEND_URL=https://cheese.example\n' > "$run/env"
  cp "$ROOT/deploy/llm-tunnel/app-router.conf" "$run/active/app-router.conf"
  cp "$OLD/llm-tunnel/nginx.conf" "$run/nginx.conf"
  bash "$OLD/llm-tunnel/configure-preview.sh" owner "$run/active" 18087 >/dev/null
  bash "$OLD/llm-tunnel/configure-frontend.sh" "$run/active" 18086 18080 18087
  printf 'upstream backend_active { server 127.0.0.1:%s; }\n' "$backend" > "$run/active/backend.conf"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend-upstream.sh" "$run/active" "$frontend" 8080 0.0.0.0:80
  : > "$run/docker.log"
  PATH="$OLD/tests/fakes/app-tier:$PATH" \
    APP_TIER_REAL_PYTHON="$REAL_PYTHON" APP_TIER_SCENARIO=healthy APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$run/docker.log" ACTIVE_BACKEND_DIR="$run/active" ACTIVE_FRONTEND_DIR="$run/active" \
    API_FRONT_CONF="$run/nginx.conf" BACKEND_PORT=18081 BACKEND_PORT_NEXT=18082 \
    DEPLOY_DRAIN_SECONDS=0 DEPLOY_BACKEND_START_TIMEOUT=3 DEPLOY_HEALTH_ATTEMPTS=1 DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    BACKEND_ENV_FILE="$run/env" FORGE_EVENTS_ENV_FILE="$run/env.events" FORGEJO_URL=https://cheese.example/forge/ \
    CLAUDE_CACHE_HOST_PATH="$run/cc" PI_CACHE_HOST_PATH="$run/pc" APPHOME_HOST_PATH="$run/ah" HOME="$run" \
    bash "$OLD/deploy-docker.sh" testsha "$OLD/compose/docker-compose.base.yml" > "$run/release.log" 2>&1
}

# The `-b` slots serve: the old script refuses at its backend check, after it
# has installed its app-router.conf and reloaded. That config includes
# frontend.conf, which still carries the ports.
run="$work/b"
if old_release "$run" 18082 18084; then fail "the old script switched while the -b slots serve"; fi
grep -q 'backend router is still on :18082' "$run/release.log" || { cat "$run/release.log"; fail "the old script stopped for another reason"; }
grep -q 'exec cheese-app-router nginx -s reload' "$run/docker.log" || fail "the old script did not reload app-router (the case this guards)"
grep -Fq 'include /etc/nginx/active/frontend.conf;' "$run/active/app-router.conf" || fail "the old app-router.conf does not include frontend.conf"
grep -Fq 'listen 0.0.0.0:8080;' "$run/active/frontend.conf" && grep -Fq 'listen 0.0.0.0:80;' "$run/active/frontend.conf" \
  || fail "the box's ports are gone after the old script's refusal"
echo "PASS: an older commit's release that refuses while the -b slots serve leaves the box's ports served"

# The first slots serve: the old script releases. It drops the ports at its
# frontend switch and recreates the compose frontend on them after its drain.
run="$work/a"
old_release "$run" 18081 18088 || { cat "$run/release.log"; fail "the old script could not release from the first slots"; }
recreate="$(grep -n ' up -d --no-deps frontend$' "$run/docker.log" | head -n 1 | cut -d: -f1)"
[ -n "$recreate" ] || fail "the old script never recreated the compose frontend"
[ "$(sed -n "$((recreate - 1))p" "$run/docker.log")" = 'sleep 31' ] \
  && [ "$(sed -n "$((recreate - 2))p" "$run/docker.log")" = 'exec cheese-app-router nginx -s reload' ] \
  || fail "the compose frontend was not recreated right after the reload and drain that dropped the ports"
grep -Fqx 'upstream frontend_active { server 127.0.0.1:8080; }' "$run/active/frontend.conf" \
  || fail "the old script did not end on the compose frontend: $(cat "$run/active/frontend.conf")"
echo "PASS: an older commit's release from the first slots hands the box's ports back to the compose frontend"
