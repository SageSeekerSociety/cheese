#!/usr/bin/env bash
# Called only by the independent gateway release workflow, after its build gate.
#
# Callers reach gateway-proxy (HAProxy) at 172.17.0.1:4000 and http://litellm:4000.
# Behind it, one of two LiteLLM instances serves: litellm-blue or litellm-green.
# A release starts the other one on the new image, waits until it is ready and
# the proxy sees it up, moves traffic to it through the proxy's runtime API
# (no proxy reload: every caller connection stays open, and its next request
# goes to the new color), then waits until the old color has no request left
# and stops it. A stream that started before the switch finishes on the old
# color. A new color that is not healthy never receives traffic; rolling back
# is releasing the previous SHA, which takes the same path.
set -euo pipefail

[[ "${GITHUB_ACTIONS:-}" = true ]] || {
  echo 'Use the Release gateway workflow.' >&2
  exit 1
}
sha="${1:?usage: release-gateway.sh <full-main-sha>}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 1
here="$(cd "$(dirname "$0")" && pwd)"
gateway_home="${GATEWAY_HOME:-$HOME/gateway}"
env_file="$gateway_home/compose/.env"
[[ -r "$env_file" ]] || { echo 'Gateway credentials file is missing.' >&2; exit 1; }
release_dir="$gateway_home/releases/${sha}-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
mkdir -m 700 -p "$release_dir"
export GATEWAY_IMAGE="ghcr.io/sageseekersociety/cheese/gateway:${sha:0:7}"
export GATEWAY_CONFIG="$release_dir/config.yaml"
export GATEWAY_PROXY_DIR="$gateway_home/proxy"
project=cheese-gateway
compose=(docker compose --env-file "$env_file" -f "$here/compose/docker-compose.gateway.yml" -p "$project")
# How long the color traffic left may keep serving the streams it already has.
# The proxy's own inactivity timeouts are the same length
# (deploy/compose/gateway-proxy.cfg); a stream still running at the cap is cut.
drain_seconds="${GATEWAY_DRAIN_SECONDS:-1800}"
poll_seconds="${GATEWAY_POLL_SECONDS:-5}"
# The gateway as a single container, before this script ran two colors.
legacy="$project-litellm-1"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { log "$*" >&2; exit 1; }

docker pull "$GATEWAY_IMAGE"
revision="$(docker image inspect "$GATEWAY_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
# Promoted images retain their original revision; the workflow verifies the
# requested commit's build, and its immutable tag selects the resulting image.
[[ -n "$revision" ]] || fail 'Gateway image has no revision label.'
candidate="$(docker create "$GATEWAY_IMAGE")"
trap 'docker rm "$candidate" >/dev/null' EXIT
docker cp "$candidate:/opt/cheese-gateway/config.yaml" "$GATEWAY_CONFIG"
docker rm "$candidate" >/dev/null
trap - EXIT
docker run --rm --network none --entrypoint /app/.venv/bin/python \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True "$GATEWAY_IMAGE" /opt/cheese-gateway/test_deepseek_images.py

# Shared with the metering proxy, whose release may not have run yet; created
# here if missing, and a concurrent creation by that release is not an error.
docker network inspect cheese-meter-gateway >/dev/null 2>&1 \
  || docker network create --internal cheese-meter-gateway >/dev/null \
  || docker network inspect cheese-meter-gateway >/dev/null

container_of() { "${compose[@]}" ps -q "$1"; }

# The proxy's runtime API, inside its container (deploy/compose/gateway-proxy.cfg).
runtime() { docker exec -i "$proxy" socat stdio tcp4:127.0.0.1:9999 <<<"$1"; }
# `show servers state` columns: 4 name, 6 operational state (2 = up),
# 7 administrative flags (0 = ready; 0x08 = drain set through the API).
server_field() { runtime 'show servers state litellm' | awk -v s="$1" -v f="$2" '$4 == s { print $f }'; }
is_up() { [[ "$(server_field "litellm-$1" 6)" = 2 ]]; }
is_ready() { [[ "$(server_field "litellm-$1" 7)" = 0 ]]; }
is_draining() { local flags; flags="$(server_field "litellm-$1" 7)"; [[ -n "$flags" ]] && (( (flags & 8) != 0 )); }
# Requests the proxy has open on a color right now (`show stat` field 5, scur).
sessions() { runtime 'show stat' | awk -F, -v s="litellm-$1" '$1 == "litellm" && $2 == s { print $5 }'; }
other() { [[ "$1" = blue ]] && echo green || echo blue; }
# The proxy's master and one worker, and nothing else. A configuration reload
# leaves the previous worker finishing the connections it had, and the runtime
# API answers only for the new one, so until that worker exits its streams are
# on no count above.
proxy_settled() { [[ "$(docker exec "$proxy" pgrep haproxy | grep -c .)" -le 2 ]]; }

