#!/usr/bin/env bash
# dogfood-bridge — carry an accepted (采纳/merged) change from a dogfooding
# project repo back into THIS dev repo as a conventional-commit branch.
#
# The two repos deliberately use different commit regimes: product repos are
# jj auto-snapshots ("芝士 edits") merged by 采纳; this dev repo is human
# conventional commits reviewed via PR. This script is the bridge that reshapes
# the former into the latter. It is deterministic plumbing only: the commit
# message is REQUIRED input, written by a human/AI — the script never invents
# semantics.
#
# Usage:
#   scripts/dogfood-bridge.sh -r <project-repo> -c <merge-commit> \
#       -m "feat: ..." [-b <branch>] [-x <exclude-path>]...
#
# Example (bridge the accepted 采纳-merge, dropping the artifact page):
#   scripts/dogfood-bridge.sh \
#       -r backend/.workspaces/<project-id> -c 1f9558d \
#       -m "feat: add truncate_words helper" \
#       -b dogfood/truncate-words -x backend/summary.html
set -euo pipefail

die() { echo "dogfood-bridge: $*" >&2; exit 1; }

DEV_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="" COMMIT="" MSG="" BRANCH=""
EXCLUDES=()

while getopts "r:c:m:b:x:" opt; do
  case "$opt" in
    r) SRC="$OPTARG" ;;
    c) COMMIT="$OPTARG" ;;
    m) MSG="$OPTARG" ;;
    b) BRANCH="$OPTARG" ;;
    x) EXCLUDES+=(":(exclude)$OPTARG") ;;
    *) die "unknown option" ;;
  esac
done

[[ -n "$SRC" && -n "$COMMIT" && -n "$MSG" ]] \
  || die "need -r <project-repo> -c <commit> -m <message>"
SRC="$(cd "$SRC" && pwd)" || die "project repo not found: $SRC"
git -C "$SRC" rev-parse -q --verify "$COMMIT^{commit}" >/dev/null \
  || die "commit $COMMIT not found in $SRC"
FULL="$(git -C "$SRC" rev-parse "$COMMIT")"
BRANCH="${BRANCH:-dogfood/${FULL:0:8}}"

# The bridged change = what the 采纳-merge brought in: first-parent diff.
PATCH="$(mktemp "${TMPDIR:-/tmp}/dogfood-bridge.XXXXXX")"
trap 'rm -f "$PATCH"' EXIT
git -C "$SRC" diff --binary "$COMMIT^1" "$COMMIT" -- . "${EXCLUDES[@]+"${EXCLUDES[@]}"}" > "$PATCH"
[[ -s "$PATCH" ]] || die "empty diff (all changes excluded?)"

# Refuse to run on a dirty dev tree — the bridge must be the only change.
[[ -z "$(git -C "$DEV_ROOT" status --porcelain)" ]] \
  || die "dev repo working tree not clean; commit or stash first"
git -C "$DEV_ROOT" show-ref -q --verify "refs/heads/$BRANCH" \
  && die "branch $BRANCH already exists"

PREV="$(git -C "$DEV_ROOT" rev-parse --abbrev-ref HEAD)"
git -C "$DEV_ROOT" checkout -q -b "$BRANCH"
restore() { git -C "$DEV_ROOT" checkout -q "$PREV"; }
if ! git -C "$DEV_ROOT" apply --index --3way "$PATCH"; then
  git -C "$DEV_ROOT" reset -q --hard
  restore
  git -C "$DEV_ROOT" branch -q -D "$BRANCH"
  die "patch did not apply cleanly (dev repo diverged from the project seed?)"
fi
# Author = 芝士 (the change is AI work); the bridging human is the committer.
# The trailer records provenance for audit: which project repo + merge commit.
git -C "$DEV_ROOT" commit -q \
  --author "芝士 <cheese@zhishi.local>" \
  -m "$MSG" \
  -m "Dogfood-Source: $(basename "$SRC")@${FULL:0:12}"
NEW="$(git -C "$DEV_ROOT" rev-parse --short HEAD)"
restore

echo "bridged $COMMIT ($SRC)"
echo "  -> branch $BRANCH commit $NEW"
echo "next: review with 'git show $NEW', run checks, then merge/PR the branch."
