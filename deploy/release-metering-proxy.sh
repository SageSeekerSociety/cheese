#!/usr/bin/env bash
# Both deployment entry points check the exact main SHA's build and Required CI first.
#
# Callers reach `front` (HAProxy, deploy/metering-proxy/front.cfg): sessions at
# 172.17.0.1:8444, the gateway at http://metering-proxy:8445. Behind it one of
# two proxies serves, slot-blue or slot-green. A release starts the other one on
# the new image, waits until it is healthy and the front sees it up, and moves
# new connections to it through the front's runtime API (no front reload: every
# open connection stays where it is). Then it marks the old slot draining, so
# the old slot closes each HTTP/1 client connection after the response it
# carries and the client's next request reconnects to the new slot; once the
# old slot has no connection open, the release stops it. A stream that started
# before the switch finishes on the old slot if it ends within the drain limit
# (METERING_DRAIN_SECONDS); whatever is still open at the limit is cut. An
# HTTP/2 client connection or a raw tunnel to another host leaves the old slot
# only when its client closes it. A new slot that is not healthy
# never receives traffic; rolling back is releasing the previous SHA, which
# takes the same path.
#
# The two slots overlap for the length of the drain. What they share is safe to
# share (deploy/metering-proxy/README.md, "Release on the dev box"): the ledger, the
# credentials and the account cooldowns are written under file locks.
set -euo pipefail
umask 077

[[ "${GITHUB_ACTIONS:-}" = true ]] || {
  echo 'Use the Release metering proxy workflow.' >&2
  exit 1
}
sha="${1:?usage: release-metering-proxy.sh <full-main-sha>}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 1
here="$(cd "$(dirname "$0")" && pwd)"
proxy_home="${METERING_PROXY_HOME:-$HOME/cheese-proxy-new/deploy/metering-proxy}"
env_file="$proxy_home/.env"
[[ -r "$env_file" ]] || { echo 'Metering proxy environment file is missing.' >&2; exit 1; }

# This box's names for the stack, and where sessions reach it.
project=metering-proxy
# The single container the proxy ran as before this stack had a front.
legacy=cheese-metering-proxy
connect_host=172.17.0.1
connect_port=8444
# How long a slot that no longer takes connections may keep serving the ones it
# has. The front's idle timeout is longer (front.cfg); a connection still open
# at the cap is cut.
drain_seconds="${METERING_DRAIN_SECONDS:-1800}"
poll_seconds="${METERING_POLL_SECONDS:-5}"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { log "$*" >&2; exit 1; }

image="ghcr.io/sageseekersociety/cheese/metering-proxy:$sha"
# A registry hiccup (a dropped connection mid-manifest, `EOF`) fails one pull
# and nothing else, so the pull is tried again before the release gives up;
# deploy-docker.sh retries the app images' pulls the same way.
pulled=false
for delay in ${METERING_PULL_RETRY_DELAYS:-10 30 60} ""; do
  if docker pull "$image"; then pulled=true; break; fi
  [[ -n "$delay" ]] || break
  echo "Pulling $image failed; trying again in ${delay}s." >&2
  sleep "$delay"
done
$pulled || fail "Pulling $image failed on every attempt."
export METERING_PROXY_IMAGE
METERING_PROXY_IMAGE="$(docker image inspect "$image" --format '{{index .RepoDigests 0}}')"
[[ "$METERING_PROXY_IMAGE" =~ ^ghcr.io/sageseekersociety/cheese/metering-proxy@sha256:[0-9a-f]{64}$ ]] \
  || fail 'Pulled image has no expected immutable registry digest.'
