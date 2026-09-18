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
# mid-run before.
#
# When something IS missing, `flock` serialises the install against the other
# jobs on this host. apt's own `-o DPkg::Lock::Timeout=` is deliberately not
# used: it does not cover the lists lock that `apt-get update` takes (Debian
# #1053726), and that is the lock we lose.
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

echo "ensure-apt: installing ${missing[*]}"
# One lock per host, shared by every job on it. Every job on these runners runs
# as the same user, so a plain /tmp path is lockable by all of them.
flock "${CHEESE_APT_LOCK:-/tmp/cheese-ci-apt.lock}" sudo sh -c \
  "apt-get update -qq && apt-get install -y -qq $(printf '%q ' "${missing[@]}")"
