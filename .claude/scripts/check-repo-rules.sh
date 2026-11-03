#!/usr/bin/env bash
# check-repo-rules.sh — ONLY the rules ruff cannot see.
#
# THE TEST BEFORE ADDING ANYTHING HERE: can ruff do this? If it can, it belongs
# in backend/pyproject.toml and not in this file. Reimplementing a linter in
# bash is worse than not checking at all — it is slower, it matches text instead
# of syntax, it needs its own opt-out convention, and it rots while the real
# linter gains rules nobody switches on. This was not hypothetical: the
# builtin-shadowing rule lived here until ruff's A003 was found to do the same
# job across every builtin rather than four hand-listed names, and switching on
# ruff's DTZ at the same time caught a case (a now() with no tz at all) this
# file had never checked, in live code.
#
# So what is left below is what ruff structurally cannot reach: markdown files,
# .vue templates, relationships BETWEEN files, and the shape of a name. Ask the
# question again whenever a rule is added, and re-ask it for the existing ones
# when ruff ships a release.
#
# WHY THIS EXISTS AT ALL: a rule that lives only in prose decays. The proof is in this
# repo — .claude/rules/backend-tests.md says "Eight files already carry a
# copy-pasted _auth() helper — don't add a ninth", and there are now nine. So
# these rules are enforced rather than asked for: the difference is between "the
# agent remembered to read the rules" and "the build is red".
#
# Each rule below is STATED here, next to its enforcement, and nowhere else.
# They used to be written in AGENTS.md too, until that file was cut back to
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
# Numbering has holes in it (there is no Rule 2 or 4): Rule 2 checked for
# methods named after builtins, which ruff's A003 does across every builtin;
# Rule 4 (one note per topic) policed note naming, which this repository no
# longer gates. Each was deleted rather than renumbered.
# Renumbering would break the references in git history and in the sibling rules.
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

