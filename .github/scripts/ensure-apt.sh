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
apt_waiting() {
  local deadline=$((SECONDS + ${CHEESE_APT_WAIT_SECONDS:-600})) out
  until out="$(sudo apt-get "$@" 2>&1)"; do
    if ! grep -q 'Could not get lock' <<<"$out" || [ "$SECONDS" -ge "$deadline" ]; then
      printf '%s\n' "$out" >&2
      return 1
    fi
    echo "ensure-apt: another apt holds its lock; waiting ($(grep -m1 'Could not get lock' <<<"$out"))"
    sleep 5
  done
  printf '%s\n' "$out"
}

echo "ensure-apt: installing ${missing[*]}"
# One lock per host, shared by every job on it. Every job on these runners runs
# as the same user, so a plain /tmp path is lockable by all of them.
exec 9>"${CHEESE_APT_LOCK:-/tmp/cheese-ci-apt.lock}"
flock 9
apt_waiting update -qq
apt_waiting install -y -qq "${missing[@]}"
