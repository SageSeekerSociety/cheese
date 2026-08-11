#!/usr/bin/env bash
# install-hooks.sh — put .claude/scripts/pre-commit where git will actually run it.
#
# WHY: the hook has lived in this repo unreferenced. `grep -rn "scripts/pre-commit"`
# across md/sh/yml matched nothing — no task, no script, no doc installed it —
# while CLAUDE.md asserted "Tests MUST pass before any commit. Pre-commit hook
# enforces this." A fresh clone enforced nothing. This is the missing half.
#
# Usage: bash .claude/scripts/install-hooks.sh   (or: task hooks)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="$REPO_ROOT/.claude/scripts/pre-commit"

[ -f "$SOURCE" ] || { echo "ERROR: $SOURCE is missing" >&2; exit 1; }

# --git-common-dir, not --git-dir: in a linked worktree the latter points at a
# per-worktree directory whose hooks/ git does not consult.
if ! HOOKS_DIR="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/hooks"; then
  echo "ERROR: not a git checkout — nothing to install into" >&2
  exit 1
fi

mkdir -p "$HOOKS_DIR"
ln -sf "$SOURCE" "$HOOKS_DIR/pre-commit"
chmod +x "$SOURCE"
echo "installed: $HOOKS_DIR/pre-commit -> $SOURCE"

# Honest about the limit rather than claiming coverage this cannot deliver: the
# hook runs on `git commit`. If you commit through jj, jj does not run git's
# hooks, so the gate for that path is CI, not this. Say so at install time
# instead of letting someone infer protection they do not have.
if [ -d "$REPO_ROOT/.jj" ]; then
  echo ""
  echo "NOTE: this checkout is jj-colocated. jj does not run git hooks, so commits"
  echo "      made with \`jj commit\` bypass this. Run 'bash .claude/scripts/check.sh'"
  echo "      yourself on that path — CI is the backstop either way."
fi