# Rule 5 — #282 决定 2: a device's SUPPLY FORM (platform-provisioned vs
# human-enrolled) decides whether the platform may destroy that machine, so it is
# stored on `device.supply` and read from there. It must never be re-derived by
# asking whether some row in `cloud_hosts` happens to point at the device.
#
# This is not hypothetical tidiness: that reverse lookup was real, load-bearing
# code (`ProjectMachineRepository.is_provisioned_device`, read by the device
# provider to decide co-location) right up to the commit that added this rule.
# The failure mode it invites is silent — a `cloud` device created by some future
# provisioning path that writes no `cloud_hosts` row reads as self-hosted,
# and the platform then treats a machine it opened as untouchable (or, on the
# co-location path, opens a turn in an empty directory).
#
# Scoped to where the answer is CONSUMED — the device + agent layers. The machine
# layer legitimately owns `cloud_hosts` rows and joins them freely.
check_supply_reverse_lookup() {
  local hits=""
  local dirs="$ROOT/backend/app/domain/device $ROOT/backend/app/domain/agent"
  # (a) the device/agent layers must not reach into the machine table at all.
  # The import is spelled out to its boundary (`machine import` / `machine.`) so
  # a sibling package whose name merely starts with it — `machine_skill` — is not
  # read as the machine domain. A bare prefix matched that one and failed the
  # guard on a legitimate import.
  hits="$(grep -rn --include='*.py' \
    -e 'CloudHostRepository' -e 'from app.domain.machine import' -e 'from app.domain.machine\.' \
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
# `color="grey-lighten-5"` is exactly `#FAFAFA` in both themes, forever, so it
# is wrong in one of them — not a style choice but a dark-theme defect.
#
# WHY A SECOND GUARD, when stylelint already bans hex: stylelint parses CSS. It
# never sees `<template>`, and it never sees a `withDefaults` value in
# `<script>`. The seven that sat on the global shell (rail, title bar, mobile
# bars, v-main) shipped a dark theme where the shell stayed near-white while the
# text on it followed --v-theme-on-surface and went pale grey. White-on-white,
# on every page. The tree holds none now, so any one is a failure.
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

check_fixed_palette() {
  local fe="$ROOT/frontend" hits
  [ -d "$fe/src" ] || return 0
  hits="$( cd "$fe" && grep -rEn --include='*.vue' --include='*.ts' "$(palette_re)" src 2>/dev/null || true )"
  [ -z "$hits" ] && return 0
  echo "FAIL: Vuetify fixed-palette colour name (does not follow the theme)"
  report "fixed-palette colour name in a template/script" \
    "use a theme colour: background / surface / surface-bright / surface-light / on-surface / primary — see docs/design-system.md" \
    "$hits"
}

# Rule 7 — AGENTS.md's "This repo does not adapt to the platform": a repository
# must never have to change in order to be hosted, so nothing equally true of
# every hosted repo belongs in THIS repo's AGENTS.md. The `cheese` CLI is the
# sharpest form of that leak. It already reaches every hosted repo: the tools
# carry their own descriptions and the subcommands are in the `cheese` skill
# every session receives, so a
# second copy here rots on its own schedule AND demonstrates the very adaptation
# we promise nobody has to make. This guard exists because the leak is invisible
# from inside: we are both the platform and a repo it hosts, so platform prose
# reads perfectly natural here — a jj section sat at the top of AGENTS.md for
# months opening with a sentence that is false on any laptop.
#
# The tool names come from the CLI's own tool table and the subcommands from the
# skill's command table, so a new one is guarded the day it exists and there is
# nothing here to update. Scoped to invocations, never the bare word: AGENTS.md may name the
# product, the skill's path, and `cheese` among the CLIs that describe
# themselves — and it does all three.
check_platform_cli_in_claude_md() {
  local skill="$ROOT/backend/sandbox/skills/cheese/SKILL.md" cli="$ROOT/backend/sandbox/cheese" subs hits targets=()
  [ -f "$skill" ] || return 0
  [ -f "$ROOT/AGENTS.md" ] && targets+=("$ROOT/AGENTS.md")
  # .claude/rules/ too, and not as an afterthought: the leak this guard was
  # written for turned up there next, in a rules file that was ENTIRELY platform
  # knowledge (cheese gh-token, the App's permission model, how to pull CI logs
  # from a sandbox) while this guard watched only AGENTS.md. Same reasoning, and
  # rules are worse in one way — they carry a `paths:` trigger, so platform
  # knowledge filed under one gets loaded on an unrelated criterion.
  for f in "$ROOT"/.claude/rules/*.md; do [ -e "$f" ] && targets+=("$f"); done
  [ ${#targets[@]} -eq 0 ] && return 0
  # A tool (`cheese_doc(action=set)`) and a subcommand (`cheese sync`) name the same
  # kind of thing, and either spelling in AGENTS.md is the leak. `|| true`: a
  # skill or CLI with no names must leave this guard inert, not abort the
  # script under `set -e`.
  subs="$( { grep -oE '`cheese [a-z][a-z-]*' "$skill" 2>/dev/null | sed 's/.*cheese //'; \
             grep -oE '^ +"cheese_[a-z_]+",' "$cli" 2>/dev/null | sed 's/.*"cheese_//; s/",$//'; } \
    | tr '_' '-' | sort -u | paste -sd'|' - || true)"
  [ -z "$subs" ] && return 0
  tools="$(printf '%s' "$subs" | tr '-' '_')"
  hits="$(grep -nE "cheese ($subs)\b|cheese_($tools)\b" "${targets[@]}" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: platform CLI documented in a file only this repo sees"
  report "the cheese CLI in AGENTS.md / .claude/rules — a hosted repo never sees either" \
    "move it to backend/sandbox/skills/cheese/SKILL.md (or the tool's own description), which every session receives" \
    "$hits"
}

# Rule 8 — 退役的档位名字不能以字符串形式写进 `author_type`。这一列只有两档：
# `participant` 和 `platform`，`human`、`ai`、`system` 都已经离场。
#
# 写成 `AuthorType.system` 不需要守卫：成员不在了，一取就是 AttributeError。写成
# 字符串需要。这一列的类型是 `Enum(AuthorType, native_enum=False, length=16)`，没传
# `validate_strings`（默认 False），于是 SQLAlchemy 把它不认识的字符串原样绑进
# INSERT（`sqlalchemy/sql/sqltypes.py` 的 `_db_value_for_elem`），列上也没有 CHECK
# 约束接着。写的那一刻什么都不响，LookupError 要等下一次 select 取到这行才抛，那时
# 整条时间线打不开，正是 `2895c4967ca4` 这次发布要终结的故障。
#
# 写错的形状就长在眼前：平台自己的事件署名 `author="system"`（那是 handle，
# `backend/app` 里十几处，都对），在它旁边再写一个 `author_type="system"` 是顺手的事。
#
# 别处接不住：`tests/unit/test_author_type_two_values.py` 的 AST 扫描追的是**读**
# （`author_type.value`，以及拿这一列和非成员比），关键字实参是写点，不匹配。
#
# 只看 `backend/app`：`tests/` 里要造存量行（迁移测试就得写 'system'），
# `alembic/versions/` 里的历史迁移是写死的过去。整行注释跳过：注释一行都写不进库。
check_retired_author_type_as_string() {
  local hits
  hits="$(grep -rnE --include='*.py' \
    'author_type *= *["'"'"'](human|ai|system)["'"'"']' \
    "$ROOT/backend/app" 2>/dev/null | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#' || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: 退役的档位名字以字符串形式写进了 author_type"
  report "a retired author_type name written as a string — SQLAlchemy binds it as-is" \
    "use AuthorType.participant or AuthorType.platform; the string goes in unchecked and raises on the next read" \
    "$hits"
}

# Rule 9 — an agent's handle is derived from the AGENT, never from a room. A
# room is a collaboration space and may seat several agents, so a handle derived
# from one gives two agents in that room the same name, and the same agent two
# names in two rooms — and the name is what a seat grants, what a block is signed
# with, what a token carries and what keys a memory pool, so the four disagree
# the moment a room seats a second agent.
#
# That derivation really existed (`topic_agent_handle`, `cheese-<topic hex12>`),
# under its own name beside the correct one, for long enough that nine files
# called it. Deleting it is not self-enforcing: nothing goes red when somebody
# writes the same three lines again next to a room id, and the handle it returns
# looks exactly like a real one. So the guard matches the SHAPE of the name, not
# one spelling — `room_agent_handle` is the same function with the launder
# applied.
#
# `agent_instance_handle` is the one minting function and passes: it names the
# agent it derives from. Scoped to the source trees; prose may still discuss the
# retired one.
check_room_derived_agent_handle() {
  local hits
  hits="$(grep -rnE --include='*.py' --include='*.ts' --include='*.vue' \
    '(topic|room|place|screen)_agent_handle' \
    "$ROOT/backend" "$ROOT/frontend" 2>/dev/null || true)"
  [ -z "$hits" ] && return 0
  echo "FAIL: an agent handle derived from a room"
  report "an agent handle derived from a room, not from the agent" \
    "derive it from the agent: identity.handles.agent_instance_handle(instance_id)" \
    "$hits"
}

# Rule 10 — a spec that asserts what DOMPurify.sanitize() left must run under
# jsdom. frontend/vite.config.ts defaults vitest to happy-dom, and there
# DOMPurify 3.4.16 answers any input carrying `<script>` with an empty string, so
# every "the dangerous part is gone" assertion passes whether or not anything
# was cleaned — the worst shape a failure can take, a security assertion that is
# green either way. jsdom behaves like the browser. Measured 2026-10-08 on
# frontend/src/utils/parser.spec.ts.
#
# The environment is chosen per FILE, so this reads the file's own text and not
# the config: a spec that forgets the pragma still passes, and nothing else in
# the build can tell the difference. What decides the environment is the FIRST
# annotation anywhere in the raw text — vitest 5.0.3's detectCodeBlock runs
# `content.match(/@(?:vitest|jest)-environment\s+([\w-]+)\b/)` over the whole
# file — so a check that looked only at the top reports specs that run under
# jsdom perfectly well, and one that asks whether jsdom appears anywhere clears
# a spec whose first pragma names happy-dom. Both ends of that regex are
# reproduced here: the name starts at the first annotation, and ends at the
# first character that cannot be in one.
check_dompurify_specs_use_jsdom() {
  local f files=() first
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    # Newlines to spaces first: vitest's `\s+` crosses them, grep's does not.
    first="$(tr '\n' ' ' < "$f" \
      | grep -oE '@(vitest|jest)-environment[[:space:]]+[A-Za-z0-9_-]+' | head -n 1 || true)"
    case "$first" in *[[:space:]]jsdom) ;; *) files+=("$f") ;; esac
  done < <(grep -rlE --include='*.spec.ts' 'DOMPurify|\.sanitize\(' "$ROOT/frontend/src" 2>/dev/null || true)
  [ ${#files[@]} -eq 0 ] && return 0
  echo "FAIL: a spec on DOMPurify that does not run under jsdom"
  report "a spec asserting on DOMPurify.sanitize() without a jsdom environment — happy-dom returns '' and the assertion passes by accident" \
    "put '// @vitest-environment jsdom' at the top of the spec, before the imports" \
    "$(printf '%s\n' "${files[@]}")"
}

# Rule 11 — a command that runs a script INSIDE the backend container must name
# a module the production image copies. Both deployments run the Dockerfile's
# `production` stage (build.yml's `target: production`), which carries no source
# and names the `scripts/*.py` it ships one file at a time — the rest of that
# directory is dev probes and demo seeders that have no business in the image.
# The repository has the file, so `python -m scripts.<name>` reads as runnable
# everywhere and raises ModuleNotFoundError on the box. Measured 2026-10-14
# (PR #3753): `docker exec … python -m scripts.seat_throughput` ran nowhere,
# written both in a document and in the script's own docstring.
#
# Read in docs/ and deploy/ only, where a line that says `uv run` is a checkout
# command (the whole source tree is right there) and what is left is what an
# operator is told to run on a deployed box. The workflows are left out on
# purpose: there the same spelling runs on the CI runner, in the checkout
# (test.yml's assert_shards_complete), and means the opposite.
check_container_script_is_in_the_image() {
  local dockerfile="$ROOT/backend/Dockerfile"
  [ -f "$dockerfile" ] || return 0
  local stage copied
  stage="$(awk '/^FROM /{p=($0 ~ /AS production([[:space:]]|$)/)} p' "$dockerfile")"
  [ -n "$stage" ] || return 0
  # A directory copy would put every script in the image; there is nothing to
  # compare against, and reading nothing is better than reading a wrong set.
  printf '%s\n' "$stage" | grep -qE '^COPY[^#]*[[:space:]]scripts/?([[:space:]]|$)' && return 0
  # `|| true`: a stage that names no scripts/&lt;name&gt;.py leaves grep with nothing
  # to match, and under `set -eo pipefail` that status would take the whole guard
  # down here — before the inert return below, and with no FAIL line to say why.
  copied="$(printf '%s\n' "$stage" | grep -oE 'scripts/[A-Za-z0-9_]+\.py' \
    | sed -E 's|scripts/||; s|\.py$||' | sort -u || true)"
  [ -n "$copied" ] || return 0
  local line mod lines=()
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    mod="$(printf '%s\n' "$line" | sed -nE 's|.*python3? -m scripts\.([A-Za-z0-9_]+).*|\1|p')"
    printf '%s\n' "$copied" | grep -qx "$mod" || lines+=("$line")
  done < <(grep -rnE 'python3? -m scripts\.[A-Za-z0-9_]+' "$ROOT/docs" "$ROOT/deploy" 2>/dev/null \
             | grep -v 'uv run' || true)
  [ ${#lines[@]} -eq 0 ] && return 0
  echo "FAIL: a script run in the backend container that the image does not carry"
  report "a python -m scripts.<name> meant for the deployed box, naming a module backend/Dockerfile's production stage never COPYs" \
    "run it where the source is, or add COPY --chown=cheese:python scripts/<name>.py ./scripts/ to the production stage" \
    "$(printf '%s\n' "${lines[@]}")"
}

run_all() {
  check_naive_datetime
  check_raw_http_exception
  check_supply_reverse_lookup
  check_fixed_palette
  check_platform_cli_in_claude_md
  check_retired_author_type_as_string
  check_room_derived_agent_handle
  check_dompurify_specs_use_jsdom
  check_container_script_is_in_the_image
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

  printf 'raise HTTPException(404)\n' > "$tmp/backend/app/domain/bad_err.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "HTTPException in domain must fail"
  # Same line outside the domain layer is allowed — the rule is scoped, and a
  # guard that fires everywhere would just be turned off.
  mv "$tmp/backend/app/domain/bad_err.py" "$tmp/backend/app/core/ok_err.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "HTTPException outside domain must pass"

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
  rm -rf "$tmp/frontend"

  # Rule 7. The subcommand list is read from the skill, so the fixture supplies
  # both halves — a skill that documents `cheese doc`, and an AGENTS.md that
  # leaks it.
  mkdir -p "$tmp/backend/sandbox/skills/cheese"
  printf '| `cheese doc set <file>` | set the live doc |\n' \
    > "$tmp/backend/sandbox/skills/cheese/SKILL.md"
  printf '# Project\n\nPublish with `cheese doc set ./x.md`.\n' > "$tmp/AGENTS.md"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the cheese CLI in AGENTS.md must fail"
  # Naming the product, the skill path, and the bare binary must all pass —
  # a guard that fires on the word would make this section unwritable.
  printf '# Project\n\nThe `cheese` CLI is documented in backend/sandbox/skills/cheese/SKILL.md.\nReview via the cheese-py-code-review skill; any repo running on cheese gets it.\n' \
    > "$tmp/AGENTS.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "naming the product must pass"
  # A subcommand the skill does not document is not this guard's business.
  printf '# Project\n\nRun `cheese frobnicate` daily.\n' > "$tmp/AGENTS.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "an undocumented subcommand must not fire"
  # The same leak in .claude/rules/ — where it actually turned up, after this
  # guard had been watching AGENTS.md alone.
  rm -f "$tmp/AGENTS.md"
  mkdir -p "$tmp/.claude/rules"
  printf -- '---\npaths:\n  - "backend/**"\n---\n\nPublish with `cheese doc set ./x.md`.\n' \
    > "$tmp/.claude/rules/leaky.md"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the cheese CLI in .claude/rules must fail"
  printf -- '---\npaths:\n  - "backend/**"\n---\n\nThe `cheese` CLI is elsewhere.\n' \
    > "$tmp/.claude/rules/leaky.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "naming the product in a rule must pass"
  rm -rf "$tmp/backend/sandbox" "$tmp/.claude"

  # Rule 8. The fixture is the half that can actually reach the database: the
  # string form, which binds unchecked.
  mkdir -p "$tmp/backend/app/domain/review"
  printf 'await blocks.add(author="system", author_type="system")\n' \
    > "$tmp/backend/app/domain/review/bad_sig.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the string form must fail"
  printf 'await blocks.add(author="cheese", author_type="ai")\n' \
    > "$tmp/backend/app/domain/review/bad_sig.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the retired name ai must fail too"
  printf 'await blocks.add(author="cheese", author_type="human")\n' \
    > "$tmp/backend/app/domain/review/bad_sig.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the retired name human must fail too"
  printf 'await blocks.add(author="cheese", author_type=AuthorType.platform)\n' \
    > "$tmp/backend/app/domain/review/bad_sig.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "AuthorType.platform must pass"
  # The handle is another column and keeps the word — that is what a live
  # platform write looks like, and firing on it would make the guard unusable.
  printf 'await blocks.add(author="system", author_type=AuthorType.platform)\n' \
    > "$tmp/backend/app/domain/review/bad_sig.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "the author handle must pass"
  rm "$tmp/backend/app/domain/review/bad_sig.py"
  # Scoped to app/: a migration test has to build a row under the old name.
  mkdir -p "$tmp/backend/tests"
  printf 'await blocks.add(author="system", author_type="system")\n' \
    > "$tmp/backend/tests/test_rewrite.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "tests naming the old value must pass"
  rm -rf "$tmp/backend/tests"

  # Rule 9. The fixture is the function that actually existed until P11.
  mkdir -p "$tmp/backend/app/domain/identity"
  printf 'def topic_agent_handle(topic_id):\n    return "cheese-" + topic_id.hex[:12]\n' \
    > "$tmp/backend/app/domain/identity/handles.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a room-derived agent handle must fail"
  # Renaming it into another room word must not launder it.
  printf 'def room_agent_handle(room_id):\n    return "cheese-" + room_id.hex[:12]\n' \
    > "$tmp/backend/app/domain/identity/handles.py"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the same function under a room word must fail"
  # Deriving from the agent is the whole point — it must pass.
  printf 'def agent_instance_handle(instance_id):\n    return "cheese-" + instance_id.hex[:12]\n' \
    > "$tmp/backend/app/domain/identity/handles.py"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "deriving from the agent must pass"
  rm "$tmp/backend/app/domain/identity/handles.py"

  # Rule 10. The environment is picked per file, so the fixture is the file's
  # header — the one thing no other tool in the build can see, and the reason a
  # spec that forgets the pragma still passes.
  mkdir -p "$tmp/frontend/src/utils"
  printf "import DOMPurify from 'dompurify'\n\nexpect(DOMPurify.sanitize('<b>x</b>')).toBe('<b>x</b>')\n" \
    > "$tmp/frontend/src/utils/clean.spec.ts"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a sanitize assertion under the default environment must fail"
  printf "// @vitest-environment jsdom\nimport DOMPurify from 'dompurify'\n\nexpect(DOMPurify.sanitize('<b>x</b>')).toBe('<b>x</b>')\n" \
    > "$tmp/frontend/src/utils/clean.spec.ts"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "the same assertion under jsdom must pass"
  # Position is not part of it: vitest matches the pragma against the whole
  # file, so one written below the imports is honoured and must not be reported.
  printf "import DOMPurify from 'dompurify'\n\n// c\n// c\n// c\n// @vitest-environment jsdom\nexpect(DOMPurify.sanitize('<b>x</b>')).toBe('<b>x</b>')\n" \
    > "$tmp/frontend/src/utils/clean.spec.ts"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a pragma below the imports must pass"
  # It is the FIRST annotation that decides, so a file that names happy-dom and
  # only mentions jsdom further down runs under happy-dom however this guard
  # reads it — "jsdom appears somewhere in here" would clear that file.
  printf "// @vitest-environment happy-dom\nimport DOMPurify from 'dompurify'\n\n// not @vitest-environment jsdom\nexpect(DOMPurify.sanitize('<b>x</b>')).toBe('<b>x</b>')\n" \
    > "$tmp/frontend/src/utils/clean.spec.ts"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a later jsdom must not clear a file whose first pragma is happy-dom"
  # What ends the name is vitest's business and not this file's: it stops at the
  # first character that cannot be in one, so a block comment closing straight
  # after `jsdom` still selects jsdom and must not be reported.
  printf "/* @vitest-environment jsdom*/\nimport DOMPurify from 'dompurify'\n\nexpect(DOMPurify.sanitize('<b>x</b>')).toBe('<b>x</b>')\n" \
    > "$tmp/frontend/src/utils/clean.spec.ts"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a pragma closed by a block comment must pass"
  # And vitest's `\s+` crosses a newline, so the file has to be on one line
  # before the name is read — otherwise a spec vitest runs under jsdom is red.
  printf "/* @vitest-environment\njsdom */\nimport DOMPurify from 'dompurify'\n\nexpect(DOMPurify.sanitize('<b>x</b>')).toBe('<b>x</b>')\n" \
    > "$tmp/frontend/src/utils/clean.spec.ts"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a pragma split across two lines must pass"
  # Either spelling triggers, so a spec that reaches the sanitiser through an
  # alias — and never writes the library's name — is still this guard's business.
  printf "import dp from 'dompurify'\n\nexpect(dp.sanitize('<b>x</b>')).toBe('<b>x</b>')\n" \
    > "$tmp/frontend/src/utils/clean.spec.ts"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "an aliased .sanitize() under the default environment must fail"
  # And a spec that neither imports DOMPurify nor sanitizes is not this guard's
  # business — requiring jsdom everywhere would be switched off within a week.
  printf "import { parse } from './parse'\n\nexpect(parse('<b>x</b>')).toBe('x')\n" \
    > "$tmp/frontend/src/utils/clean.spec.ts"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "an unrelated spec must pass without a pragma"
  rm -rf "$tmp/frontend"

  # Rule 11. The fixture is the split that actually shipped: the image names its
  # scripts one at a time, and docs/ is where a command meant for the box is
  # written down.
  mkdir -p "$tmp/backend/scripts" "$tmp/docs" "$tmp/deploy"
  printf 'FROM python:3.12-slim AS production\nCOPY --chown=cheese:python scripts/alpha_one.py ./scripts/\n' \
    > "$tmp/backend/Dockerfile"
  printf 'python -m scripts.alpha_one --all\n' > "$tmp/docs/ops.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a script the image carries must pass"
  printf 'python3 -m scripts.beta_two --all\n' > "$tmp/docs/ops.md"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "a script the image does not carry must fail"
  # The same command under deploy/, where release steps are written — and docs/
  # is cleared first, or the run below is red on docs/ alone and this assertion
  # would hold whether or not deploy/ is ever read.
  rm "$tmp/docs/ops.md"
  printf 'docker exec backend python -m scripts.beta_two\n' > "$tmp/deploy/release.md"
  bash "$me" "$tmp" >/dev/null 2>&1 && self_fail "the check must read deploy/ too"
  # An image that takes the whole directory names no file to compare against, so
  # the rule stands down — and stands down quietly. The grep behind it matches
  # nothing here, and a status that took the rest of the run with it would be a
  # red build with nothing on it to read.
  printf 'FROM python:3.12-slim AS production\nCOPY --chown=cheese:python scripts/*.py ./scripts/\n' \
    > "$tmp/backend/Dockerfile"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "an image that copies the whole directory must stand down"
  # The other way to say the same thing, and the one where the directory branch
  # is the only thing between a correct document and a red build: the stage does
  # name a file, so there is a set to compare against — and the module the
  # release step names, beta_two, is not in it.
  printf 'FROM python:3.12-slim AS production\nCOPY --chown=cheese:python scripts/alpha_one.py ./scripts/\nCOPY --chown=cheese:python scripts/ ./scripts/\n' \
    > "$tmp/backend/Dockerfile"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "an image that takes the whole directory must stand down even when it also names a file"
  printf 'FROM python:3.12-slim AS production\nCOPY --chown=cheese:python scripts/alpha_one.py ./scripts/\n' \
    > "$tmp/backend/Dockerfile"
  # `uv run` is a checkout command — the source tree is right there — so a
  # dev-only script named that way is not a claim about the image.
  rm "$tmp/deploy/release.md"
  printf 'uv run python -m scripts.beta_two --all\n' > "$tmp/docs/ops.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a uv run command must pass"
  rm "$tmp/docs/ops.md" "$tmp/backend/Dockerfile"
  # No Dockerfile, nothing to compare against — inert, not red.
  printf 'python -m scripts.beta_two\n' > "$tmp/docs/ops.md"
  bash "$me" "$tmp" >/dev/null 2>&1 || self_fail "a tree without the image must pass"
  rm -rf "$tmp/docs" "$tmp/deploy" "$tmp/backend/scripts"

  echo "PASS: check-repo-rules self-test (9 rules and their scoping verified)"
  exit 0
fi

run_all


if [ "$FAILED" = 1 ]; then
  echo ""
  echo "Each rule above is stated where it is enforced; the comment on it says why it exists."
  exit 1
fi
echo "PASS: repo rules (naive datetime, raw HTTPException, supply reverse lookup, fixed-palette colours, platform CLI in AGENTS.md, retired author_type name as a string, room-derived agent handle, DOMPurify specs under jsdom, container scripts present in the image)"
