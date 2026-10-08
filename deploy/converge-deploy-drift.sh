#!/usr/bin/env bash
# Fire the release that converges a drifted dev box — and pick the commit.
#
# deploy-drift.yml calls this where its comment says "images for main exist but
# the box runs something else": the case it converges by firing the deploy leg
# again. Which commit to fire is the question, because there is one state where
# firing main cannot work and would only repeat a refusal every hour.
#
# A release of a commit from before the two slots per service — a revert, or a
# manual dispatch of an older SHA — runs that commit's own deploy-docker.sh,
# which refuses to switch while app-router names the second slots
# (deploy/tests/test-pre-slot-release.sh). If a revert lands on main while the
# `-b` slots serve, no commit on main has the slots any more, so every
# automatic release of main refuses, drifts again, and comes back here an hour
# later with nothing changed (#2770).
#
# What moves that box is a release of the newest commit on main that still has
# the slots: it runs the slots-era script, which starts the idle slots beside
# the serving ones and switches app-router back to them (read_slots, in
# deploy-docker.sh, picks the other slot from the one backend.conf names). The
# next release of main then goes through; from the first slots, an older commit
# releases as it always did.
#
# The remaining case is state, not silence: when that commit cannot be found,
# this fails with the recovery to run by hand rather than firing a release that
# will refuse.
#
# Env:
#   GITHUB_REPOSITORY    owner/repo, as the workflow provides
#   PROJECT              compose project (default cheese)
#   MAIN_DEPLOY_SCRIPT   the file whose contents say whether main's release has
#                        the slots (default deploy/deploy-docker.sh, relative to
#                        the working tree — deploy-drift.yml checks out the
#                        default branch, so that copy is main's)
set -euo pipefail

PROJECT="${PROJECT:-cheese}"
MAIN_DEPLOY_SCRIPT="${MAIN_DEPLOY_SCRIPT:-deploy/deploy-docker.sh}"
# The compose service of the second backend slot. A tree that names it has the
# two-slot release; one that does not predates it (test-pre-slot-release.sh pins
# that commit, b01c3165, and its script).
SLOTS_MARKER="backend-b"
# How far back to look for a commit that still has the slots. A revert of them
# puts one at most a few commits back; this bounds the API calls when there is
# none at all.
COMMITS_TO_SEARCH=10

dispatch_main() {
  echo "images for main exist but the box runs something else — re-dispatching deploy-dev"
  gh workflow run deploy-dev.yml --ref main
}

if grep -q "$SLOTS_MARKER" "$MAIN_DEPLOY_SCRIPT" 2>/dev/null; then
  dispatch_main
  exit 0
fi

# main's release script has no second slot. It still releases while the first
# slots serve; only the `-b` slots refuse it.
serving="$(docker ps \
  --filter "label=com.docker.compose.project=$PROJECT" \
  --filter label=com.docker.compose.oneoff=False \
  --format '{{.Label "com.docker.compose.service"}}' 2>/dev/null \
  | grep -E '^backend(-b)?$' || true)"
case "$serving" in
  ""|backend)
    dispatch_main
    exit 0
    ;;
  backend-b) ;;
  *)
    # Two backends are running: a release is in flight, and deciding from a
    # half-switched box would act on a slot that is about to stop. The next
    # hourly run sees the result.
    echo "a release is in flight ($(printf '%s' "$serving" | tr '\n' ' '))— leaving convergence to the next run"
    exit 0
    ;;
esac

echo "main's release script has no second slot and the -b slot serves: a release of main would refuse to switch."

recovery=""
while IFS= read -r sha; do
  [ -n "$sha" ] || continue
  content="$(gh api -H 'Accept: application/vnd.github.raw' \
    "repos/${GITHUB_REPOSITORY}/contents/${MAIN_DEPLOY_SCRIPT}?ref=${sha}" 2>/dev/null || true)"
  case "$content" in
    *"$SLOTS_MARKER"*) recovery="$sha"; break ;;
  esac
done < <(gh api \
  "repos/${GITHUB_REPOSITORY}/commits?sha=main&path=${MAIN_DEPLOY_SCRIPT}&per_page=${COMMITS_TO_SEARCH}" \
  --jq '.[].sha')

if [ -z "$recovery" ]; then
  echo "::error::main's release script has no second slot, the -b slot serves, and no commit in the last ${COMMITS_TO_SEARCH} touching ${MAIN_DEPLOY_SCRIPT} has them either. A release of main will keep refusing, so nothing was re-dispatched. Recover by hand: release the last commit that had the slots (gh workflow run deploy-dev.yml --ref main -f ref=<sha>)."
  exit 1
fi

echo "releasing $recovery, the newest commit on main with the slots: it moves app-router back to the first slots, and main's next release then goes through"
gh workflow run deploy-dev.yml --ref main -f "ref=$recovery"
