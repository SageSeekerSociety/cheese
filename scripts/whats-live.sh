#!/usr/bin/env bash
# What is actually running on dev, next to what main says.
#
# "The PR was green" and "the change is live" are different facts, and the gap
# between them is invisible: the image build runs AFTER a merge, so a build that
# fails leaves main ahead of the box with nothing red on the PR to show it. That
# gap once hid a fix for two merges. This answers it in one command.
set -euo pipefail

HOST="${CHEESE_DEV_HOST:-cheese-dev-mini}"

main_sha="$(git rev-parse --short HEAD)"
echo "main            : ${main_sha}"

live="$(ssh -o ConnectTimeout=20 -o BatchMode=yes "$HOST" \
  "docker ps --format '{{.Names}} {{.Image}}' | grep -E 'backend|frontend'" 2>/dev/null || true)"

if [ -z "$live" ]; then
  echo "live            : UNREACHABLE (${HOST}) — is the split-tunnel up?"
  exit 2
fi

echo "$live" | while read -r name image; do
  printf '%-16s: %s\n' "${name}" "${image##*:}"
done

if echo "$live" | grep -q ":${main_sha}\b"; then
  echo "verdict         : live matches main"
else
  echo "verdict         : DRIFTED — main is not what is running"
  echo "                  check: gh run list --workflow=build.yml -L 3"
  exit 1
fi
