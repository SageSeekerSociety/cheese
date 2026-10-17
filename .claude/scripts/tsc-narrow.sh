#!/usr/bin/env bash
# tsc-narrow.sh — the frontend type checker, narrowed to the files you name.
#
# WHY: `pnpm run typecheck` (`node scripts/typecheck.mjs`, then vue-tsc over the
# whole tree) needs more memory than a work machine's sandbox has, and its
# ratchet reports only a count per file — `src/lib/x.spec.ts: 0 -> 3` — never
# the text of the error. This builds a throwaway project that inherits
# frontend/tsconfig.json but compiles only the files you name, and prints the
# diagnostics verbatim, one per line, in the `file(line,col): error TSxxxx: …`
# shape. Use it to read what the type gate is actually complaining about, and to
# find the type-only import a refactor dropped (TS2304 "Cannot find name" — a
# name used only in an annotation that eslint, the bundler and every test miss).
#
# Narrow is not always small: the project still follows the target's imports, so
# a file that reaches most of the tree still runs out of memory. That is a real
# limit of this machine, not a pass — the run says so on stderr, and the exit
# code is this script's cannot-judge code.
#
# Usage: tsc-narrow.sh [options] [file ...]
#   file ...          frontend source files (.ts/.tsx/.vue); paths are taken
#                     relative to the current directory
#   (no files)        the frontend/src files this branch changed:
#                     `git diff --name-only origin/main...HEAD`
# Options:
#   --no-generate     do not generate frontend/src/api/openapi.d.ts when missing
#   -h, --help        this text
#
# Exit codes, the repository's cannot-judge convention (see
# `.claude/scripts/check-file-sizes.py`): 0 nothing to report, 1 the targets
# have type errors, 2 this run could not judge — a misuse, a target that does
# not exist, a missing toolchain, a git failure, a target too big for this
# machine to compile, or API types that could not be generated. The diagnostic
# text is printed either way. Exit 1 is a verdict on the targets and nothing
# else; anything that stops the run before the compiler has an opinion is a 2.
#
# frontend/src/api/openapi.d.ts is generated, not committed, and a checkout
# that has never run a type check does not have it — without it every file that
# imports the API layer reports a spurious TS2307. So it is generated here the
# same way `scripts/typecheck.mjs` does it, through the repo's one generator; the
# file is gitignored, and --no-generate skips it. When generation fails — the
# backend's uv and Python environment are not there — this stops with 2 rather
# than compile: every TS2307 below would be about types that were never
# generated, which is not a type error in anyone's code. `scripts/typecheck.mjs`
# stops at the same point for the same reason.
#
# The project lives in a temp directory (mktemp -d, honours TMPDIR) and is
# removed on every exit, success or failure: it is never written into the
# worktree. The `types: [...]` and `@/*` entries of frontend/tsconfig.json
# resolve relative to the config that declares them, so a config in a temp dir
# finds no node_modules and vue-tsc exits 2 without judging anything — the temp
# directory gets a node_modules symlink and absolute baseUrl/paths to fix that.
# Nothing generated here belongs in a commit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND="$REPO_ROOT/frontend"
VUE_TSC="$FRONTEND/node_modules/.bin/vue-tsc"

# The help text is the file header itself, up to the first line of code — a
# range of line numbers would silently cut it in half the next time a paragraph
# is added above.
usage() {
  awk 'NR < 3 { next } /^set -/ { exit } { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
}

GENERATE=1
files=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --no-generate) GENERATE=0; shift ;;
    --) shift; while [ $# -gt 0 ]; do files+=("$1"); shift; done ;;
    -*) echo "tsc-narrow.sh: unknown option: $1" >&2; exit 2 ;;
    *) files+=("$1"); shift ;;
  esac
done

