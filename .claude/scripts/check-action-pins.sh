#!/usr/bin/env bash
# check-action-pins.sh — every GitHub Action must be pinned to a commit SHA.
#
# WHY: `uses: some/action@v3` resolves the tag at run time, and a tag is
# mutable — whoever controls that repo can repoint it at any commit, including
# after we reviewed it. For a project whose CI runs on ephemeral hosted VMs that
# costs one throwaway VM. Ours does not: `nick-fields/retry@v3`,
# `docker/build-push-action@v5`, `docker/setup-buildx-action@v3` and
# `docker/metadata-action@v5` execute on `[self-hosted, cheese-dev]` and
# `[self-hosted, cheese-prod]` — the dev and production boxes themselves, with
# the docker socket, the ghcr credentials and the deploy path right there.
#
# So: a 40-character commit SHA, with the human-readable version in a trailing
# comment (`@<sha> # v4`). Upgrading is then a reviewable diff instead of
# something that happens to us silently.
#
# Usage: check-action-pins.sh [root]        check a tree (default: repo root)
#        check-action-pins.sh --self-test   prove it catches what it claims to
set -euo pipefail

SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }
ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

scan() {
  # Local composite actions (`uses: ./...`) have no upstream to be repointed.
  grep -rnoE '^[[:space:]]*-?[[:space:]]*uses:[[:space:]]*[^[:space:]]+' \
    "$1"/.github/workflows 2>/dev/null \
    | grep -vE 'uses:[[:space:]]*\./' \
    | grep -vE 'uses:[[:space:]]*[^[:space:]]+@[0-9a-f]{40}$' || true
}

if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  mkdir -p "$tmp/.github/workflows"
  me="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  sha=1234567890abcdef1234567890abcdef12345678

  printf 'jobs:\n  a:\n    steps:\n      - uses: actions/checkout@%s # v4\n' "$sha" \
    > "$tmp/.github/workflows/w.yml"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a pinned action must pass"

  printf 'jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n' \
    > "$tmp/.github/workflows/w.yml"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a mutable tag must fail"

  # A short hex string is a tag that merely looks like a SHA, not a pin.
  printf 'jobs:\n  a:\n    steps:\n      - uses: a/b@1234567\n' \
    > "$tmp/.github/workflows/w.yml"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "an abbreviated sha must fail"

  printf 'jobs:\n  a:\n    steps:\n      - uses: ./.github/actions/local\n' \
    > "$tmp/.github/workflows/w.yml"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a local composite action must pass"

  # An exit code alone does not say the author can act on a failure, so the
  # cases below read the message too: what the guard prints is what sends the
  # reader to the right file, or to every file in the tree.
  CODE=0
  OUT=""
  verdict() { OUT="$(bash "$me" "$1" 2>&1)" && CODE=0 || CODE=$?; }
  says() {
    case "$OUT" in
      *"$1"*) ;;
      *) self_fail "$2 (exit $CODE, got: $OUT)" ;;
    esac
  }

  # The shape this guard exists to prevent: a scan that matched nothing because
  # there was nothing to scan reads exactly like a tree that is clean.
  # Under $tmp rather than a second mktemp: the trap takes this one back on the
  # failure path too, and every case keeps reporting a path inside the fixture.
  mkdir "$tmp/none"
  verdict "$tmp/none"
  [ "$CODE" -ne 0 ] || self_fail "a tree with no .github/workflows must fail, not read as clean"
  says "does not exist" "the failure must say the directory was missing"
  says "$tmp/none/.github/workflows" "the failure must name the directory it could not find"

  # A file holding both, in the two shapes this repo writes a step: the pinned
  # action as a list item, the unpinned one under a key of its own. Both are in
  # use — 145 of the 223 `uses:` lines under .github/workflows carry the dash
  # and 78 do not — so the second shape hangs on the `-?` in the scan staying
  # optional. Losing it leaves those lines unread rather than reported, which
  # is the silent half of what this guard is for.
  printf 'jobs:\n  a:\n    steps:\n      - uses: actions/checkout@%s # v4\n      - name: Bad\n        uses: a/b@v1\n' \
    "$sha" > "$tmp/.github/workflows/w.yml"
  verdict "$tmp"
  [ "$CODE" -ne 0 ] || self_fail "an unpinned action beside a pinned one must fail"
  says "a/b@v1" "the report must name the action that is not pinned"
  says "w.yml:6" "the report must point at the line to fix"
  case "$OUT" in
    *"checkout@$sha"*) self_fail "a pinned action must not be reported (got: $OUT)" ;;
  esac

  # The report has to reach the reader: the annotation turns the failure into a
  # line on the pull request, and the other two say where to get the sha and
  # what to write. Matched on the parts the report is for, not on its wording —
  # ids and the path in, prose about.
  says "::error::unpinned GitHub Action" "the failure must carry the annotation CI reads"
  says "repos/<owner>/<repo>/git/ref/tags/" "the failure must say how to resolve the tag"
  says "uses: <owner>/<repo>@<40-char-sha>" "the failure must show the pinned form to write instead"

  echo "PASS: check-action-pins self-test (pinned, mutable tag, short sha, local action,"
  echo "      missing workflows dir, only the offender named, actionable report)"
  exit 0
fi

# A missing directory would make the scan return nothing and read as PASS —
# the same disguised-failure shape this guard exists to prevent.
if [ ! -d "$ROOT/.github/workflows" ]; then
  echo "FAIL: $ROOT/.github/workflows does not exist — nothing was scanned"
  exit 1
fi

hits="$(scan "$ROOT")"
if [ -n "$hits" ]; then
  echo "FAIL: these actions are not pinned to a commit SHA:"
  printf '%s\n' "$hits" | sed 's/^/  /'
  echo ""
  echo "Resolve the tag and pin it, keeping the version as a comment:"
  echo "    gh api repos/<owner>/<repo>/git/ref/tags/<tag> --jq .object.sha"
  echo "    uses: <owner>/<repo>@<40-char-sha> # <tag>"
  echo "::error::unpinned GitHub Action (a mutable tag can be repointed upstream)"
  exit 1
fi
echo "PASS: every action is pinned to a commit SHA"
