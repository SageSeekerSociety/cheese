#!/usr/bin/env bash
# check-repo-rules.sh — the CLAUDE.md rules that a linter does not cover.
#
# WHY THIS EXISTS: a rule that lives only in prose decays. The proof is in this
# repo — .claude/rules/backend-tests.md says "Eight files already carry a
# copy-pasted _auth() helper — don't add a ninth", and there are now nine. So
# these rules are enforced rather than asked for: the difference is between "the
# agent remembered to read the rules" and "the build is red".
#
# Each rule below is STATED here, next to its enforcement, and nowhere else.
# They used to be written in CLAUDE.md too, until that file was cut back to
# principles: a rule a script already enforces does not need a second home, and
# a second home is somewhere it can drift out of sync with what actually runs.
#
# Deliberately NOT here: rules a real linter already covers, and rules no honest
# pattern can express. That first category is checked periodically rather than
# assumed — the builtin-shadowing rule lived here until ruff's A003 was found to
# do the same job across every builtin instead of four hand-listed ones, and
# ruff's DTZ turned out to cover a case (`now()` with no tz at all) that the
# hand-rolled datetime rule below never did. What remains below is what ruff
# genuinely cannot see: markdown, .vue templates, cross-file duplication, and
# the shape of a function name. A guard that misfires is worse than no
# guard: people learn to bypass it, and then it protects nothing.
#
# Usage: check-repo-rules.sh [root]        check a tree (default: repo root)
#        check-repo-rules.sh --self-test   prove each rule fires and is scoped
#        check-repo-rules.sh --update-palette-baseline [root]
#                                          ratchet frontend/palette-baseline.json down
set -euo pipefail

SELF_TEST=0
UPDATE_PALETTE=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }
[ "${1:-}" = "--update-palette-baseline" ] && { UPDATE_PALETTE=1; shift; }

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
FAILED=0

report() {
  local rule="$1" fix="$2" hits="$3"
  printf '%s\n' "$hits" | sed 's/^/  /'
  echo "  -> $fix"
  echo "::error::$rule"
  FAILED=1
}

# Rule 1 — always pass datetime.now(UTC); never .replace(tzinfo=None). Every DB
# column is TIMESTAMPTZ, so a naive datetime does not raise — it silently reads
# as UTC and shifts the value.
check_naive_datetime() {
  local hits
  hits="$(grep -rn --include='*.py' 'tzinfo=None' "$ROOT/backend/app" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: naive datetime (tzinfo=None) in app code"
  report "naive datetime (tzinfo=None) — every DB column is TIMESTAMPTZ" \
    "use datetime.now(UTC); to compare, make the other side aware instead" "$hits"
}

# Rule 3 — raise app.core.errors classes, never a raw HTTPException.
# Scoped to the domain layer on purpose: app/core/errors.py has
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

# Rule 6 — .claude/rules/frontend.md: "Never write a colour literal." A Vuetify
# fixed-palette NAME is a colour literal that does not look like one:
# `color="grey-lighten-5"` is exactly `#FAFAFA` in both themes, forever.
#
# WHY A SECOND GUARD, when stylelint already bans hex: stylelint parses CSS. It
# never sees `<template>`, and it never sees a `withDefaults` value in
# `<script>`. So the whole class was invisible to every gate — the wave-1 audit
# counted 244 hex literals and scored zero of these, and the seven that happened
# to sit on the global shell (rail, title bar, mobile bars, v-main) shipped a
# dark theme where the shell stayed near-white while the text on it followed
# --v-theme-on-surface and went pale grey. White-on-white, on every page.
#
# RATCHETED, deliberately: 111 pre-existing hits across 40 view/component files
# are frozen in frontend/palette-baseline.json and only NEW ones fail. A rule
# that goes red on 111 sites gets switched off, and then it protects nothing —
# same reasoning, same shape, as stylelint-baseline.json and tsc-baseline.json.
# Baselines only ever go down: `--update-palette-baseline` refuses to raise one.
#
# NOT matched, on purpose: `transparent` (theme-neutral), `:color="expr"`
# bindings (the value is not visible here), and typography classes like
# `text-h6` / `text-medium-emphasis` — only real palette names follow bg-/text-.
palette_re() {
  local hue='deep-purple|deep-orange|light-blue|light-green|blue-grey'
  hue="$hue|red|pink|purple|indigo|blue|cyan|teal|green|lime|yellow|amber|orange|brown|grey"
  # Longer names lead the alternation so `deep-orange` cannot match as `orange`.
  local shade="((${hue})(-(lighten|darken|accent)-[1-5])?|white|black)"
  printf '%s' "[A-Za-z-]*[Cc]olor=\"${shade}\"|[A-Za-z-]*[Cc]olor: *'${shade}'|\\b(bg|text)-${shade}\\b"
}

