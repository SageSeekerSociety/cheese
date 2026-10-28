#!/usr/bin/env bash
# Run backend tests on the backend image's own runtime stage (its build-base
# stage: runtime-base plus the pinned uv, nothing else), so code that
# shells out to git runs with the git the image ships rather than the
# runner's newer one: `git merge-tree --quiet` passed on a runner and exits
# 129 on the image's Git 2.47 (PR #4853).
#
# usage (from the repository root): bash backend/scripts/test_with_image_git.sh <test file>...
# Needs docker; DOCKER_CONTEXT picks a non-default one.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
docker build --quiet --target build-base -t cheese-backend-build-base "$root/backend" >/dev/null
docker run --rm \
  -v "$root/backend:/src:ro" \
  -v "$root/frontend/src/i18n/messages:/frontend/src/i18n/messages:ro" \
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
  -e JWT_SECRET=test-secret -e ENVIRONMENT=test \
  cheese-backend-build-base sh -c '
    set -e
    git --version
    # At /work, so app/core/sentences.py finds the catalogs at /frontend, as
    # it does in the image.
    cp -r /src /work && cd /work && rm -rf .venv
    uv sync --frozen --quiet
    uv run --frozen python -m pytest -q -p no:cacheprovider "$@"
  ' sh "$@"
