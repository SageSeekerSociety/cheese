#!/usr/bin/env bash
# Called only by the independent gateway release workflow, after its build gate.
#
# Callers reach 172.17.0.1:4000 and http://litellm:4000, an address
# gateway-netns owns. In its network namespace a proxy (HAProxy, gateway-proxy-a
# or -b) sends each request to one of two LiteLLM instances, litellm-blue or
# litellm-green. A release starts the idle instance on the new image, waits
# until it is ready, serves the models the running one serves and the proxy
# sees it up, moves traffic to it through the proxy's runtime API (no proxy
# restart: every caller connection stays open, and its next request goes to
# the new color), then waits until the old color has no request left and stops
# it. A stream that started before the switch finishes on the old color. A new
# color that fails a check never receives traffic; rolling back is releasing
# the previous SHA, which takes the same path. A change to the proxy itself
# starts the idle proxy beside the running one and soft-stops the old.
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
export GATEWAY_PROXY_STATE_DIR="$gateway_home/proxy-state"
project=cheese-gateway
compose=(docker compose --env-file "$env_file" -f "$here/compose/docker-compose.gateway.yml" -p "$project")
# How long the color traffic left may keep serving the streams it already has.
# The proxy's own inactivity timeouts are the same length, and a soft-stopped
# proxy lives at most 35 minutes (hard-stop-after in
# deploy/compose/gateway-proxy.cfg); a stream still running at the cap is cut.
drain_seconds="${GATEWAY_DRAIN_SECONDS:-1800}"
poll_seconds="${GATEWAY_POLL_SECONDS:-5}"
# The gateway as a single container, before this script ran two colors.
legacy="$project-litellm-1"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { log "$*" >&2; exit 1; }

# A registry or containerd hiccup (a dropped connection mid-manifest, `EOF`, a
# failed layer commit) fails one pull and nothing else, so a pull is tried
# again before the release gives up, as release-metering-proxy.sh does. $@ is
# the pull command.
pull() {
  local delay
  for delay in ${GATEWAY_PULL_RETRY_DELAYS:-10 30 60} ""; do
    "$@" && return 0
    [[ -n "$delay" ]] || return 1
    log "Pulling failed; trying again in ${delay}s."
    sleep "$delay"
  done
}
pull docker pull "$GATEWAY_IMAGE" || fail "Pulling $GATEWAY_IMAGE failed on every attempt."
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
running() { [[ "$(docker inspect "$1" --format '{{.State.Running}}' 2>/dev/null)" = true ]]; }
other() { [[ "$1" = blue ]] && echo green || echo blue; }

# The runtime API of the proxy in $proxy (deploy/compose/gateway-proxy.cfg).
runtime() { docker exec -i "$proxy" socat stdio unix-connect:/tmp/gateway-proxy.sock <<<"$1"; }
# `show servers state` columns: 4 name, 6 operational state (2 = up),
# 7 administrative flags (0 = ready; 0x08 = drain set through the API).
server_field() { runtime 'show servers state litellm' | awk -v s="$1" -v f="$2" '$4 == s { print $f }'; }
is_up() { [[ "$(server_field "litellm-$1" 6)" = 2 ]]; }
is_ready() { [[ "$(server_field "litellm-$1" 7)" = 0 ]]; }
is_draining() { local flags; flags="$(server_field "litellm-$1" 7)"; [[ -n "$flags" ]] && (( (flags & 8) != 0 )); }
# Requests this proxy has open on a color right now (`show stat` field 5, scur).
sessions() { runtime 'show stat' | awk -F, -v s="litellm-$1" '$1 == "litellm" && $2 == s { print $5 }'; }

# A proxy being soft-stopped (a proxy change, below). It goes on carrying the
# streams it had, which no count of the running proxy includes, until they end
# or hard-stop-after ends it.
old_proxy=""
settled() { [[ -z "$old_proxy" ]] || ! running "$old_proxy"; }

