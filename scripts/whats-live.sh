#!/usr/bin/env bash
# What is actually running on dev, next to what main says.
#
# "The PR was green" and "the change is live" are different facts, and the gap
# between them is invisible: the image build runs AFTER a merge, so a build that
# fails leaves main ahead of the box with nothing red on the PR to show it. That
# gap once hid a fix for two merges. This answers it in one command.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${CHEESE_DEV_HOST:-cheese-dev-mini}"
PROJECT="${CHEESE_COMPOSE_PROJECT:-cheese}"

# origin/main, not HEAD: run from a feature branch, HEAD is a commit that was
# never meant to be deployed, and every check would report a false drift.
git fetch -q origin main 2>/dev/null || true
main_sha="$(git rev-parse --short=7 origin/main 2>/dev/null || git rev-parse --short=7 main)"
echo "origin/main     : ${main_sha}"

live="$(ssh -o ConnectTimeout=20 -o BatchMode=yes "$HOST" \
  "docker ps -a --filter 'label=com.docker.compose.project=$PROJECT' \
  --format '{{.Label \"com.docker.compose.service\"}}\t{{.Image}}\t{{.State}}\t{{.Status}}'" \
  2>/dev/null || true)"

if [ -z "$live" ]; then
  echo "live            : UNREACHABLE (${HOST}) — is the split-tunnel up?"
  exit 2
fi

echo "$live" | while IFS=$'\t' read -r service image state status; do
  printf '%-16s: tag=%s state=%s status=%s\n' \
    "$service" "${image##*:}" "$state" "$status"
done

if printf '%s\n' "$live" | "$ROOT/deploy/check-app-tier.sh" "$main_sha"; then
  echo "verdict         : live matches main"
else
  echo "verdict         : DRIFTED — main is not what is running"
  echo "                  check: gh run list --workflow=build.yml -L 3"
  exit 1
fi
