#!/usr/bin/env bash
# Check one commit of this repository out into the current directory (the dev
# deploy job's workspace), fetching it from GitHub the way the deploy's
# registry calls are retried (registry-retry.sh). The dev box reaches
# github.com over a link that resets connections in bursts: dev deploy runs
# 40112125458 (2026-10-24), 40154711336 (2026-10-25) and 40315955795
# (2026-10-26, UTC) died in actions/checkout on `Recv failure: Connection reset
# by peer`, its own three fetch attempts spent 38-47 seconds after the first.
#
# Usage: git-fetch.sh <ref> <depth>
#   <ref>    a commit SHA, branch or tag; fetched from origin and checked out
#            detached, with every untracked and ignored file removed
#   <depth>  how many commits of history to fetch
#
# Env:
#   GITHUB_SERVER_URL, GITHUB_REPOSITORY  the repository; set by the runner
#   GIT_FETCH_TOKEN  the token git offers when GitHub asks for credentials. A
#                    credential helper reads it from the environment, so it is
#                    on no command line and in no git config file, and git
#                    never prints it.
#   DEPLOY_PULL_BUDGET_SECONDS / DEPLOY_PULL_BACKOFF_CAP_SECONDS  as in
#                    registry-retry.sh; each run of this script has its own
#                    budget
#
# The deploy workflow runs this from a copy outside the workspace: checking out
# another commit rewrites the files of the workspace, and bash reads a script
# while it runs.
set -euo pipefail

ref="${1:?usage: git-fetch.sh <ref> <depth>}"
depth="${2:?usage: git-fetch.sh <ref> <depth>}"
: "${GITHUB_SERVER_URL:?GITHUB_SERVER_URL is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GIT_FETCH_TOKEN:?GIT_FETCH_TOKEN is required}"
export GIT_FETCH_TOKEN
# Without a terminal a prompt for a username fails anyway; this makes it fail
# at once, with the message the refusal test below recognises.
export GIT_TERMINAL_PROMPT=0

log() { echo "[git-fetch $(date '+%H:%M:%S')] $*"; }
fail() { echo "[git-fetch $(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }
# A fetch of two commits does not fill the disk; nothing to report or reclaim.
log_disk() { :; }
reclaim_before_pull_retry() { :; }

# Refusals a later attempt gets again: a credential GitHub rejects, a
# repository it will not show this token, a ref or commit it does not have
# (`couldn't find remote ref` for a name, `not our ref` for a SHA). A reset
# connection reads `Recv failure: Connection reset by peer` and matches none.
PULL_REFUSED_PATTERN='Authentication failed|could not read Username|Repository not found|returned error: 40[134]|denied|not our ref|couldn.t find remote ref'
PULL_REFUSED_BY=GitHub
. "$(cd "$(dirname "$0")" && pwd)/registry-retry.sh"

url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY"
[ -d .git ] || git init -q .
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$url"
else
  git remote add origin "$url"
fi
# What a killed job can leave behind in a workspace that persists between jobs,
# and that would fail every later run the same way: a token header an
# actions/checkout run wrote and never removed (the token died with its job),
# and git's lock files.
git config --local --unset-all "http.$GITHUB_SERVER_URL/.extraheader" || true
rm -f .git/index.lock .git/shallow.lock

# The empty helper drops any the box configures; the second answers from the
# environment, and only when git asks for credentials (`get`).
# shellcheck disable=SC2016  # expanded by git's shell, not here
helper='!f() { test "$1" = get || exit 0; echo username=x-access-token; echo "password=$GIT_FETCH_TOKEN"; }; f'
fetch() {
  git -c protocol.version=2 -c credential.helper= -c "credential.helper=$helper" \
    fetch --no-tags --no-recurse-submodules --depth="$depth" origin "$ref"
}
retry_pull "git fetch of $ref" fetch

git checkout --quiet --force --detach 'FETCH_HEAD^{commit}'
git clean -ffdxq
log "checked out $(git rev-parse HEAD) ($ref)"