# Waits until no proxy has a request open on color $1, which the proxy sends
# nothing new, then stops it. At the cap it stops it anyway, once a
# soft-stopped proxy (bounded by hard-stop-after) has exited, and says how many
# requests that cut. Someone switching traffic back to it in the meantime ends
# the wait and leaves it running.
drain_and_stop() {
  local color="$1" waited=0 open
  log "litellm-$color takes no new requests; waiting for the ones it has (at most ${drain_seconds}s)"
  while :; do
    is_draining "$color" || { log "litellm-$color serves again; not stopping it"; return 0; }
    open="$(sessions "$color")"
    [[ "$open" = 0 ]] && settled && break
    if (( waited >= drain_seconds )) && settled; then
      echo "::warning::litellm-$color still had $open open requests after the ${drain_seconds}s drain cap; stopping it cuts them."
      log "DRAIN CAP: stopping litellm-$color cuts $open open requests"
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

# The models container $1 serves, through its /v1/models with the master key
# it holds: a listing, which calls no provider (unlike /health).
model_ids() {
  docker exec "$1" /app/.venv/bin/python -c '
import json, os, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:4000/v1/models",
    headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"]},
)
for model in json.load(urllib.request.urlopen(request, timeout=20))["data"]:
    print(model["id"])
' | sort -u
}
config_of() { docker inspect "$1" --format '{{range .Mounts}}{{if eq .Destination "/app/config.yaml"}}{{.Source}}{{end}}{{end}}'; }
declared() { sed -nE "s/^[[:space:]-]*model_name:[[:space:]]*[\"']?([^\"'[:space:]]+)[\"']?[[:space:]]*$/\1/p" "$1" 2>/dev/null; }

# Container $1 (new) serves every model container $2 (serving) serves, except
# one the serving container's configuration declares and this release's no
# longer does. Any other missing one was added on the admin page, lives in the
# database, and has not loaded: the new color would refuse it.
serves_the_same_models() {
  local live new old_declared new_declared missing="" id
  live="$(model_ids "$2")" || { log "the serving gateway does not list its models"; return 1; }
  new="$(model_ids "$1")" || return 1
  old_declared="$(declared "$(config_of "$2")")"
  new_declared="$(declared "$GATEWAY_CONFIG")"
  while read -r id; do
    [[ -n "$id" ]] || continue
    grep -Fxq -- "$id" <<<"$new" && continue
    grep -Fxq -- "$id" <<<"$old_declared" && ! grep -Fxq -- "$id" <<<"$new_declared" && continue
    missing+=" $id"
  done <<<"$live"
  [[ -z "$missing" ]] || { log "not served yet:$missing"; return 1; }
}

# Starts color $1 on this release's image; it takes no traffic. $2 is the
# container serving now, if any, whose models it must serve too.
start_color() {
  local color="$1" serving="${2:-}" id deadline
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
  # Models added on the admin page load from the database after startup.
  if [[ -n "$serving" ]]; then
    deadline=$((SECONDS + ${GATEWAY_MODELS_WAIT_SECONDS:-60}))
    until serves_the_same_models "$id" "$serving"; do
      if (( SECONDS >= deadline )); then
        "${compose[@]}" rm -f -s "litellm-$color" >/dev/null 2>&1 || true
        fail "litellm-$color does not serve every model the running gateway serves; traffic stays where it was."
      fi
      sleep "$poll_seconds"
    done
  fi
  log "litellm-$color is ready on $GATEWAY_IMAGE"
}

# The proxy's service definition (image, environment, volumes, healthcheck) as
# this checkout renders it, recorded whenever a release starts a proxy. Not
# compose's own config hash: for a service in another container's network
# namespace compose hashes the resolved container id, which never matches what
# `config --hash` prints, so every release would replace the proxy.
proxy_definition() {
  "${compose[@]}" config --format json | python3 -c '
import hashlib, json, sys
service = json.load(sys.stdin)["services"]["gateway-proxy-a"]
print(hashlib.sha256(json.dumps(service, sort_keys=True).encode()).hexdigest())'
}
record_proxy_definition() { proxy_definition > "$GATEWAY_PROXY_STATE_DIR/proxy-definition"; }

# Readable by the proxy's own user (haproxy, uid 99); it holds no secret.
# Returns 1 when the installed configuration is already this one.
install_proxy_config() {
  mkdir -m 755 -p "$GATEWAY_PROXY_DIR" "$GATEWAY_PROXY_STATE_DIR"
  cp "$here/compose/gateway-proxy.cfg" "$GATEWAY_PROXY_DIR/haproxy.cfg.next"
  chmod 644 "$GATEWAY_PROXY_DIR/haproxy.cfg.next"
  if cmp -s "$GATEWAY_PROXY_DIR/haproxy.cfg.next" "$GATEWAY_PROXY_DIR/haproxy.cfg"; then
    rm -f "$GATEWAY_PROXY_DIR/haproxy.cfg.next"
    return 1
  fi
  [[ ! -f "$GATEWAY_PROXY_DIR/haproxy.cfg" ]] \
    || cp -p "$GATEWAY_PROXY_DIR/haproxy.cfg" "$GATEWAY_PROXY_DIR/haproxy.cfg.previous"
  mv -f "$GATEWAY_PROXY_DIR/haproxy.cfg.next" "$GATEWAY_PROXY_DIR/haproxy.cfg"
}