# The operator's login tool runs on this box, next to the credential it
# writes. Installed before the unchanged-image exit below, so a release that
# changes only the tool still delivers it.
install -m 0755 "$here/metering-proxy/claude-login.sh" "$proxy_home/claude-login.sh"
install -m 0755 "$here/metering-proxy/chatgpt-login.sh" "$proxy_home/chatgpt-login.sh"
# The configuration a slot runs is the compose file and the box's .env it was
# created from, and a change to either alone (a new env value, a new mount)
# ships no new image. So the release labels the slot with a hash of the two,
# and the unchanged-image exit below also needs the hash unchanged. Only the
# hash is kept anywhere: the .env holds secrets.
export METERING_PROXY_CONFIG_SHA256
METERING_PROXY_CONFIG_SHA256="$(
  { sha256sum < "$here/metering-proxy/compose.yml"; sha256sum < "$env_file"; } \
    | sha256sum | cut -d' ' -f1
)"
release_dir="$proxy_home/releases/${sha}-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
mkdir -p "$release_dir"
printf '%s\n' "$METERING_PROXY_IMAGE" > "$release_dir/image"
cp "$here/metering-proxy/compose.yml" "$release_dir/compose.yml"
export METERING_FRONT_DIR="$proxy_home/front"
# Created here, as the operator, so the login script can write the credential
# into it; left to docker, the bind source would be created owned by root.
mkdir -p "$proxy_home/claude-credential" "$proxy_home/chatgpt-credential"
# The private network the gateway reaches the ChatGPT listener on. Shared with
# the gateway stack, whose release may not have run yet; created here if
# missing, and a concurrent creation by that release is not an error.
docker network inspect cheese-meter-gateway >/dev/null 2>&1 \
  || docker network create --internal cheese-meter-gateway >/dev/null \
  || docker network inspect cheese-meter-gateway >/dev/null
# Keep raw configuration, never docker compose config: its output contains secrets.
compose=(docker compose --project-directory "$proxy_home" --env-file "$env_file" -p "$project" -f "$release_dir/compose.yml")

container_of() { "${compose[@]}" ps -q "$1"; }
other() { [[ "$1" = blue ]] && echo green || echo blue; }

# The front's runtime API, inside its container (front.cfg).
runtime() { docker exec -i "$1" socat stdio tcp4:127.0.0.1:9999 <<<"$2"; }
# `show servers state` columns: 4 name, 6 operational state (2 = up),
# 7 administrative flags (0 = ready; 0x08 = drain set through the API).
# $4 is the backend, connect_slots unless named.
server_field() {
  runtime "$1" "show servers state ${4:-connect_slots}" | awk -v s="slot-$2" -v f="$3" '$4 == s { print $f }'
}
is_up() { [[ "$(server_field "$1" "$2" 6 "${3:-connect_slots}")" = 2 ]]; }
is_ready() { [[ "$(server_field "$1" "$2" 7)" = 0 ]]; }
is_draining() { local flags; flags="$(server_field "$1" "$2" 7)"; [[ -n "$flags" ]] && (( (flags & 8) != 0 )); }
# Connections front $1 has open to slot $2 now (`show stat` field 5, scur) on
# the backends matching $3: tunnels on the CONNECT listener, requests on the
# ChatGPT one.
open_on() {
  runtime "$1" 'show stat' \
    | awk -F, -v s="slot-$2" -v b="${3:-^(connect|chatgpt)_slots$}" '$1 ~ b && $2 == s { n += $5 } END { print n + 0 }'
}
# Both backends, each with its own servers. The API answers a command it
# carried out with nothing.
set_state() {
  local answer
  answer="$(runtime "$1" "set server connect_slots/slot-$2 state $3; set server chatgpt_slots/slot-$2 state $3")"
  [[ -z "${answer//[[:space:]]/}" ]] || fail "The front refused to set slot-$2 $3: $answer"
}
# The pid of the front's current worker, which a reload replaces.
front_pid() { runtime "$front" 'show info' 2>/dev/null | awk -F': ' '$1 == "Pid" { print $2 }'; }
# Connections opened through front $1 since it started (its frontends' stot).
accepted_by() {
  runtime "$1" 'show stat' | awk -F, '($1 == "connect") && $2 == "FRONTEND" { print $8 }'
}

# Established connections to port $2 inside container $1, read from its own
# /proc (it has no ss): field 2 is local address:port in hex, field 4 the
# state, 01 = established. A container that is not running has none.
established_in() {
  { docker exec "$1" cat /proc/net/tcp /proc/net/tcp6 2>/dev/null || true; } \
    | awk -v p="$(printf ':%04X' "$2")" '$4 == "01" && substr($2, length($2) - 4) == p { n++ } END { print n + 0 }'
}
# Connections slot $1 has open on its two listeners, counted at the slot: a
# front reloaded since they opened carries them in its previous process,
# which the runtime API does not see. A health check in passing counts too,
# and only delays the stop by a poll.
open_at_slot() {
  local id
  id="$(container_of "slot-$1")"
  echo $(( $(established_in "$id" 8444) + $(established_in "$id" 8445) ))
}

