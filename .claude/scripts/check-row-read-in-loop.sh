#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
RULE="$ROOT/.semgrep/rules/row-read-in-loop.yaml"

if [[ "${1:-}" == "--self-test" ]]; then
  shift
  [[ $# -eq 0 ]] || { echo "usage: $0 [--self-test]" >&2; exit 2; }
  exec uvx --python 3.12 --with setuptools semgrep==1.181.0 --test \
    --config "$RULE" "$ROOT/.semgrep/rules/row-read-in-loop.py"
fi

[[ $# -eq 0 ]] || { echo "usage: $0 [--self-test]" >&2; exit 2; }

BASELINE="${ROW_READ_BASELINE_COMMIT:-}"
if [[ -z "$BASELINE" ]]; then
  BASELINE="$(git -C "$ROOT" merge-base origin/main HEAD)" || {
    echo "row-read-in-loop: cannot find merge-base with origin/main" >&2
    exit 2
  }
fi

exec uvx --python 3.12 --with setuptools semgrep==1.181.0 scan \
  --config "$RULE" \
  --baseline-commit "$BASELINE" \
  --error \
  "$ROOT/backend/app"