# No files named: take the branch's changed frontend sources. Anything not under
# frontend/src, or not a type-checked source kind, is out of scope for this
# check by construction.
changed=()
if [ "${#files[@]}" -eq 0 ]; then
  if ! git -C "$REPO_ROOT" rev-parse --verify --quiet origin/main >/dev/null; then
    echo "tsc-narrow.sh: origin/main is not present; name the files to check," >&2
    echo "              or fetch first (the default file set comes from origin/main...HEAD)." >&2
    exit 2
  fi
  # Read the diff's exit code from a file rather than a process substitution:
  # `set -e` does not reach a command run by one, and a git that could not
  # compute the diff must not read as "this branch changed nothing". The shape
  # is a shallow or unrelated-history checkout, and a guard that never ran is
  # not a guard that passed — the same condition check-file-sizes.py exits 2 on.
  # `if ! x="$(cmd)"` rather than a bare assignment: a bare one that fails takes
  # the script out under `set -e` with mktemp's own status, which is 1 — the
  # "targets have type errors" this script reserves for a real verdict. A
  # TMPDIR that cannot be written is a run that judged nothing, so it exits 2.
  if ! diffout="$(mktemp)"; then
    echo "tsc-narrow.sh: mktemp failed (TMPDIR=${TMPDIR:-/tmp} not writable?);" >&2
    echo "              nothing was judged." >&2
    exit 2
  fi
  if ! git -C "$REPO_ROOT" diff --name-only --no-renames origin/main...HEAD >"$diffout"; then
    rm -f "$diffout"
    echo "tsc-narrow.sh: \`git diff origin/main...HEAD\` failed; nothing was judged." >&2
    echo "              The usual cause is no merge base with origin/main (shallow" >&2
    echo "              or unrelated history). Fetch, or name the files yourself." >&2
    exit 2
  fi
  while IFS= read -r changed; do
    case "$changed" in
      frontend/src/*.ts|frontend/src/*.tsx|frontend/src/*.vue) files+=("$REPO_ROOT/$changed") ;;
    esac
  done <"$diffout"
  rm -f "$diffout"
fi

# A path the branch deleted is in that list but has nothing to check — a rename
# shows up as the old path deleted plus the new one added. Named files are the
# caller's to get wrong and are refused; discovered ones are the branch's own
# state, and a deletion in the diff has no diagnostic to report.
if [ "${#files[@]}" -eq 0 ]; then
  echo "tsc-narrow.sh: no frontend sources to check." >&2
  exit 0
fi
kept=()
for f in "${files[@]}"; do
  case "$f" in
    *.ts|*.tsx|*.vue) ;;
    *) echo "tsc-narrow.sh: not a TypeScript source file: $f" >&2; exit 2 ;;
  esac
  if [ ! -f "$f" ]; then
    if [ "${#changed[@]}" -gt 0 ]; then
      continue
    fi
    echo "tsc-narrow.sh: no such file: $f" >&2
    exit 2
  fi
  kept+=("$f")
done
if [ "${#kept[@]}" -eq 0 ]; then
  echo "tsc-narrow.sh: no frontend sources to check." >&2
  exit 0
fi

# Absolute paths, so the config and the report do not depend on this shell's
# working directory. NUL-separated: a source file whose name contains a newline
# would otherwise arrive as two targets, and both would be compiled as paths
# that do not exist.
mapfile -t -d '' targets < <(python3 -c 'import os,sys; sys.stdout.write("\0".join(os.path.abspath(p) for p in sys.argv[1:] if p))' "${kept[@]}")
# `set -e` does not reach a command inside a process substitution, so a python3
# that failed leaves this empty and the run would compile nothing at all — a
# pass for a run that never resolved its own arguments.
if [ "${#targets[@]}" -eq 0 ]; then
  echo "tsc-narrow.sh: could not resolve the target paths; nothing was judged." >&2
  exit 2
fi

if [ ! -x "$VUE_TSC" ]; then
  echo "tsc-narrow.sh: vue-tsc is not installed at $VUE_TSC" >&2
  echo "              run \`pnpm install --frozen-lockfile\` in frontend/ first." >&2
  exit 2
fi

echo "tsc-narrow.sh: checking ${#targets[@]} file(s):" >&2
printf '  %s\n' "${targets[@]#"$REPO_ROOT"/}" >&2

if ! tmp="$(mktemp -d)"; then
  echo "tsc-narrow.sh: mktemp -d failed (TMPDIR=${TMPDIR:-/tmp} not writable?);" >&2
  echo "              nothing was judged." >&2
  exit 2
fi
trap 'rm -rf "$tmp"' EXIT

# The symlink is what lets `types: ["vitest/globals", "vue/jsx", "vuetify"]` and
# the `@/*` paths of frontend/tsconfig.json resolve from the temp config.
if ! ln -s "$FRONTEND/node_modules" "$tmp/node_modules"; then
  echo "tsc-narrow.sh: could not link frontend/node_modules into $tmp;" >&2
  echo "              nothing was judged." >&2
  exit 2
fi

# Guarded like the rest: a bare command here runs under `set -e`, and its own
# status 1 would leave the script as "the targets have type errors" with no
# message — a verdict on targets the compiler never saw.
if ! python3 - "$tmp/tsconfig.json" "$FRONTEND" "${targets[@]}" <<'PY'
import json, sys

out, frontend = sys.argv[1], sys.argv[2]
config = {
    # Inherit the real compiler options, then override what a narrow project
    # must: only the target files, plus the ambient declarations the whole
    # frontend compiles against (env.d.ts carries ImportMeta.env; src/**/*.d.ts
    # the rest). Omitting those turns them into a wall of false errors.
    "extends": f"{frontend}/tsconfig.json",
    "files": sys.argv[3:],
    "include": [f"{frontend}/env.d.ts", f"{frontend}/src/**/*.d.ts"],
    "exclude": [],
    "references": [],
    "compilerOptions": {
        "noEmit": True,
        "baseUrl": frontend,
        "paths": {"@/*": ["src/*"]},
    },
}
with open(out, "w") as handle:
    json.dump(config, handle, indent=2)
