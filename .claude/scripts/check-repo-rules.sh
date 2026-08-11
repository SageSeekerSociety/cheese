#!/usr/bin/env bash
# check-repo-rules.sh — the CLAUDE.md rules that a linter does not cover.
#
# WHY THIS EXISTS: a rule that lives only in prose decays. The proof is in this
# repo — .claude/rules/backend-tests.md says "Eight files already carry a
# copy-pasted _auth() helper — don't add a ninth", and there are now nine. Every
# rule enforced below is one CLAUDE.md already states as absolute, so this
# script changes nothing about what is allowed; it only moves the enforcement
# from "the agent remembered to read the rules" to "the build is red".
#
# Deliberately NOT here: rules a real linter already covers (ruff/pyright), and
# rules no honest pattern can express. A guard that misfires is worse than no
# guard: people learn to bypass it, and then it protects nothing.
#
# Usage: check-repo-rules.sh [root]        check a tree (default: repo root)
#        check-repo-rules.sh --self-test   prove each rule fires and is scoped
set -euo pipefail

SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
FAILED=0

report() {
  local rule="$1" fix="$2" hits="$3"
  printf '%s\n' "$hits" | sed 's/^/  /'
  echo "  -> $fix"
  echo "::error::$rule"
  FAILED=1
}

# Rule 1 — CLAUDE.md, Datetime: "Always pass datetime.now(UTC) (timezone-aware).
# Never use .replace(tzinfo=None)." Every DB column is TIMESTAMPTZ, so a naive
# datetime does not raise — it silently reads as UTC and shifts the value.
check_naive_datetime() {
  local hits
  hits="$(grep -rn --include='*.py' 'tzinfo=None' "$ROOT/backend/app" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: naive datetime (tzinfo=None) in app code"
  report "naive datetime (tzinfo=None) — every DB column is TIMESTAMPTZ" \
    "use datetime.now(UTC); to compare, make the other side aware instead" "$hits"
}

# Rule 2 — CLAUDE.md, Python Conventions: "Do NOT name methods list, set, dict,
# type — they shadow builtins." A method whose name genuinely IS the domain term
# (prometheus's Gauge.set) may opt out with a marker comment, which keeps the
# exception visible at the definition rather than buried in this script.
#
# The marker counts on the def line OR the line above it. Same-line only would
# make this rule fight ruff's 88-column limit: any honest justification pushes
# the def past E501, and a guard whose escape hatch trips another gate just
# teaches people to delete the guard.
check_builtin_shadowing() {
  local hits
  hits="$(find "$ROOT/backend/app" -name '*.py' -type f -print0 2>/dev/null \
    | xargs -0 -r awk '
        FNR == 1 { prev = "" }
        {
          if ($0 ~ /^[[:space:]]+(async )?def (list|set|dict|type)\(/ &&
              $0 !~ /allow-builtin-shadow/ && prev !~ /allow-builtin-shadow/)
            printf "%s:%d:%s\n", FILENAME, FNR, $0
          prev = $0
        }
      ' || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: method name shadows a builtin"
  report "method name shadows a builtin (list/set/dict/type)" \
    "rename it, or add '# allow-builtin-shadow: <reason>' on that line or the one above" \
    "$hits"
}

# Rule 3 — CLAUDE.md, API Design: "Errors: use app.core.errors classes, not raw
# HTTPException." Scoped to the domain layer on purpose: app/core/errors.py has
# to import it to install the handler, and a route may still translate a
# third-party failure. Business logic raising HTTPException is the actual defect
# — it drags a transport concern into the service layer and bypasses the
# repo's error envelope.
check_raw_http_exception() {
  local hits
  hits="$(grep -rn --include='*.py' 'HTTPException' "$ROOT/backend/app/domain" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: raw HTTPException in the domain layer"
  report "raw HTTPException in the domain layer" \
    "raise the matching class from app.core.errors instead" "$hits"
}

run_all() {
  check_naive_datetime
  check_builtin_shadowing
  check_raw_http_exception
}

# --- self-test -------------------------------------------------------------
# A guard nobody has seen fail is a guard nobody knows works. Each case builds a
# tiny tree, runs this same script against it, and asserts the verdict.
if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  mkdir -p "$tmp/backend/app/domain" "$tmp/backend/app/core"
  me="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

  printf 'x = 1\n' > "$tmp/backend/app/core/clean.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a clean tree must pass"

  printf 'd = d.replace(tzinfo=None)\n' > "$tmp/backend/app/core/bad_dt.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "tzinfo=None must fail"
  rm "$tmp/backend/app/core/bad_dt.py"

  printf 'class C:\n    def set(self, v):\n        pass\n' > "$tmp/backend/app/core/bad_name.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a builtin-shadowing method must fail"
  printf 'class C:\n    def set(self, v):  # allow-builtin-shadow: gauge API\n        pass\n' \
    > "$tmp/backend/app/core/bad_name.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a same-line opt-out marker must be honoured"
  # The line above counts too, so a long justification does not have to collide
  # with ruff's 88-column limit.
  printf 'class C:\n    # allow-builtin-shadow: gauge API\n    def set(self, v):\n        pass\n' \
    > "$tmp/backend/app/core/bad_name.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a previous-line opt-out marker must be honoured"
  # But only the line immediately above — a marker two lines up is not consent.
  printf 'class C:\n    # allow-builtin-shadow: gauge API\n\n    def set(self, v):\n        pass\n' \
    > "$tmp/backend/app/core/bad_name.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a marker two lines up must not count"
  rm "$tmp/backend/app/core/bad_name.py"

  printf 'raise HTTPException(404)\n' > "$tmp/backend/app/domain/bad_err.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "HTTPException in domain must fail"
  # Same line outside the domain layer is allowed — the rule is scoped, and a
  # guard that fires everywhere would just be turned off.
  mv "$tmp/backend/app/domain/bad_err.py" "$tmp/backend/app/core/ok_err.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "HTTPException outside domain must pass"

  echo "PASS: check-repo-rules self-test (3 rules, scoping and opt-out verified)"
  exit 0
fi

run_all
if [ "$FAILED" = 1 ]; then
  echo ""
  echo "These rules are stated as absolute in CLAUDE.md; this script only enforces them."
  exit 1
fi
echo "PASS: repo rules (naive datetime, builtin shadowing, raw HTTPException)"