# Waits until the proxy has no request open on color $1, which it sends nothing
# new, then stops it; at the cap it stops it anyway and says how many it cut.
# Someone switching traffic back to it in the meantime ends the wait and leaves
# it running.
drain_and_stop() {
  local color="$1" waited=0 open
  log "litellm-$color takes no new requests; waiting for the ones it has (at most ${drain_seconds}s)"
  while :; do
    is_draining "$color" || { log "litellm-$color serves again; not stopping it"; return 0; }
    open="$(sessions "$color")"
    [[ "$open" = 0 ]] && proxy_settled && break
    if (( waited >= drain_seconds )); then
      log "litellm-$color still has $open open requests after ${drain_seconds}s; stopping it cuts them"
      break
    fi
    sleep "$poll_seconds"
    waited=$((waited + poll_seconds))
  done
  "${compose[@]}" stop "litellm-$color"
  log "litellm-$color stopped after ${waited}s of drain"
}

# Ready = answers /health/readiness, which checks the gateway database.
ready() {
  docker exec "$1" /app/.venv/bin/python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/readiness', timeout=10).read()" \
    >/dev/null 2>&1
}

# Starts color $1 on this release's image, takes no traffic until it is ready.
start_color() {
  local color="$1" id
  # The budget brake's spend counters; created if missing, never recreated here.
  "${compose[@]}" up -d --no-deps --wait --wait-timeout 60 litellm-redis
  if ! "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 150 "litellm-$color"; then
    "${compose[@]}" rm -f -s "litellm-$color" >/dev/null 2>&1 || true
    fail "litellm-$color failed its health check; traffic stays where it was."
  fi
  id="$(container_of "litellm-$color")"
  ready "$id" || {
    "${compose[@]}" rm -f -s "litellm-$color" >/dev/null 2>&1 || true
    fail "litellm-$color is not ready (/health/readiness); traffic stays where it was."
  }
  log "litellm-$color is ready on $GATEWAY_IMAGE"
}

# Readable by the proxy's own user (haproxy, uid 99); it holds no secret.
install_proxy_config() {
  mkdir -m 755 -p "$GATEWAY_PROXY_DIR"
  cp "$here/compose/gateway-proxy.cfg" "$GATEWAY_PROXY_DIR/haproxy.cfg.next"
  chmod 644 "$GATEWAY_PROXY_DIR/haproxy.cfg.next"
  if cmp -s "$GATEWAY_PROXY_DIR/haproxy.cfg.next" "$GATEWAY_PROXY_DIR/haproxy.cfg"; then
    rm -f "$GATEWAY_PROXY_DIR/haproxy.cfg.next"
    return 1
  fi
  mv -f "$GATEWAY_PROXY_DIR/haproxy.cfg.next" "$GATEWAY_PROXY_DIR/haproxy.cfg"
}

proxy="$(container_of gateway-proxy)"

if [[ -z "$proxy" ]] && docker inspect "$legacy" >/dev/null 2>&1; then
  # ---- Once: from the single container to the proxy and two colors ----
  # The single container holds 172.17.0.1:4000 and the alias `litellm`, and
  # Docker moves neither while it runs, so the proxy can take them only after
  # it stops: the one release that interrupts the streams it carries.
  [[ "${GATEWAY_ALLOW_INTERRUPT:-}" = 1 ]] || fail \
    "This release moves the gateway from $legacy to gateway-proxy and two colors, which interrupts the streams $legacy carries. Run it at a quiet moment and acknowledge the interruption."
  install_proxy_config || true
  start_color blue
  docker pull -q "$("${compose[@]}" config --images gateway-proxy)" >/dev/null
  "${compose[@]}" up --no-start --no-deps gateway-proxy
  log "stopping $legacy (in-flight requests get ${GATEWAY_MIGRATION_GRACE:-30}s); requests are refused until gateway-proxy listens"
  docker stop -t "${GATEWAY_MIGRATION_GRACE:-30}" "$legacy" >/dev/null
  log "$legacy stopped"
  if ! "${compose[@]}" up -d --no-deps --wait --wait-timeout 60 gateway-proxy; then
    "${compose[@]}" rm -f -s gateway-proxy >/dev/null 2>&1 || true
    docker start "$legacy" >/dev/null
    "${compose[@]}" rm -f -s litellm-blue >/dev/null 2>&1 || true
    fail "gateway-proxy did not become healthy; $legacy serves again."
  fi
  log "gateway-proxy serves 172.17.0.1:4000 through litellm-blue"
  docker rm "$legacy" >/dev/null