# "src/x.vue<TAB>N" for every file with at least one hit, paths relative to
# frontend/ so they match the baseline keys.
palette_counts() {
  local fe="$1"
  [ -d "$fe/src" ] || return 0
  ( cd "$fe" && grep -rEno --include='*.vue' --include='*.ts' "$(palette_re)" src 2>/dev/null || true ) \
    | sed -E 's/^([^:]+):[0-9]+:.*/\1/' | sort | uniq -c \
    | awk '{ printf "%s\t%s\n", $2, $1 }' | sort
}

# The baseline is the same {"files": {path: count}} shape the other two ratchets
# use, so it stays readable in a diff and needs no JSON parser here.
palette_baseline() {
  local f="$1"
  [ -f "$f" ] || return 0
  sed -n 's/^[[:space:]]*"\(src\/[^"]*\)"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1\t\2/p' "$f" | sort
}

check_fixed_palette() {
  local fe="$ROOT/frontend" baseline="$ROOT/frontend/palette-baseline.json"
  [ -d "$fe/src" ] || return 0
  local cur base over hits
  cur="$(palette_counts "$fe")"
  base="$(palette_baseline "$baseline")"
  # A file absent from the baseline has an allowance of 0, so a brand-new file
  # with a violation fails even though nothing about it regressed.
  over="$(awk -F'\t' '
      NR == FNR { allow[$1] = $2; next }
      { a = ($1 in allow) ? allow[$1] : 0
        if ($2 > a) printf "%s\t%d\t%d\n", $1, $2, a }
    ' <(printf '%s\n' "$base") <(printf '%s\n' "$cur"))"
  [ -z "$over" ] && return 0
  hits="$(while IFS=$'\t' read -r f now allow; do
      [ -n "$f" ] || continue
      echo "$f: $now fixed-palette use(s), baseline allows $allow"
      ( cd "$fe" && grep -EnoH "$(palette_re)" "$f" 2>/dev/null || true ) | sed 's/^/    /'
    done <<< "$over")"
  echo "FAIL: Vuetify fixed-palette colour name (does not follow the theme)"
  report "fixed-palette colour name in a template/script" \
    "use a theme colour: background / surface / surface-bright / surface-light / on-surface / primary — see docs/design-system.md" \
    "$hits"
}

