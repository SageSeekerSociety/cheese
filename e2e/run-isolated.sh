#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Chromium must not observe interfaces created by concurrent Docker jobs on
# the runner. Only the browser moves; localhost is forwarded by Playwright.
version=$(node -p "require('@playwright/test/package.json').version")
dockerfile_hash=$(node -p "require('crypto').createHash('sha256').update(require('fs').readFileSync('browser/Dockerfile')).digest('hex')")
image="cheese-ci-playwright:$version-$dockerfile_hash"
if ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build \
    --build-arg "PLAYWRIGHT_VERSION=$version" --tag "$image" browser
fi
container="cheese-e2e-browser-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$"
cleanup() {
  docker logs "$container" || true
  docker rm -f "$container" >/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker run --name "$container" --detach --rm --init --shm-size=1g \
  --publish 127.0.0.1::3000 "$image" >/dev/null
address=$(docker port "$container" 3000/tcp)
# Docker can accept then reset a connection before the server starts listening.
curl --fail --silent --show-error --retry 30 --retry-all-errors \
  --retry-delay 1 --retry-max-time 30 --max-time 2 "http://$address/" >/dev/null
export E2E_BROWSER_WS_ENDPOINT="ws://$address/"
pnpm exec playwright test "$@"
