#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULE="$SCRIPT_DIR/../../.semgrep/rules/row-read-in-loop.yaml"
SEMGREP=(uvx --python 3.12 --with setuptools semgrep==1.181.0)

# Scan from the repo root with relative paths: absolute paths make the baseline
# comparison drop findings in files the baseline commit cannot see.
ROOT="${ROW_READ_ROOT:-$(git rev-parse --show-toplevel)}"
cd "$ROOT"

if [[ "${1:-}" == "--self-test" ]]; then
  [[ $# -eq 1 ]] || { echo "usage: $0 [--self-test]" >&2; exit 2; }
  "${SEMGREP[@]}" --test --config "$RULE" "$SCRIPT_DIR/../../.semgrep/rules/row-read-in-loop.py"
  # Dirty-tree regression: an untracked violating app file must fail closed.
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  git -C "$TMP" init -q
  git -C "$TMP" -c user.name=t -c user.email=t@t commit -q --allow-empty -m base
  git -C "$TMP" update-ref refs/remotes/origin/main HEAD
  mkdir -p "$TMP/backend/app"
  printf 'async def f(rows, repo):\n    for r in rows:\n        await repo.get_by_id(r.id)\n' > "$TMP/backend/app/x.py"
  # Exit 2 exactly: a missing script (127) or a semgrep failure must not pass
  # for "the guard refused the dirty tree". Run it from the repo root and from
  # a subdirectory, with the absolute path of this script.
  SELF="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
  for dir in "$TMP" "$TMP/backend"; do
    rc=0
    (cd "$dir" && ROW_READ_ROOT="$TMP" bash "$SELF" >/dev/null 2>&1) || rc=$?
    if [[ $rc -ne 2 ]]; then
      echo "self-test FAILED: dirty tree run from $dir exited $rc, expected 2" >&2
      exit 1
    fi
  done
  # Commit-path regression: pre-commit hands this hook a tree whose only
  # difference is staged, and a staged violation must reach semgrep and come
  # back red. Exit 1 exactly, so "reached semgrep" cannot be confused with the
  # exit-2 precondition above.
  git -C "$TMP" add backend/app/x.py
  rc=0
  (cd "$TMP" && ROW_READ_ROOT="$TMP" bash "$SELF" >/dev/null 2>&1) || rc=$?
  if [[ $rc -ne 1 ]]; then
    echo "self-test FAILED: staged violation exited $rc, expected 1" >&2
    exit 1
  fi
  # And a staged benign change is green: the hook must not block commits that
  # touch backend/app just because the index differs from HEAD.
  printf 'def f():\n    return 1\n' > "$TMP/backend/app/x.py"
  git -C "$TMP" add backend/app/x.py
  rc=0
  (cd "$TMP" && ROW_READ_ROOT="$TMP" bash "$SELF" >/dev/null 2>&1) || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "self-test FAILED: staged benign change exited $rc, expected 0" >&2
    exit 1
  fi
  echo "dirty-tree and staged-range self-tests ok"
  exit 0
fi

[[ $# -eq 0 ]] || { echo "usage: $0 [--self-test]" >&2; exit 2; }

# Staged changes are the range this hook is here to judge, not dirt: in a
# pre-commit run they are what the commit is about to contain, and the working
# tree matches the index at that moment (pre-commit stashes everything else).
# Only content outside the index can make semgrep read what the commit does
# not contain, so that is the only thing that fails closed here.
DIRTY="$(git status --porcelain --untracked-files=all -- backend/app \
  | awk 'substr($0, 1, 2) == "??" || substr($0, 2, 1) != " "')"
if [[ -n "$DIRTY" ]]; then
  echo "row-read-in-loop: backend/app has unstaged/untracked changes; commit them first" >&2
  exit 2
fi

BASELINE="${ROW_READ_BASELINE_COMMIT:-}"
if [[ -z "$BASELINE" ]]; then
  BASELINE="$(git merge-base origin/main HEAD)" || {
    echo "row-read-in-loop: cannot find merge-base with origin/main" >&2
    exit 2
  }
fi

exec "${SEMGREP[@]}" scan --config "$RULE" --baseline-commit "$BASELINE" --error backend/app