# Waits until slot $2, which front $1 sends nothing new, has no connection
# open, then stops it; at the cap it stops it anyway and says how
# many it cut. Someone switching traffic back to the slot in the meantime ends
# the wait and leaves it running.
drain_and_stop() {
  local front="$1" color="$2" waited=0 open
  log "slot-$color takes no new connections; waiting for the ones it has (at most ${drain_seconds}s)"
  while :; do
    is_draining "$front" "$color" || { log "slot-$color serves again; not stopping it"; return 0; }
    open="$(open_at_slot "$color")"
    [[ "$open" = 0 ]] && break
    if (( waited >= drain_seconds )); then
      log "slot-$color still has $open open connections after ${drain_seconds}s; stopping it cuts them"
      break
    fi
    sleep "$poll_seconds"
    waited=$((waited + poll_seconds))
  done
  "${compose[@]}" stop "slot-$color"
  log "slot-$color stopped after ${waited}s of drain"
}

# Starts slot $1 on this release's image; it takes no traffic until switched to.
start_slot() {
  if ! "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 150 "slot-$1"; then
    "${compose[@]}" rm -f -s "slot-$1" >/dev/null 2>&1 || true
    fail "slot-$1 failed its health check; traffic stays where it was."
  fi
  log "slot-$1 is healthy on $METERING_PROXY_IMAGE"
}

# Whether service $1 runs and its health check passes.
healthy() {
  local id
  id="$(container_of "$1")"
  [[ -n "$id" && "$(docker inspect "$id" --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}')" = healthy ]]
}

# Waits until front $1 sees slot $2 up on both listeners: the ChatGPT one has
# its own check, and switching before it passes leaves that listener with no
# server for a few seconds.
await_up() {
  local waited=0
  until is_up "$1" "$2" && is_up "$1" "$2" chatgpt_slots; do
    (( waited < 60 )) || return 1
    sleep 1
    waited=$((waited + 1))
  done
}

# Readable by the front's own user (haproxy, uid 99); it holds no secret.
# Returns 1 when the installed configuration is already this one.
install_front_config() {
  mkdir -m 755 -p "$METERING_FRONT_DIR"
  cp "$here/metering-proxy/front.cfg" "$METERING_FRONT_DIR/haproxy.cfg.next"
  chmod 644 "$METERING_FRONT_DIR/haproxy.cfg.next"
  if cmp -s "$METERING_FRONT_DIR/haproxy.cfg.next" "$METERING_FRONT_DIR/haproxy.cfg"; then
    rm -f "$METERING_FRONT_DIR/haproxy.cfg.next"
    return 1
  fi
  mv -f "$METERING_FRONT_DIR/haproxy.cfg.next" "$METERING_FRONT_DIR/haproxy.cfg"
}