PY
then
  echo "tsc-narrow.sh: could not write the throwaway tsconfig in $tmp;" >&2
  echo "              nothing was judged." >&2
  exit 2
fi

# The API types are generated, not committed: a fresh checkout lacks them, and
# without them every target that imports the API layer reports TS2307 for
# `../api/openapi` — noise that hides the errors this run exists to show.
if [ "$GENERATE" = 1 ] && [ ! -f "$FRONTEND/src/api/openapi.d.ts" ]; then
  echo "tsc-narrow.sh: generating frontend/src/api/openapi.d.ts (gitignored)…" >&2
  if ! (cd "$FRONTEND" && node scripts/gen-api-types.mjs >&2); then
    # Not a warning to compile past: without the file every target that imports
    # src/api reports TS2307 about types that were never generated, and those
    # would arrive as exit 1 — a verdict on code that was never read. This is
    # the missing-toolchain cannot-judge, the same stop scripts/typecheck.mjs
    # makes.
    echo "tsc-narrow.sh: the API types could not be generated — the backend" >&2
    echo "              toolchain (uv and the app's Python environment) is not" >&2
    echo "              available, so src/api has no types to check against." >&2
    echo "              Nothing was judged; run \`pnpm install --frozen-lockfile\`" >&2
    echo "              in frontend/ and make the backend toolchain available," >&2
    echo "              or pass --no-generate and judge non-API targets only." >&2
    exit 2
  fi
fi

cd "$FRONTEND"
set +e
"$VUE_TSC" --noEmit --pretty false -p "$tmp/tsconfig.json"
status=$?
set -e

# vue-tsc 5.9 answers 2 for a run that produced diagnostics and 1 for a config
# it could not read, so its codes are not this script's: map diagnostics to 1
# and everything else to the cannot-judge 2, and never let a killed run read
# like a pass.
if [ "$status" -eq 0 ]; then
  exit 0
fi
if [ "$status" -ge 128 ]; then
  echo "tsc-narrow.sh: vue-tsc was killed (exit $status) — the target pulls in" >&2
  echo "              more of the tree than this machine can type-check." >&2
  exit 2
fi
if [ "$status" -eq 1 ]; then
  echo "tsc-narrow.sh: vue-tsc exited 1 without diagnostics; the project could" >&2
  echo "              not be read. This is not a verdict on the targets." >&2
  exit 2
fi
exit 1
