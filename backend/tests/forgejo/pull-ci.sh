#!/usr/bin/env bash
# Internal Linux CI helper: retry only the complete HTTP 503 diagnostic observed
# from Compose. A new/unknown diagnostic fails closed rather than broadening retry.
set -euo pipefail
umask 077

if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_OS:-}" != Linux || $# == 0 ]]; then
  echo "Forge fixture pull retry requires GitHub Actions on Linux and a Compose command" >&2
  exit 2
fi
for tool in timeout awk mktemp cat rm sleep "$1"; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Forge fixture CI pull requires $tool" >&2
    exit 2
  fi
done
# The pull below needs three behaviours of `timeout`, whichever implementation
# provides it; each is checked here so a runner whose `timeout` lacks one stops
# before pulling, naming what is missing. A probe runs bash under `timeout`
# with its stdin and stdout on pipes this script holds: the probe command says
# when its TERM trap is set, prints each TERM it gets, and exits when its stdin
# closes, so nothing a probe starts outlives this script.
probe_command='
  if [[ $1 == exit ]]; then trap "echo term; exit 0" TERM; else trap "echo term" TERM; fi
  echo ready
  until read -r -t 10; do (( $? > 128 )) || exit 0; done'
probe_start() {
  local mode="$1"
  shift
  coproc probe { exec timeout "$@" bash -c "$probe_command" probe "$mode"; }
  probe_pid=$probe_PID
  exec {probe_in}>&"${probe[1]}" {probe_out}<&"${probe[0]}"
  probe_line && [[ "$probe_got" == ready ]]
}
probe_line() { read -r -t 5 -u "$probe_out" probe_got; }
# Read to end of file, which comes once `timeout` and its command have both
# exited, then reap. A probe still running after 5s, or one ended with `stop`,
# is killed and the call returns 1; closing its stdin then ends any command
# the killed `timeout` left behind.
probe_end() {
  local line ended=129
  if [[ "${1:-}" != stop ]]; then
    while :; do read -r -t 5 -u "$probe_out" line || { ended=$?; break; }; done
  fi
  if (( ended > 128 )); then kill -KILL "$probe_pid" 2>/dev/null || :; fi
  if wait "$probe_pid" 2>/dev/null; then probe_status=0; else probe_status=$?; fi
  exec {probe_in}>&- {probe_out}<&-
  (( ended <= 128 ))
}
now_ms() { local now="${EPOCHREALTIME/./}"; echo $((now / 1000)); }
timeout_lacks() {
  echo "Forge fixture CI pull requires a timeout that $1 ($(timeout --version 2>&1 | head -n 1))" >&2
  exit 2
}
# cancel_active's path: TERM sent to `timeout` once the command is ready.
#  1. The command receives that TERM.
#  2. A command that ignores it gets KILL once the --kill-after grace has
#     passed, and not before: the KILL timer starts at the TERM, so its output
#     ends no sooner than the grace after it.
forwarded=0
killed=0
if probe_start stay --kill-after=0.5s 30s; then
  termed="$(now_ms)"
  kill -TERM "$probe_pid" 2>/dev/null || :
  if probe_line && [[ "$probe_got" == term ]]; then forwarded=1; fi
fi
if (( forwarded )) && probe_end && (( $(now_ms) - termed >= 450 )); then
  killed=1
elif (( ! forwarded )); then
  probe_end stop || :
fi
kill_status="$probe_status"
(( forwarded )) || timeout_lacks "forwards TERM to its command"
(( killed )) || timeout_lacks "sends KILL after the --kill-after grace to a command ignoring TERM"
#  3. Exit status 124 when the duration runs out, 137 when KILL was needed.
probe_start exit 0.1s || :
probe_end || :
if [[ "$probe_status" != 124 || "$kill_status" != 137 ]]; then
  timeout_lacks "exits 124 on timeout and 137 after KILL (got $probe_status and $kill_status)"
fi
pull_log="$(mktemp)" || exit 2
active_pid=""
active_kind=""
trap 'rm -f "$pull_log"' EXIT
cancel_active() {
  local signal_status="$1"
  trap '' INT TERM
  if [[ -n "$active_pid" ]]; then
    # Only this helper's directly owned PID is signalled. timeout forwards
    # TERM to its client and enforces the existing 5s KILL grace, even when the
    # client ignores TERM (both checked above). A backoff sleep exits on TERM. Reap before returning;
    # stopping the client does not imply Docker daemon work has stopped.
    kill -TERM "$active_pid" 2>/dev/null || :
    wait "$active_pid" 2>/dev/null || :
  fi
  if [[ "$active_kind" == pull ]]; then cat "$pull_log" || :; fi
  exit "$signal_status"
}
trap 'cancel_active 130' INT
trap 'cancel_active 143' TERM

only_http_503() {
  # Ignore known plain progress, but require every other nonempty line to be
  # a complete, recognized 503 terminal error. Do not grep for a 503 substring:
  # progress byte counts, mixed failures and truncated messages are not proof.
  LC_ALL=C awk '
    {
      gsub(/\033\[[0-?]*[ -/]*[@-~]/, "")
      gsub(/\r/, "")
      sub(/^[ \t]+/, "")
      sub(/[ \t]+$/, "")
    }
    /^$/ { next }
    /^(forgejo|snapshots) Error received unexpected HTTP status: 503 Service Unavailable$/ {
      errors++; next
    }
    /^Error response from daemon: received unexpected HTTP status: 503 Service Unavailable$/ {
      errors++; next
    }
    /^(forgejo|snapshots) (Pulling|Pulled)$/ { next }
    /^[0-9a-f]+ (Pulling fs layer|Waiting|Already exists|Download complete|Verifying Checksum|Pull complete)$/ {
      next
    }
    /^[0-9a-f]+ (Downloading|Extracting) \[[= >]*\][ \t]+[0-9.]+(B|kB|MB|GB)\/[0-9.]+(B|kB|MB|GB)$/ {
      next
    }
    { unknown = 1 }
    END { exit (errors > 0 && !unknown ? 0 : 1) }
  ' "$pull_log"
}

for attempt in 1 2 3; do
  # 3 * (120s + 5s kill grace) + 10s + 20s = at most 405s of client pull
  # and backoff. Timing out a client does not prove the daemon stopped work;
  # any timeout/signal terminates this path without starting another pull.
  # Bash dispatches traps while waiting on a background child, whereas a
  # foreground command would postpone cancellation until it finished.
  active_kind=pull
  timeout --kill-after=5s 120s "$@" --ansi never --progress plain pull --policy missing \
    > "$pull_log" 2>&1 &
  active_pid=$!
  if wait "$active_pid"; then
    status=0
  else
    status=$?
  fi
  active_pid=""
  active_kind=""
  cat "$pull_log" || exit 2
  if [[ "$status" == 0 ]]; then exit 0; fi
  if [[ "$status" != 1 ]] || ! only_http_503 || [[ "$attempt" == 3 ]]; then
    exit "$status"
  fi
  delay=$((attempt * 10))
  echo "Forge fixture pull attempt $attempt failed with only HTTP 503; retrying in ${delay}s" >&2
  active_kind=backoff
  sleep "$delay" &
  active_pid=$!
  wait "$active_pid"
  active_pid=""
  active_kind=""
done
