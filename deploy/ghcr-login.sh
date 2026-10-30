#!/usr/bin/env bash
# Log the box in to ghcr.io before a deploy, retried the way the deploy's pulls
# are (registry-retry.sh): the login crosses the same link that resets
# connections in bursts, and dev deploy run 40138894358 (2026-10-24) died on a
# single login attempt with `read: connection reset by peer`.
#
# A workflow runs this from its own commit, before checking out the commit it
# releases, so a release of an older commit is logged in as well. The images
# are private, and the credential the box has stored is not one to trust: each
# job that logs in leaves its own GITHUB_TOKEN behind, and that token ends with
# its job.
#
# Env:
#   GHCR_USERNAME  the account to log in as
#   GHCR_TOKEN     its token; handed to docker on stdin, never on a command line
#   DEPLOY_PULL_BUDGET_SECONDS / DEPLOY_PULL_BACKOFF_CAP_SECONDS  as in
#                  registry-retry.sh; the login's budget is its own
set -euo pipefail

: "${GHCR_USERNAME:?GHCR_USERNAME is required}"
: "${GHCR_TOKEN:?GHCR_TOKEN is required}"

log() { echo "[ghcr-login $(date '+%H:%M:%S')] $*"; }
fail() { echo "[ghcr-login $(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }
# A login writes no image layers, so there is no disk to report or reclaim.
log_disk() { :; }
reclaim_before_pull_retry() { :; }

. "$(cd "$(dirname "$0")" && pwd)/registry-retry.sh"

ghcr_login() {
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io --username "$GHCR_USERNAME" --password-stdin
}

retry_pull "ghcr login" ghcr_login