# ---- Once: from the single container to the front and two slots ----
#
# The single container holds 172.17.0.1:8444 and the alias metering-proxy, and
# Docker moves neither while it runs. So a second front, front-handover, takes
# the new connections first: a NAT rule ahead of Docker's own sends new
# connections for 172.17.0.1:8444 to it, while the connections the old
# container already has keep their translation and finish there. Once the old
# container has none left it is stopped, `front` takes the address and the
# alias, the rule goes, and front-handover is stopped once the connections it
# carries have ended. The old container runs code from before the drain flag,
# so it cannot close its keep-alive connections; they leave when their clients
# close them, and whatever is still open at the drain limit is cut, as is
# anything front-handover still carries at its own limit. On the ChatGPT
# listener the gateway's requests still in flight on the old container when it
# stops are cut; the release stops it at a moment it has none when one comes
# within a minute.
#
# Every step can be run again: a release that died part-way (a cancelled job, a
# lost runner) leaves containers and possibly the rule behind, and the next
# release picks up from what it finds rather than recreating what carries
# traffic.
steer_comment=cheese-metering-handover
steer_to() {
  local chain rule=(-d "$connect_host/32" -p tcp --dport "$connect_port" -m comment --comment "$steer_comment" -j DNAT --to-destination "$1:8444")
  for chain in PREROUTING OUTPUT; do
    sudo -n iptables -t nat -C "$chain" "${rule[@]}" 2>/dev/null \
      || sudo -n iptables -t nat -I "$chain" 1 "${rule[@]}"
  done
}
# Every rule carrying the comment, whichever address it steers to: one a killed
# release left behind may point at a front-handover since recreated.
unsteer() {
  local chain spec
  for chain in PREROUTING OUTPUT; do
    while read -r -a spec; do
      [[ ${#spec[@]} -gt 0 ]] && sudo -n iptables -t nat "${spec[@]}"
    done < <(sudo -n iptables -t nat -S "$chain" | awk -v c="$steer_comment" 'index($0, "--comment " c) { sub(/^-A /, "-D "); print }')
  done
}

# The single container's credential refreshes take no lock (the lock is new),
# so while it overlaps a slot one rotating pair could be refreshed by both and
# be left spent. The move only starts while no pair refreshes within the
# longest it can take. A pair already past its expiry is left out: it is either
# dead or renewed by its next request whatever this release does.
refresh_due_during_move() {
  python3 - "$proxy_home" "$1" <<'PY'
import json, pathlib, sys, time

home, window, now = pathlib.Path(sys.argv[1]), float(sys.argv[2]), time.time()
due = []
claude = home / "claude-credential"
for path in [claude / "credential", *sorted((claude / "accounts").glob("*/credential"))]:
    try:
        oauth = json.loads(path.read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        continue  # absent, or a setup token, which never refreshes
    expires = oauth.get("expiresAt")
    if oauth.get("refreshToken") and isinstance(expires, (int, float)):
        left = expires / 1000 - 300 - now  # refreshed 5 min before expiry
        if 0 < left < window:
            due.append(f"{path} in {int(left)}s")
for path in sorted((home / "chatgpt-credential").glob("*/credential")):
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    if not isinstance(doc, dict):
        continue
    expires = doc.get("expires_at")
    if doc.get("refresh_token") and isinstance(expires, (int, float)):
        left = expires - 600 - now  # refreshed 10 min before expiry
        if 0 < left < window:
            due.append(f"{path} in {int(left)}s")
print("; ".join(due))
PY
}

# The rest of the move once `front` serves: lift the steering, remove the old
# container, and stop front-handover once its tunnels have ended. Also run by
# a later release when a move died after `front` started.
finish_cutover() {
  local handover waited=0 open
  unsteer
  trap - EXIT
  docker rm -f "$legacy" >/dev/null
  # The single container's lock beside the ledger; nothing takes it any more.
  docker exec "$(container_of slot-blue)" rm -f /var/log/cheese/usage.jsonl.lock
  handover="$(container_of front-handover)"
  [[ -n "$handover" ]] || return 0
  while :; do
    open="$(open_on "$handover" blue '^connect_slots$')"
    [[ "$open" = 0 ]] && break
    if (( waited >= drain_seconds )); then
      log "front-handover still carries $open tunnels after ${drain_seconds}s; stopping it cuts them"
      break
    fi
    sleep "$poll_seconds"
    waited=$((waited + poll_seconds))
  done
  # Its stop signal is HAProxy's soft stop: it stops listening, so the gateway
  # connects to the other holder of the alias, and exits once the requests it
  # carries have ended.
  "${compose[@]}" --profile handover stop -t "$drain_seconds" front-handover
  "${compose[@]}" --profile handover rm -f front-handover >/dev/null
  log "front-handover stopped after ${waited}s"
}

cutover() {
  local handover ip waited=0 open due before
  [[ "$(docker inspect "$legacy" --format '{{index .Config.Labels "com.docker.compose.project"}}')" = "$project" \
    && "$(docker inspect "$legacy" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')" = "$proxy_home" ]] \
    || fail "$legacy belongs to another compose project or directory than the configured target."
  due="$(refresh_due_during_move "$((2 * drain_seconds + 900))")"
  [[ -z "$due" ]] || fail "A credential refreshes before the move could end, and $legacy refreshes without the lock the slots take: $due. Release again once it has refreshed."
  # A move that died after stopping it leaves it stopped, and it is where
  # traffic goes again if this one fails before the end.
  if [[ "$(docker inspect "$legacy" --format '{{.State.Running}}')" != true ]]; then
    log "$legacy is stopped; starting it again before the move"
    docker start "$legacy" >/dev/null
  fi
  # Nothing may make this release interrupt streams: a failure before the old
  # container stops sends new connections back to it.
  trap 'unsteer' EXIT
  install_front_config || true
  # Left by a move that died part-way, these may already carry tunnels.
  if healthy slot-blue; then log 'slot-blue already serves; keeping it'; else start_slot blue; fi
  if healthy front-handover; then
    log 'front-handover already runs; keeping it'
  else
    "${compose[@]}" --profile handover up -d --no-deps --force-recreate --wait --wait-timeout 90 front-handover \
      || fail 'front-handover did not see slot-blue up; the old container keeps serving.'
  fi
  handover="$(container_of front-handover)"
  # The gateway finds the ChatGPT listener by the alias; with two holders its
  # resolver may pick either, and the old container gets fewer requests to cut.
  # Already connected when kept from a move that died.
  docker network connect --alias metering-proxy cheese-meter-gateway "$handover" 2>/dev/null || true
  ip="$(docker inspect "$handover" --format "{{(index .NetworkSettings.Networks \"${project}_default\").IPAddress}}")"
  [[ -n "$ip" ]] || fail 'front-handover has no address on the default network.'
  unsteer
  steer_to "$ip"
  # Proven by a connection the probe opens itself being counted by the
  # handover front: a probe's 407 alone would come from either.
  before="$(accepted_by "$handover")"
  python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import healthcheck; healthcheck.check(int(sys.argv[3]), sys.argv[2])' \
    "$here/metering-proxy" "$connect_host" "$connect_port" \
    || fail "$connect_host:$connect_port does not answer through front-handover."
  (( "$(accepted_by "$handover")" > before )) \
    || fail "New connections to $connect_host:$connect_port do not reach front-handover."
  log "new connections to $connect_host:$connect_port go to front-handover; waiting for $legacy's (at most ${drain_seconds}s)"
  while :; do
    open="$(established_in "$legacy" 8444)"
    [[ "$open" = 0 ]] && break
    if (( waited >= drain_seconds )); then
      log "$legacy still has $open open connections after ${drain_seconds}s; stopping it cuts them"
      break
    fi
    sleep "$poll_seconds"
    waited=$((waited + poll_seconds))
  done
  for _ in $(seq 60); do
    open="$(established_in "$legacy" 8445)"
    [[ "$open" = 0 ]] && break
    sleep 1
  done
  [[ "$open" = 0 ]] || log "$legacy still carries $open ChatGPT-listener connections; stopping it cuts them"
  "${compose[@]}" up --no-start --no-deps front
  docker stop "$legacy" >/dev/null
  log "$legacy stopped after ${waited}s; $connect_host:$connect_port is free"
  if ! "${compose[@]}" up -d --no-deps --wait --wait-timeout 90 front; then
    "${compose[@]}" rm -f -s front >/dev/null 2>&1 || true
    docker start "$legacy" >/dev/null
    fail "front did not see slot-blue up; $legacy serves again, front-handover keeps the connections it has."
  fi
  log "front serves $connect_host:$connect_port and metering-proxy:8445 through slot-blue"
  finish_cutover
}

front="$(container_of front)"

if [[ -z "$front" ]] && docker inspect "$legacy" >/dev/null 2>&1; then
  cutover
  echo "Metering proxy released: $METERING_PROXY_IMAGE"
  exit 0
fi

[[ -n "$front" ]] || fail 'The metering front is not running; start it before releasing.'
# A move that died after `front` started; it is finished before anything else.
if docker inspect "$legacy" >/dev/null 2>&1; then
  log "$legacy is still here from an unfinished move; finishing it"
  finish_cutover
fi
# The front carries every open stream, so it is never recreated in passing.
wanted="$("${compose[@]}" config --hash front | awk '{ print $2 }')"
running="$(docker inspect "$front" --format '{{index .Config.Labels "com.docker.compose.config-hash"}}')"
if [[ "$wanted" != "$running" ]]; then
  [[ "${METERING_ALLOW_INTERRUPT:-}" = 1 ]] || fail \
    'The metering front itself changed (image or service definition); recreating it interrupts every open stream. Run the Release metering proxy workflow and acknowledge the interruption.'
  install_front_config || true
  log 'recreating the metering front; open streams are interrupted'
  "${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 90 front
  front="$(container_of front)"
elif install_front_config; then
  # A reload hands the listeners to a new process; the old one finishes what it
  # carries, tunnels included. The gateway's idle connections to the old one
  # are closed, so its next request on each fails once (#3640).
  docker run --rm -v "$METERING_FRONT_DIR:/usr/local/etc/haproxy:ro" \
    -e METERING_BLUE_ADDR=10.213.49.2 -e METERING_GREEN_ADDR=10.213.49.3 --entrypoint haproxy \
    "$("${compose[@]}" config --images front)" -c -q -f /usr/local/etc/haproxy/haproxy.cfg \
    || fail 'The new metering front configuration is invalid; nothing was changed.'
  # The slots' state as the front holds it now, for the reloaded front to
  # start from (front.cfg, server-state-file). Removed as soon as the new
  # process runs: HAProxy reads the file on every start, and a restart after a
  # crash or a reboot would otherwise bring back this moment's state, with a
  # slot drained that has since become the one serving.
  old_pid="$(front_pid)"
  runtime "$front" 'show servers state' | docker exec -i "$front" sh -c 'cat > /tmp/server-state'
  docker kill --signal USR2 "$front" >/dev/null
  reloaded=false
  for _ in $(seq 30); do
    pid="$(front_pid)"
    if [[ -n "$pid" && "$pid" != "$old_pid" ]]; then reloaded=true; break; fi
    sleep 1
  done
  docker exec "$front" rm -f /tmp/server-state
  $reloaded || fail 'The metering front did not start a new process after the reload; nothing was switched.'
  log 'the metering front reloaded its configuration'
fi

from=""
for color in blue green; do
  [[ -n "$(container_of "slot-$color")" ]] && is_up "$front" "$color" && is_ready "$front" "$color" || continue
  [[ -z "$from" ]] || fail 'Both slots take traffic; a release was interrupted between its two steps. Release again once one of them is stopped.'
  from="$color"
done
if [[ -n "$from" ]]; then
  serving="$(container_of "slot-$from")"
  # Promoted tags can resolve to the running digest; keep its active streams intact.
  if [[ "$(docker inspect "$serving" --format '{{.Config.Image}}')" = "$METERING_PROXY_IMAGE" \
    && "$(docker inspect "$serving" --format '{{index .Config.Labels "cheese.metering-proxy.config-sha256"}}')" = "$METERING_PROXY_CONFIG_SHA256" ]]; then
    echo "Metering proxy already serves $METERING_PROXY_IMAGE with this configuration from slot-$from; no restart needed."
    exit 0
  fi
  docker inspect "$serving" --format '{{.Image}}' > "$release_dir/previous-image"
fi
to="$(other "${from:-green}")"
[[ -n "$from" ]] || log 'No slot serves now; starting slot-blue.'
# An interrupted release can leave the slot this one replaces draining.
if [[ -n "$(container_of "slot-$to")" ]]; then
  set_state "$front" "$to" drain
  drain_and_stop "$front" "$to"
fi
# Takes nothing until the switch below, however soon the front sees it up.
set_state "$front" "$to" drain
start_slot "$to"
await_up "$front" "$to" || {
  "${compose[@]}" rm -f -s "slot-$to" >/dev/null 2>&1 || true
  fail "The metering front does not see slot-$to up; traffic stays where it was."
}
set_state "$front" "$to" ready
log "new connections go to slot-$to ($METERING_PROXY_IMAGE)"
if [[ -n "$from" ]]; then
  set_state "$front" "$from" drain
  # Its clients' keep-alive connections end after the response each carries,
  # so their next request reconnects to slot-$to (billing_addon.py).
  docker exec "$(container_of "slot-$from")" touch /tmp/cheese-metering-draining
  drain_and_stop "$front" "$from"
fi
echo "Metering proxy released: $METERING_PROXY_IMAGE"
