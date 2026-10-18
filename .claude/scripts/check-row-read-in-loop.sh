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
  if ROW_READ_ROOT="$TMP" bash "${BASH_SOURCE[0]}" >/dev/null 2>&1; then
    echo "self-test FAILED: dirty tree with a violation was accepted" >&2
    exit 1
  fi
  echo "dirty-tree self-test ok"
  exit 0
fi

[[ $# -eq 0 ]] || { echo "usage: $0 [--self-test]" >&2; exit 2; }

if [[ -n "$(git status --porcelain --untracked-files=all -- backend/app)" ]]; then
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
