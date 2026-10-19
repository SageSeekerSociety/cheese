#!/usr/bin/env bash
# push-rewritten.sh — push a task branch whose tip was rewritten (amend or
# rebase) after it had already been pushed.
#
# WHY THIS EXISTS: `cheese push-fix` compares commit ids, not trees. A rewritten
# tip therefore reads to it as "the forge has commits this checkout never had"
# and is refused even when the new commit's tree is byte-identical to the
# remote's — exactly what a message-only amend produces. The manual way through
# is `git push --force-with-lease` pinned to the id you last saw. This script is
# that command with the guards a force push needs, in one place.
#
# It refuses to force-push anything but a `task/*` branch; refuses when the two
# histories are not diverged the way a rewrite makes them (a plain fast-forward,
# or a checkout behind the remote that a force would silently clobber); refuses
# when the remote tip is not a commit this branch ever held (a fetch nobody
# integrated, or a different history under the same branch name); and lets
# `--force-with-lease` be the atomic last word on whether the remote still holds
# the commit we validated against. If it does not, the remote moved since the
# last fetch and nothing is pushed.
#
# Usage: push-rewritten.sh [--remote <name>] [--dry-run]
#          --remote <name>  remote to push to (default: origin)
#          --dry-run        run every check, print the push, push nothing
#
# Exit codes:
#   0  pushed, or nothing to push
#   1  git failed for a reason other than the lease
#   2  refused before pushing without judging the branch: not a task/* branch,
#      detached HEAD, unknown argument, no remote-tracking ref to validate
#      against, or a branch with no reflog to check the remote tip against
#   3  refused before pushing: the histories do not diverge like a rewrite, or
#      the remote tip is a commit this branch never held
#   4  refused by the lease: the remote moved since the last fetch
set -euo pipefail

REMOTE=origin
DRY_RUN=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --remote)
      if [ "$#" -lt 2 ]; then
        printf 'push-rewritten: --remote needs a remote name.\n' >&2
        exit 2
      fi
      REMOTE="$2"
      shift 2
      ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      printf 'Usage: push-rewritten.sh [--remote <name>] [--dry-run]\n'
      exit 0
      ;;
    *) printf 'push-rewritten: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
if [ -z "$branch" ]; then
  printf 'push-rewritten: HEAD is detached; refusing to force-push.\n' >&2
  exit 2
