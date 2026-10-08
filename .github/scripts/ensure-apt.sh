#!/usr/bin/env bash
# Install the named apt packages, and on the common path do not open apt at all.
#
# Two jobs sharing one self-hosted runner that both run `apt-get update` race
# for /var/lib/apt/lists/lock, and the loser dies with `E: Could not get lock
# /var/lib/apt/lists/lock` before a single step of our own has run. These
# runners are long-lived and these packages are tiny and stable, so every run
# after the first already has them: checking first means the common path never
# touches apt, never races, and never needs the network — which matters here
# because these boxes reach the outside through a gateway that has gone down
# mid-run before. Name the package that actually gets installed (Debian's t64
# renames: `libfuse2t64`, not `libfuse2`): `dpkg -s` never finds a name only
# provided by another package, so asking for it sends every run through apt.
#
# When something IS missing, `flock` serialises the install against the other
# jobs on this host. That lock does not order the host's own apt: Debian's
# apt-daily and unattended-upgrades timers take apt's locks on their own clock.
# apt's `-o DPkg::Lock::Timeout=` cannot wait them out, because it covers only
# dpkg's locks and not the lists lock `apt-get update` takes (Debian #1053726;
# apt 3.0 still takes that one without waiting). So each apt command is
# repeated while, and only while, what it lost on is one of apt's locks.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: ensure-apt.sh <package>..." >&2
  exit 2
fi

missing=()
for pkg in "$@"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done

if [ "${#missing[@]}" -eq 0 ]; then
  echo "ensure-apt: already installed: $*"
  exit 0
fi

# Run `sudo apt-get "$@"`, waiting up to CHEESE_APT_WAIT_SECONDS for a lock that
# another apt holds. Any other failure is returned at once: a lock refusal
# happens before apt changes anything, so only that one is safe to repeat.
#
# apt's own network waits are unbounded by default: on 2026-09-30 a stalled
# mirror held four hosted jobs in `apt-get update` until their job timeouts, 20
# and 30 minutes, with nothing in the log. The Acquire options turn a stall into
# an error within about a minute, Error-Mode=any makes `update` report a failed
# index instead of exiting 0, and a download failure is retried a few times
# before the step gives up.
#
# Those options do not bound everything apt does: on 2026-10-07 four hosted
# integration shards sat in this step for 19 minutes, until the job timeout,
# where it normally takes 19 seconds, again with nothing in the log. So each
# apt command also runs under `timeout` (CHEESE_APT_STEP_SECONDS), a stall is
# repeated once, and apt's output is printed as it comes, so the next stall
# shows the line it stopped on. apt runs non-interactively: debconf and
# needrestart otherwise wait for an answer nobody can give.
apt_net=(-o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30
  -o Acquire::Retries=3 -o APT::Update::Error-Mode=any)
apt_env=(env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a)

apt_waiting() {
  local deadline=$((SECONDS + ${CHEESE_APT_WAIT_SECONDS:-600})) fetches=0 stalls=0 status log
  log="$(mktemp "${TMPDIR:-/tmp}/ensure-apt.XXXXXX")"
  while :; do
    # Into a file, followed by `tail --pid`, not through a pipe: a pipe stays
    # open while any child apt left behind still holds it, so a killed apt
    # could keep this step waiting all the same.
    : > "$log"
    sudo "${apt_env[@]}" timeout --kill-after=10 "${CHEESE_APT_STEP_SECONDS:-180}" \
      apt-get "${apt_net[@]}" "$@" > "$log" 2>&1 &
    local pid=$!
    tail -n +1 -f --pid="$pid" "$log"
    status=0
    wait "$pid" || status=$?
    [ "$status" -eq 0 ] && break
    if [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; then
      if [ "$stalls" -lt 1 ]; then
        stalls=$((stalls + 1))
        echo "ensure-apt: apt-get $1 made no progress in ${CHEESE_APT_STEP_SECONDS:-180}s; retrying once"
        continue
      fi
      echo "ensure-apt: apt-get $1 stalled again; giving up" >&2
    elif grep -q 'Could not get lock' "$log" && [ "$SECONDS" -lt "$deadline" ]; then
      echo "ensure-apt: another apt holds its lock; waiting ($(grep -m1 'Could not get lock' "$log"))"
      sleep 5
      continue
    elif grep -qE 'Failed to fetch|Could not connect|Connection timed out|Temporary failure resolving|Unable to connect' "$log" \
        && [ "$fetches" -lt 3 ]; then
      fetches=$((fetches + 1))
      echo "ensure-apt: download failed; retrying ($fetches/3): $(grep -m1 -E 'Failed to fetch|Could not connect|Connection timed out|Temporary failure resolving|Unable to connect' "$log")"
      sleep 15
      continue
    fi
    cat "$log" >&2
    rm -f "$log"
    return 1
  done
  rm -f "$log"
}

echo "ensure-apt: installing ${missing[*]}"
# One lock per host, shared by every job on it. Every job on these runners runs
# as the same user, so a plain /tmp path is lockable by all of them.
exec 9>"${CHEESE_APT_LOCK:-/tmp/cheese-ci-apt.lock}"
flock 9
apt_waiting update -qq
apt_waiting install -y -qq -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "${missing[@]}"
