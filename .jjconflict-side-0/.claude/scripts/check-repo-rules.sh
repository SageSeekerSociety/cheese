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

# Rule 4 — one piece of work leaves ONE note in docs/topics/. Nothing in the
# platform writes these files; 芝士 writes them by hand, and with no convention
# anywhere the same topic has twice been written down under two names in a
# SINGLE accept commit (955707a31 added both authz收敛-分身身份.md and
# 分身独立身份与authz收敛.md; 937a615e7 did the same for the sidebar-collapse
# pair). Two of 85 files, and it compounds — every accept can leave another.
#
# Matching is on the heading plus the first sentence, not on whole-file
# equality: the duplicates are never byte-identical — one is the plan and one
# the finished note, and they diverge within a few lines. That first sentence is
# what actually names the topic. Tuned against the real corpus: 2 non-blank
# lines finds both known pairs among 85 files and pairs nothing else; 3 already
# misses one, because by then the plan and the finished note have split.
check_duplicate_topic_docs() {
  local dir="$ROOT/docs/topics" hits
  [ -d "$dir" ] || return 0
  hits="$(
    for f in "$dir"/*.md; do
      [ -e "$f" ] || continue
      printf '%s\t%s\n' \
        "$(grep -v '^[[:space:]]*$' "$f" | head -2 | tr -d '[:space:]' | md5sum | cut -d' ' -f1)" \
        "${f#"$ROOT/"}"
    done | sort | awk -F'\t' '
      $1 == prev { print "  " prevf "\n  " $2 "\n" }
      { prev = $1; prevf = $2 }'
  )"
  [ -z "$hits" ] && return 0
  echo "FAIL: the same topic is written down twice in docs/topics"
  report "duplicate topic notes" \
    "keep the fuller/later note, delete the other — one topic, one file" "$hits"
}

# Rule 5 — #282 决定 2: a device's SUPPLY FORM (platform-provisioned vs
# human-enrolled) decides whether the platform may destroy that machine, so it is
# stored on `device.supply` and read from there. It must never be re-derived by
# asking whether some row in `project_machines` happens to point at the device.
#
# This is not hypothetical tidiness: that reverse lookup was real, load-bearing
# code (`ProjectMachineRepository.is_provisioned_device`, read by the device
# provider to decide co-location) right up to the commit that added this rule.
# The failure mode it invites is silent — a `cloud` device created by some future
# provisioning path that writes no `project_machines` row reads as self-hosted,
# and the platform then treats a machine it opened as untouchable (or, on the
# co-location path, opens a turn in an empty directory).
#
# Scoped to where the answer is CONSUMED — the device + agent layers. The machine
# layer legitimately owns `project_machines` rows and joins them freely.
check_supply_reverse_lookup() {
  local hits=""
  local dirs="$ROOT/backend/app/domain/device $ROOT/backend/app/domain/agent"
  # (a) the device/agent layers must not reach into the machine table at all.
  hits="$(grep -rn --include='*.py' \
    -e 'ProjectMachineRepository' -e 'from app.domain.machine' \
    $dirs 2>/dev/null || true)"
  # (b) and nowhere in app/ may a function be DEFINED that infers the answer.
  hits="$hits
$(grep -rnE --include='*.py' \
    'def (is_(platform_provisioned|cloud_machine|provisioned_device|self_hosted_device)|(infer|derive|guess)_supply)' \
    "$ROOT/backend/app" 2>/dev/null || true)"
  hits="$(printf '%s\n' "$hits" | grep -v '^[[:space:]]*$' || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: supply form derived by reverse lookup instead of read from device.supply"
  report "supply form must be stored, not inferred (#282)" \
    "read device.supply (app/domain/device/repository.py Supply); set it at the enrolment entry point" \
    "$hits"
}

run_all() {
  check_naive_datetime
  check_builtin_shadowing
  check_raw_http_exception
  check_duplicate_topic_docs
  check_supply_reverse_lookup
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

  # Rule 4. The pair that actually happened is not byte-identical — same opening,
  # one is the plan and one the finished note — so the fixture mirrors that.
  mkdir -p "$tmp/docs/topics"
  printf '## 目标\n\n让每个话题分身有自己的身份。\n\n- 阶段一（本轮必做）：做。\n' \
    > "$tmp/docs/topics/a.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a single topic note must pass"
  printf '## 目标\n\n让每个话题分身有自己的身份。\n\n- 阶段一：**已完成**。\n\n## 结果\n\n绿。\n' \
    > "$tmp/docs/topics/b.md"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the same topic under two names must fail"
  # A genuinely different note sharing only the boilerplate heading must pass,
  # or the guard would fire on every note that opens with 「## 目标」.
  printf '## 目标\n\n把上游冲突交给芝士解决。\n\n- 先定位。\n- 再改。\n' \
    > "$tmp/docs/topics/b.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "different topics sharing a heading must pass"
  rm -rf "$tmp/docs"

  # Rule 5. The fixture is the code that actually existed before #282 决定 2.
  mkdir -p "$tmp/backend/app/domain/device" "$tmp/backend/app/domain/machine"
  printf 'from app.domain.machine.repositories import ProjectMachineRepository\n' \
    > "$tmp/backend/app/domain/device/bad_lookup.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "reaching into the machine table from the device layer must fail"
  # Scoped: the machine layer owns those rows and joins them freely.
  mv "$tmp/backend/app/domain/device/bad_lookup.py" \
     "$tmp/backend/app/domain/machine/ok_lookup.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "the machine layer's own use must pass"
  rm "$tmp/backend/app/domain/machine/ok_lookup.py"
  # An inference function is forbidden wherever it is defined — renaming the
  # reverse lookup into another layer must not launder it.
  printf 'def is_platform_provisioned(device_id):\n    return True\n' \
    > "$tmp/backend/app/core/bad_infer.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "an inference function must fail wherever it lives"
  rm "$tmp/backend/app/core/bad_infer.py"
  # Reading the stored field is the whole point — it must pass.
  printf 'if device.supply is Supply.cloud:\n    pass\n' \
    > "$tmp/backend/app/domain/device/ok_read.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "reading device.supply must pass"
  rm "$tmp/backend/app/domain/device/ok_read.py"

  echo "PASS: check-repo-rules self-test (5 rules, scoping and opt-out verified)"
  exit 0
fi

run_all
if [ "$FAILED" = 1 ]; then
  echo ""
  echo "These rules are stated as absolute in CLAUDE.md; this script only enforces them."
  exit 1
fi
echo "PASS: repo rules (naive datetime, builtin shadowing, raw HTTPException, duplicate topic notes, supply reverse lookup)"