# Rule 7 — CLAUDE.md's "This repo does not adapt to the platform": a repository
# must never have to change in order to be hosted, so nothing equally true of
# every hosted repo belongs in THIS repo's CLAUDE.md. The `cheese` CLI is the
# sharpest form of that leak. It already reaches every hosted repo through
# backend/sandbox/skills/cheese/SKILL.md (injected into the system prompt), so a
# second copy here rots on its own schedule AND demonstrates the very adaptation
# we promise nobody has to make. This guard exists because the leak is invisible
# from inside: we are both the platform and a repo it hosts, so platform prose
# reads perfectly natural here — a jj section sat at the top of CLAUDE.md for
# months opening with a sentence that is false on any laptop.
#
# The subcommand list comes from the skill's own command table, so a new
# subcommand is guarded the day it is documented and there is nothing here to
# update. Scoped to invocations, never the bare word: CLAUDE.md may name the
# product, the skill's path, and `cheese` among the CLIs that describe
# themselves — and it does all three.
check_platform_cli_in_claude_md() {
  local skill="$ROOT/backend/sandbox/skills/cheese/SKILL.md" subs hits targets=()
  [ -f "$skill" ] || return 0
  [ -f "$ROOT/CLAUDE.md" ] && targets+=("$ROOT/CLAUDE.md")
  # .claude/rules/ too, and not as an afterthought: the leak this guard was
  # written for turned up there next, in a rules file that was ENTIRELY platform
  # knowledge (cheese gh-token, the App's permission model, how to pull CI logs
  # from a sandbox) while this guard watched only CLAUDE.md. Same reasoning, and
  # rules are worse in one way — they carry a `paths:` trigger, so platform
  # knowledge filed under one gets loaded on an unrelated criterion.
  for f in "$ROOT"/.claude/rules/*.md; do [ -e "$f" ] && targets+=("$f"); done
  [ ${#targets[@]} -eq 0 ] && return 0
  subs="$(grep -oE '`cheese [a-z][a-z-]*' "$skill" 2>/dev/null \
    | sed 's/.*cheese //' | sort -u | paste -sd'|' -)"
  [ -z "$subs" ] && return 0
  hits="$(grep -nE "cheese ($subs)\b" "${targets[@]}" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: platform CLI documented in a file only this repo sees"
  report "the cheese CLI in CLAUDE.md / .claude/rules — a hosted repo never sees either" \
    "move it to backend/sandbox/skills/cheese/SKILL.md, which every session's system prompt already carries" \
    "$hits"
}

run_all() {
  check_naive_datetime
  check_raw_http_exception
  check_duplicate_topic_docs
  check_supply_reverse_lookup
  check_fixed_palette
  check_platform_cli_in_claude_md
}

# --- palette baseline update ------------------------------------------------
# Only ever downward. Raising a baseline to make a gate green is the one move
# that turns a ratchet back into decoration, so this refuses to do it and says
# which file it refused on.
if [ "$UPDATE_PALETTE" = 1 ]; then
  fe="$ROOT/frontend"
  baseline="$fe/palette-baseline.json"
  cur="$(palette_counts "$fe")"
  base="$(palette_baseline "$baseline")"
  raised="$(awk -F'\t' '
      NR == FNR { allow[$1] = $2; next }
      { a = ($1 in allow) ? allow[$1] : 0; if ($2 > a) printf "  %s: %d > %d\n", $1, $2, a }
    ' <(printf '%s\n' "$base") <(printf '%s\n' "$cur"))"
  # Bootstrap: with no baseline on disk there is nothing to raise, and the
  # freeze has to start somewhere. Once the file exists it may only go down —
  # and "delete it and regenerate" is not a quiet workaround, it rewrites every
  # line of the file and shows up as such in review.
  [ -f "$baseline" ] || raised=""
  if [ -n "$raised" ]; then
    echo "refusing to raise the baseline — fix these instead:" >&2
    printf '%s\n' "$raised" >&2
    exit 1
  fi
  {
    echo '{'
    echo '  "_comment": "Frozen Vuetify fixed-palette colour names (color=\"grey-lighten-5\", class=\"bg-white\", …) in templates and script defaults. stylelint cannot see these — it parses CSS, not <template>. The ratchet in .claude/scripts/check-repo-rules.sh blocks any NEW one; these are pre-existing and may only go down. Regenerate with `bash .claude/scripts/check-repo-rules.sh --update-palette-baseline`. See docs/design-system.md.",'
    echo '  "files": {'
    printf '%s\n' "$cur" | awk -F'\t' 'NF == 2 { rows[n++] = sprintf("    \"%s\": %s", $1, $2) }
      END { for (i = 0; i < n; i++) printf "%s%s\n", rows[i], (i < n - 1 ? "," : "") }'
    echo '  }'
    echo '}'
  } > "$baseline"
  echo "wrote $baseline ($(printf '%s\n' "$cur" | grep -c . ) files)"
  exit 0
fi

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

  # Rule 6. The fixture is the line that actually shipped the broken dark shell.
  mkdir -p "$tmp/frontend/src/components"
  shell="$tmp/frontend/src/components/AppBar.vue"
  printf '<template>\n  <v-system-bar color="background" />\n</template>\n' > "$shell"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a semantic theme colour must pass"
  # …and the class form, next to the typography utilities it must NOT confuse
  # itself with (text-h6 / text-medium-emphasis are not palette names).
  printf '<template>\n  <div class="bg-background text-h6 text-medium-emphasis" />\n</template>\n' > "$shell"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "semantic bg-/typography classes must pass"

  printf '<template>\n  <v-system-bar color="grey-lighten-5" />\n</template>\n' > "$shell"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a fixed-palette colour= must fail"
  printf '<template>\n  <div class="bg-grey-lighten-5" />\n</template>\n' > "$shell"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a fixed-palette bg- class must fail"
  printf '<template>\n  <div class="text-white" />\n</template>\n' > "$shell"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a fixed-palette text- class must fail"
  # The prop DEFAULT in <script> is the form stylelint and eslint-plugin-vue
  # both miss, and it is how SecondaryNavigation shipped grey to every consumer.
  printf '<script setup lang="ts">\nwithDefaults(defineProps<P>(), { color: %s })\n</script>\n' \
    "'grey-lighten-5'" > "$shell"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a fixed-palette prop default must fail"

  # The ratchet: the same violation, frozen in the baseline, must pass — and one
  # MORE than the baseline allows must fail. Without both halves this is not a
  # ratchet, it is a rule someone will switch off.
  printf '<template>\n  <v-system-bar color="grey-lighten-5" />\n</template>\n' > "$shell"
  printf '{\n  "files": {\n    "src/components/AppBar.vue": 1\n  }\n}\n' \
    > "$tmp/frontend/palette-baseline.json"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a baselined violation must pass"
  printf '<template>\n  <v-system-bar color="grey-lighten-5" />\n  <v-app-bar color="white" />\n</template>\n' \
    > "$shell"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "one MORE than the baseline must fail"
  # A brand-new file gets an allowance of 0 even while the baseline covers others.
  printf '<template>\n  <v-system-bar color="grey-lighten-5" />\n</template>\n' > "$shell"
  printf '<template>\n  <div class="bg-white" />\n</template>\n' \
    > "$tmp/frontend/src/components/New.vue"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "an unbaselined file must get an allowance of 0"
  rm "$tmp/frontend/src/components/New.vue"
  # --update-palette-baseline must REFUSE to raise an existing baseline. This is
  # the assertion that keeps the ratchet a ratchet: without it, the documented
  # escape from a red gate is "just regenerate the baseline".
  printf '<template>\n  <v-system-bar color="grey-lighten-5" />\n  <v-app-bar color="white" />\n</template>\n' \
    > "$shell"
  bash "$me" --update-palette-baseline "$tmp" >/dev/null 2>&1 \
    && self_fail "--update-palette-baseline must refuse to raise a baseline"
  grep -q '"src/components/AppBar.vue": 1' "$tmp/frontend/palette-baseline.json" \
    || self_fail "a refused update must leave the baseline untouched"
  # Downward it must work, and the tree must then be green.
  printf '<template>\n  <v-system-bar color="background" />\n</template>\n' > "$shell"
  bash "$me" --update-palette-baseline "$tmp" >/dev/null 2>&1 \
    || self_fail "--update-palette-baseline must ratchet down"
  grep -q 'AppBar' "$tmp/frontend/palette-baseline.json" \
    && self_fail "a fixed file must drop out of the baseline entirely"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a tightened baseline must still pass"
  rm -rf "$tmp/frontend"

  # Rule 7. The subcommand list is read from the skill, so the fixture supplies
  # both halves — a skill that documents `cheese doc`, and a CLAUDE.md that
  # leaks it.
  mkdir -p "$tmp/backend/sandbox/skills/cheese"
  printf '| `cheese doc set <file>` | set the live doc |\n' \
    > "$tmp/backend/sandbox/skills/cheese/SKILL.md"
  printf '# Project\n\nPublish with `cheese doc set ./x.md`.\n' > "$tmp/CLAUDE.md"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the cheese CLI in CLAUDE.md must fail"
  # Naming the product, the skill path, and the bare binary must all pass —
  # a guard that fires on the word would make this section unwritable.
  printf '# Project\n\nThe `cheese` CLI is documented in backend/sandbox/skills/cheese/SKILL.md.\nReview via the cheese-py-code-review skill; any repo running on cheese gets it.\n' \
    > "$tmp/CLAUDE.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "naming the product must pass"
  # A subcommand the skill does not document is not this guard's business.
  printf '# Project\n\nRun `cheese frobnicate` daily.\n' > "$tmp/CLAUDE.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "an undocumented subcommand must not fire"
  # The same leak in .claude/rules/ — where it actually turned up, after this
  # guard had been watching CLAUDE.md alone.
  rm -f "$tmp/CLAUDE.md"
  mkdir -p "$tmp/.claude/rules"
  printf -- '---\npaths:\n  - "backend/**"\n---\n\nPublish with `cheese doc set ./x.md`.\n' \
    > "$tmp/.claude/rules/leaky.md"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the cheese CLI in .claude/rules must fail"
  printf -- '---\npaths:\n  - "backend/**"\n---\n\nThe `cheese` CLI is elsewhere.\n' \
    > "$tmp/.claude/rules/leaky.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "naming the product in a rule must pass"
  rm -rf "$tmp/backend/sandbox" "$tmp/.claude"

  echo "PASS: check-repo-rules self-test (6 rules, scoping and the palette ratchet verified)"
  exit 0
fi

run_all
if [ "$FAILED" = 1 ]; then
  echo ""
  echo "Each rule above is stated where it is enforced; the comment on it says why it exists."
  exit 1
fi
echo "PASS: repo rules (naive datetime, raw HTTPException, duplicate topic notes, supply reverse lookup, fixed-palette colours, platform CLI in CLAUDE.md)"
