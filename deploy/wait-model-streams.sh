#!/usr/bin/env bash
# Wait, up to a bound, for the model streams a backend still relays to end.
#
#   wait-model-streams.sh PORT NAME GRACE_SECONDS POLL_SECONDS
#
# PORT is the backend's host port and NAME what to call it in the log.
# deploy-docker.sh runs this on the backend traffic has just left, before it
# stops it: a model stream relayed through /llm/v1 (minutes, on a reasoning
# model) cannot move to the next backend, and stopping the one relaying it ends
# it. Nothing new starts there by then, so the count only falls.
#
# Exits 0 whatever the backend says: running out of time, or a backend that does
# not say (one built before the count answers 404), means the stop goes ahead as
# before. Only a malformed call exits otherwise.
set -uo pipefail

port="${1:?usage: wait-model-streams.sh PORT NAME GRACE_SECONDS POLL_SECONDS}"
name="${2:?name required}"
grace="${3:?grace seconds required}"
poll="${4:?poll seconds required}"
for value in "$grace" "$poll"; do
  [[ "$value" =~ ^[0-9]+$ ]] || { echo "not a whole number of seconds: $value" >&2; exit 2; }
done
[ "$poll" -gt 0 ] || { echo "POLL_SECONDS must be at least 1" >&2; exit 2; }

open_streams() {
  curl -fsS -m 3 "http://127.0.0.1:$port/_internal/llm/streams" 2>/dev/null \
    | sed -n 's/.*"open"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p'
}

open="$(open_streams)"
if [ -z "$open" ]; then
  echo "$name does not say how many model streams it relays; stopping it without waiting"
  exit 0
fi
# The bound is wall time, the probes' own time included: a probe that hangs
# for its 3 s must not stretch it past app-router's worker deadline.
started=$SECONDS
waited=0
last=""
missed=0
while [ "$open" != 0 ]; do
  if [ "$waited" -ge "$grace" ]; then
    echo "$name still relays $open model stream(s) after ${waited}s; stopping it ends them"
    exit 0
  fi
  [ "$open" = "$last" ] \
    || echo "$name relays $open model stream(s); waiting up to $((grace - waited))s for them before stopping it"
  last="$open"
  sleep "$poll"
  waited=$((waited + poll))
  [ $((SECONDS - started)) -le "$waited" ] || waited=$((SECONDS - started))
  answer="$(open_streams)"
  if [ -n "$answer" ]; then
    open="$answer"
    missed=0
  else
    # One probe lost to a busy backend is not the end of its streams.
    missed=$((missed + 1))
    if [ "$missed" -ge 3 ]; then
      echo "$name has not said how many model streams it relays for 3 probes, ${waited}s in; stopping it"
      exit 0
    fi
  fi
done
[ "$waited" -eq 0 ] || echo "$name finished its model streams after ${waited}s"
exit 0