# The proxy in $proxy_service changed (configuration or definition): start the
# other one in the same network namespace with the colors' states as they are,
# check it sees the serving color up, then soft-stop the running one. Both
# listen on :4000 meanwhile; the old one stops accepting at once, keeps every
# connection it has (idle ones until their next request, answered with
# Connection: close) and exits when they end or hard-stop-after ends it.
replace_proxy() {
  local next id
  next="gateway-proxy-$([[ "$proxy_service" = gateway-proxy-a ]] && echo b || echo a)"
  pull "${compose[@]}" pull -q "$next" || fail "Pulling $next's image failed on every attempt; $proxy_service keeps serving."
  mkdir -m 755 -p "$GATEWAY_PROXY_STATE_DIR"
  runtime 'show servers state' > "$GATEWAY_PROXY_STATE_DIR/servers.state.next"
  chmod 644 "$GATEWAY_PROXY_STATE_DIR/servers.state.next"
  mv -f "$GATEWAY_PROXY_STATE_DIR/servers.state.next" "$GATEWAY_PROXY_STATE_DIR/servers.state"
  if ! "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 60 "$next" \
    || ! id="$(container_of "$next")" || [[ -z "$id" ]] \
    || { [[ -n "$from" ]] && ! proxy="$id" is_up "$from"; }; then
    "${compose[@]}" rm -f -s "$next" >/dev/null 2>&1 || true
    [[ ! -f "$GATEWAY_PROXY_DIR/haproxy.cfg.previous" ]] \
      || mv -f "$GATEWAY_PROXY_DIR/haproxy.cfg.previous" "$GATEWAY_PROXY_DIR/haproxy.cfg"
    fail "$next did not come up serving what $proxy_service serves; $proxy_service keeps serving."
  fi
  # Its exit must stay an exit: a restart would bind :4000 again.
  docker update --restart=no "$proxy" >/dev/null
  docker kill --signal USR1 "$proxy" >/dev/null
  record_proxy_definition
  log "$next serves; $proxy_service finishes what it carries and exits"
  old_proxy="$proxy" proxy="$id" proxy_service="$next"
}

# Connections the single container has sent or received on within 30 s:
# requests in flight, as far as its sockets show. Read from its network
# namespace, which needs root.
busy_connections() {
  local pid
  pid="$(docker inspect "$legacy" --format '{{.State.Pid}}')"
  sudo -n nsenter -t "$pid" -n ss -Htin state established '( sport = :4000 )' \
    | awk '/lastsnd/ {
        snd = rcv = 1e12
        if (match($0, /lastsnd:[0-9]+/)) snd = substr($0, RSTART + 8, RLENGTH - 8) + 0
        if (match($0, /lastrcv:[0-9]+/)) rcv = substr($0, RSTART + 8, RLENGTH - 8) + 0
        if (snd < 30000 || rcv < 30000) n++
      } END { print n + 0 }'
}

holder="$(container_of gateway-netns)"