fi
case "$branch" in
  task/*) ;;
  *)
    printf 'push-rewritten: refusing to force-push %s — only task/* branches go through here.\n' \
      "$branch" >&2
    exit 2
    ;;
esac

tracking="refs/remotes/$REMOTE/$branch"
remote_oid="$(git rev-parse --verify --quiet "$tracking" || true)"
if [ -z "$remote_oid" ]; then
  printf 'push-rewritten: no %s/%s to validate against; run `git fetch %s` (or push the branch first).\n' \
    "$REMOTE" "$branch" "$REMOTE" >&2
  exit 2
fi
head_oid="$(git rev-parse --verify HEAD)"

if [ "$head_oid" = "$remote_oid" ]; then
  printf 'push-rewritten: %s already points at %s; nothing to push.\n' \
    "$tracking" "${head_oid:0:12}"
  exit 0
fi

# Count the commits each side has that the other lacks. A rewrite — amend or
# rebase — leaves both sides with commits the other does not have, so both
# counts are non-zero. Ancestry (`--is-ancestor`) cannot stand in for this: an
# amend's discarded tip is NOT an ancestor of the new one, so every amend would
# read as unrelated history.
counts="$(git rev-list --left-right --count "$head_oid...$remote_oid")"
ahead="${counts%%[[:space:]]*}"
behind="${counts##*[[:space:]]}"

if [ "$ahead" -eq 0 ]; then
  printf 'push-rewritten: %s has commits this checkout does not; a force push would drop them.\n' \
    "$tracking" >&2
  printf '  Run `git fetch %s` and rebase or merge onto it first.\n' "$REMOTE" >&2
  exit 3
fi
if [ "$behind" -eq 0 ]; then
  printf 'push-rewritten: %s can fast-forward %s; no force needed — use the normal push.\n' \
    "$branch" "$REMOTE" >&2
  exit 3
fi

# The commit we are about to overwrite must be one this branch has actually
# held. A rewrite keeps the commit it discarded in the branch's reflog, so an
# amended or rebased branch passes. A tip that only ever arrived by `git fetch`
# — someone else's commit, or a different history pushed under this branch name
# — never held this branch, and a force push would drop it. The counts above
# cannot tell those apart: unrelated histories also give both counts non-zero,
# and a fetch makes the lease agree with the remote, so neither stops it.
#
# The branch's own reflog, not HEAD's: inspecting a fetched tip (`git checkout
# origin/<branch>` and back) puts it in HEAD's reflog without ever making it
# this branch's, and that must not be enough to overwrite it.
reflog="$(git reflog show --format=%H "refs/heads/$branch" 2>/dev/null || true)"
if [ -z "$reflog" ]; then
  printf 'push-rewritten: %s has no reflog, so this cannot be checked.\n' "$branch" >&2
  printf '  Reflogs are off here (`core.logAllRefUpdates false`?) or were expired;\n' >&2
  printf '  nothing was pushed. Turn reflogs back on, or push by hand as the header says.\n' >&2
  exit 2
fi
if ! grep -qx -- "$remote_oid" <<<"$reflog"; then
  printf 'push-rewritten: refusing: %s is at %s, which %s never held.\n' \
    "$tracking" "${remote_oid:0:12}" "$branch" >&2
  printf '  A force push would drop whatever is on it.\n' >&2
  printf '  Run `git fetch %s` and rebase or merge onto %s/%s to keep it; if the name is\n' \
    "$REMOTE" "$REMOTE" "$branch" >&2
  printf '  not the branch you meant to push, check out the right one.\n' >&2
  exit 3
fi

printf 'push-rewritten: force-pushing %s to %s, lease pinned to %s=%s\n' \
  "$branch" "$REMOTE" "$tracking" "${remote_oid:0:12}"
if [ "$DRY_RUN" -eq 1 ]; then
  printf 'push-rewritten: --dry-run; the push would be:\n'
  printf '  git push --force-with-lease=refs/heads/%s:%s %s HEAD:refs/heads/%s\n' \
    "$branch" "$remote_oid" "$REMOTE" "$branch"
  exit 0
fi

set +e
output="$(git push --force-with-lease="refs/heads/$branch:$remote_oid" \
                   "$REMOTE" "HEAD:refs/heads/$branch" 2>&1)"
status=$?
set -e

if [ "$status" -eq 0 ]; then
  printf '%s\n' "$output"
  printf 'push-rewritten: %s is now %s.\n' "$tracking" "${head_oid:0:12}"
  exit 0
fi

printf '%s\n' "$output" >&2
if printf '%s' "$output" | grep -q 'stale info'; then
  actual="$(git ls-remote --heads "$REMOTE" "refs/heads/$branch" 2>/dev/null | awk 'NR==1{print $1}' || true)"
  printf 'push-rewritten: the remote moved since the last fetch; nothing was pushed.\n' >&2
  printf '  this checkout expects %s/%s = %s\n' "$REMOTE" "$branch" "${remote_oid:0:12}" >&2
  if [ -n "$actual" ]; then
    printf '  the remote is actually at %s\n' "${actual:0:12}" >&2
  fi
  printf '  Run `git fetch %s` and inspect `git log --oneline %s..%s/%s` before forcing.\n' \
    "$REMOTE" "$head_oid" "$REMOTE" "$branch" >&2
  exit 4
fi

printf 'push-rewritten: git push failed (exit %s).\n' "$status" >&2
exit 1