else
  [[ -n "$proxy" ]] || fail 'gateway-proxy is not running; start it before releasing.'
  # The proxy carries every open stream, so it is never recreated in passing.
  wanted="$("${compose[@]}" config --hash gateway-proxy | awk '{ print $2 }')"
  running="$(docker inspect "$proxy" --format '{{index .Config.Labels "com.docker.compose.config-hash"}}')"
  if [[ "$wanted" != "$running" ]]; then
    [[ "${GATEWAY_ALLOW_INTERRUPT:-}" = 1 ]] || fail \
      'gateway-proxy itself changed (image or service definition); recreating it interrupts every open stream. Acknowledge the interruption to release it.'
    install_proxy_config || true
    "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 60 gateway-proxy
    proxy="$(container_of gateway-proxy)"
  elif install_proxy_config; then
    # A reload hands the listener to a new process; the old one keeps every
    # connection it has until it is done with it (idle-close-on-response), and
    # drain_and_stop waits for it. Its server states carry over in the state
    # file. Checked first inside the running proxy: it has the addresses the
    # file reads.
    docker exec "$proxy" haproxy -c -q -f /usr/local/etc/haproxy/haproxy.cfg \
      || fail 'The new gateway-proxy configuration is invalid; the running proxy keeps the old one.'
    runtime 'show servers state' | docker exec -i "$proxy" sh -c 'cat > /tmp/gateway-proxy-servers.state'
    docker kill --signal USR2 "$proxy" >/dev/null
    log 'gateway-proxy reloaded its configuration'
    sleep 2
  fi

  # The color serving now. Both serve only when the box restarted during a
  # drain (both containers come back, and a fresh proxy readies both); the
  # newer one is the last release's, and the other is drained below.
  from="" from_created=""
  for color in blue green; do
    id="$(container_of "litellm-$color")"
    [[ -n "$id" ]] && is_up "$color" && is_ready "$color" || continue
    created="$(docker inspect "$id" --format '{{.Created}}')"
    [[ -z "$from" || "$created" > "$from_created" ]] || continue
    from="$color" from_created="$created"
  done
  to="$(other "${from:-green}")"
  [[ -n "$from" ]] || log 'No color serves now; starting litellm-blue.'
  # An interrupted release can leave the color this one replaces draining.
  if [[ -n "$(container_of "litellm-$to")" ]]; then
    runtime "set server litellm/litellm-$to state drain" >/dev/null
    drain_and_stop "$to"
  fi
  # Takes nothing until the switch below, however soon the proxy sees it up.
  runtime "set server litellm/litellm-$to state drain" >/dev/null
  [[ -z "$from" ]] || docker inspect "$(container_of "litellm-$from")" --format '{{.Image}}' > "$release_dir/previous-image"
  start_color "$to"
  waited=0
  until is_up "$to"; do
    if (( waited >= 60 )); then
      "${compose[@]}" rm -f -s "litellm-$to" >/dev/null 2>&1 || true
      fail "gateway-proxy does not see litellm-$to up; traffic stays where it was."
    fi
    sleep 1
    waited=$((waited + 1))
  done
  runtime "set server litellm/litellm-$to state ready${from:+; set server litellm/litellm-$from state drain}" >/dev/null
  log "new requests go to litellm-$to ($GATEWAY_IMAGE, revision $revision)"
  [[ -z "$from" ]] || drain_and_stop "$from"
fi
echo "Gateway released: $GATEWAY_IMAGE"

# LiteLLM's own spend-log pages (/spend/logs/v2 and its admin UI) read one key's
# days by (api_key, startTime). Its schema declares that index but leaves building it to
# operators, because a plain CREATE INDEX blocks spend-log inserts for the whole
# build; without it every page full-scans the spend table. CONCURRENTLY does not
# block inserts. A CONCURRENTLY build that failed leaves an invalid index, which
# IF NOT EXISTS would then skip forever, so that one is dropped first.
"${compose[@]}" exec -T litellm-db psql -U litellm -d litellm -v ON_ERROR_STOP=1 -q <<'SQL'
SELECT 'DROP INDEX CONCURRENTLY "LiteLLM_SpendLogs_api_key_startTime_idx"'
FROM pg_index JOIN pg_class ON pg_class.oid = pg_index.indexrelid
WHERE pg_class.relname = 'LiteLLM_SpendLogs_api_key_startTime_idx' AND NOT pg_index.indisvalid
\gexec
CREATE INDEX CONCURRENTLY IF NOT EXISTS "LiteLLM_SpendLogs_api_key_startTime_idx"
ON "LiteLLM_SpendLogs" ("api_key", "startTime");
SQL
echo "Spend-log index in place."