if [[ -z "$holder" ]] && docker inspect "$legacy" >/dev/null 2>&1; then
  # ---- Once: from the single container to the proxy and two colors ----
  # The single container holds 172.17.0.1:4000 and the alias `litellm`, and
  # Docker moves neither while it runs, so gateway-netns can take them only
  # after it stops: the one release that interrupts the requests it carries.
  [[ "${GATEWAY_ALLOW_INTERRUPT:-}" = 1 ]] || fail \
    "This release moves the gateway from $legacy to gateway-netns, a proxy and two colors, which interrupts the requests $legacy carries. Run it at a quiet moment and acknowledge the interruption."
  install_proxy_config || true
  start_color blue "$legacy"
  pull "${compose[@]}" pull -q gateway-netns gateway-proxy-a || fail "Pulling the proxy's images failed on every attempt; $legacy keeps serving."
  "${compose[@]}" up --no-start --no-deps gateway-netns gateway-proxy-a
  # Cut when nothing is in flight, if such a moment comes.
  quiet_wait="${GATEWAY_MIGRATION_QUIET_SECONDS:-600}" waited=0
  while :; do
    busy="$(busy_connections)" || { log "cannot read $legacy's connections; stopping it now"; break; }
    [[ "$busy" = 0 ]] && { log "$legacy has no request in flight"; break; }
    if (( waited >= quiet_wait )); then
      log "$legacy still had $busy connections in use after ${quiet_wait}s; stopping it anyway"
      break
    fi
    sleep "$poll_seconds"
    waited=$((waited + poll_seconds))
  done
  grace="${GATEWAY_MIGRATION_GRACE:-30}"
  log "stopping $legacy (in-flight requests get ${grace}s); requests are refused until the proxy listens"
  docker stop -t "$grace" "$legacy" >/dev/null
  log "$legacy stopped"
  if ! "${compose[@]}" up -d --no-deps --wait --wait-timeout 60 gateway-netns gateway-proxy-a; then
    "${compose[@]}" rm -f -s gateway-proxy-a gateway-netns >/dev/null 2>&1 || true
    docker start "$legacy" >/dev/null
    "${compose[@]}" rm -f -s litellm-blue >/dev/null 2>&1 || true
    fail "The proxy did not become healthy; $legacy serves again."
  fi
  record_proxy_definition
  log "gateway-proxy-a serves 172.17.0.1:4000 through litellm-blue"
  docker rm "$legacy" >/dev/null
else
  [[ -n "$holder" ]] || fail 'gateway-netns is not running; start it before releasing.'
  # The running proxy; the newer one if a proxy change was interrupted, the
  # other then being soft-stopped like any old proxy.
  proxy="" proxy_service="" newest=""
  for service in gateway-proxy-a gateway-proxy-b; do
    id="$(container_of "$service")"
    [[ -n "$id" ]] || continue
    created="$(docker inspect "$id" --format '{{.Created}}')"
    if [[ -z "$proxy" || "$created" > "$newest" ]]; then
      [[ -z "$proxy" ]] || old_proxy="$proxy"
      proxy="$id" proxy_service="$service" newest="$created"
    else
      old_proxy="$id"
    fi
  done
  [[ -n "$proxy" ]] || fail 'No gateway proxy is running; start one before releasing.'
  if [[ -n "$old_proxy" ]]; then
    docker update --restart=no "$old_proxy" >/dev/null
    docker kill --signal USR1 "$old_proxy" >/dev/null || true
  fi
  # The address itself: replacing gateway-netns cuts every open connection.
  wanted="$("${compose[@]}" config --hash gateway-netns | awk '{ print $2 }')"
  running_hash="$(docker inspect "$holder" --format '{{index .Config.Labels "com.docker.compose.config-hash"}}')"
  if [[ "$wanted" != "$running_hash" ]]; then
    [[ "${GATEWAY_ALLOW_INTERRUPT:-}" = 1 ]] || fail \
      'gateway-netns changed (published port, networks or image); recreating it interrupts every open stream. Acknowledge the interruption to release it.'
    install_proxy_config || true
    "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 60 gateway-netns "$proxy_service"
    proxy="$(container_of "$proxy_service")" old_proxy=""
    record_proxy_definition
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

  if install_proxy_config \
    || [[ "$(proxy_definition)" != "$(cat "$GATEWAY_PROXY_STATE_DIR/proxy-definition" 2>/dev/null)" ]]; then
    [[ -z "$old_proxy" ]] || ! running "$old_proxy" || fail \
      'The proxy changed, and the one a previous release replaced still carries streams; release again once it has exited (at most 35 minutes).'
    replace_proxy
  fi

  to="$(other "${from:-green}")"
  [[ -n "$from" ]] || log 'No color serves now; starting litellm-blue.'
  # An interrupted release can leave the color this one replaces draining.
  if [[ -n "$(container_of "litellm-$to")" ]]; then
    runtime "set server litellm/litellm-$to state drain" >/dev/null
    drain_and_stop "$to"
  fi
  # Takes nothing until the switch below, however soon the proxy sees it up.
  runtime "set server litellm/litellm-$to state drain" >/dev/null
  serving=""
  if [[ -n "$from" ]]; then
    serving="$(container_of "litellm-$from")"
    docker inspect "$serving" --format '{{.Image}}' > "$release_dir/previous-image"
  fi
  start_color "$to" "$serving"
  waited=0
  until is_up "$to"; do
    if (( waited >= 60 )); then
      "${compose[@]}" rm -f -s "litellm-$to" >/dev/null 2>&1 || true
      fail "The proxy does not see litellm-$to up; traffic stays where it was."
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
