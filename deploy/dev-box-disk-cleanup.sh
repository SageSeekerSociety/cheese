#!/usr/bin/env bash
# Routine reclaim of REGENERABLE caches on a dev/agent box.
#
# Why this exists next to cheesex-disk-pressure-guard.sh rather than inside it:
# the guard is an EMERGENCY brake — it waits for 85%, checks that no turn is
# active, then removes sandbox containers. It never touches a build cache, so on
# a box whose space is in caches it brakes against something it cannot reclaim.
# This script reclaims the caches, and `cheese-disk-cleanup.timer`
# (deploy/install-disk-cleanup-timer.sh) runs it nightly above 75%, which is
# under the guard's 85% on purpose.
#
# Measured on cheese-dev-env6-app, 2026-08-11, at 92% full (2.6G free):
#   docker build cache   3.0G   71 entries, 0 in use
#   /var/cache/apt       1.7G   downloaded .deb archives
#   ~/.cache/go-build    1.3G
#   ~/.vscode-server     2.0G   superseded server versions + VSIX cache
#   ~/.cache/puccinialin 665M   rust toolchain downloads for python builds
# Reclaiming exactly these took it to 70% (9.1G free). Nothing else was touched.
#
# The rule this script encodes: only delete what a command can rebuild. A slower
# next build is an acceptable price; someone else's data is not. Concretely it
# never touches container images or volumes that a running container uses, never
# touches ~/.cache/ms-playwright (browser binaries the e2e suite needs and pip
# will not refetch on demand), and never touches anything under a project dir.
#
# Reports by default. Pass --apply to actually delete.

set -euo pipefail

APPLY=0
SELF_TEST=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --self-test) SELF_TEST=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

human() { numfmt --to=iec --suffix=B "${1:-0}" 2>/dev/null || printf '%sB' "${1:-0}"; }

# Size of a path in bytes, 0 when absent or unreadable. Every caller feeds this
# into $(( )), so it MUST emit exactly one integer and nothing else — a root-owned
# directory makes `du` emit a partial line plus nothing on stdout, and the two
# lines that came back from /var/cache/apt/archives turned $(( )) into a syntax
# error. Hence: one line, validated numeric, 0 otherwise.
size_of() {
  test -e "$1" || { printf '0'; return 0; }
  local n
  n="$(du -sxb "$1" 2>/dev/null | head -1 | cut -f1)"
  case "$n" in
    ''|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$n" ;;
  esac
}

root_pct() { df --output=pcent / | tail -1 | tr -dc '0-9'; }

TOTAL=0
plan() { # name, bytes, command...
  local name="$1" bytes="$2"; shift 2
  TOTAL=$((TOTAL + bytes))
  printf '  %-28s %8s\n' "$name" "$(human "$bytes")"
  if [ "$APPLY" -eq 1 ] && [ "$bytes" -gt 0 ]; then
    "$@" >/dev/null 2>&1 || printf '    (failed, skipped)\n'
  fi
}

# Old vscode-server payloads: keep the newest server build, drop superseded ones.
# Editors redownload on next connect; keeping the newest avoids doing that today.
prune_vscode_servers() {
  local dir="$HOME/.vscode-server/cli/servers"
  test -d "$dir" || return 0
  local keep
  keep="$(ls -1t "$dir" 2>/dev/null | head -1 || true)"
  test -n "$keep" || return 0
  find "$dir" -mindepth 1 -maxdepth 1 ! -name "$keep" -exec rm -rf -- {} + 2>/dev/null || true
}

vscode_servers_reclaimable() {
  local dir="$HOME/.vscode-server/cli/servers" total keep
  test -d "$dir" || { printf '0'; return 0; }
  total="$(size_of "$dir")"
  keep="$(ls -1t "$dir" 2>/dev/null | head -1 || true)"
  test -n "$keep" || { printf '%s' "$total"; return 0; }
  printf '%s' "$((total - $(size_of "$dir/$keep")))"
}

# Docker prints sizes as `5.228GB` / `344.1kB` — an SI number with a trailing
# `B`. `numfmt --from=iec` rejects that ("invalid suffix ... 'B'"), and the
# guard below turns the failure into 0, so every size read this way came back
# as nothing and the prune beside it was skipped on every box, silently.
docker_bytes() {
  local s="${1:-}" n
  s="${s%% *}"          # "5.228GB (0%)" -> "5.228GB"
  s="${s%B}"            # -> "5.228G", which --from=auto reads (and "Gi" too)
  n="$(printf '%s' "$s" | numfmt --from=auto 2>/dev/null | head -1)"
  case "$n" in
    ''|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$n" ;;
  esac
}

# `docker system df` is not cheap — 33s on the box that needed this script, and
# it is asked for two different rows. Read it ONCE, in the parent shell: each
# `plan` argument runs in a subshell, so a cache filled lazily inside one would
# be thrown away and the call repeated.
DOCKER_DF=""
docker_df_load() {
  command -v docker >/dev/null 2>&1 || return 0
  DOCKER_DF="$(docker system df --format '{{.Type}}\t{{.Reclaimable}}' 2>/dev/null)"
}

docker_df_reclaimable() {
  [ -n "$DOCKER_DF" ] || { printf '0'; return 0; }
  docker_bytes "$(printf '%s\n' "$DOCKER_DF" | awk -F'\t' -v want="$1" '$1==want{print $2; exit}')"
}

docker_build_cache() { docker_df_reclaimable "Build Cache"; }

# Anonymous volumes with no container referencing them — what a finished test
# run's postgres service container leaves behind, one per run. `docker volume
# prune` without `--all` takes exactly these and leaves named volumes alone, so
# a co-tenant's stopped work is not at risk: a stopped container still counts as
# referencing its volume.
# Unused local volumes. The number is `docker system df`'s aggregate, which is
# an UPPER BOUND on what gets freed: `docker volume prune` without `--all` takes
# only the anonymous ones, and a named volume nothing currently mounts stays.
# That is the intended split — an anonymous volume is what a finished test run's
# postgres service container leaves behind, one per run, and it is regenerable
# by definition; a named one is somebody's data. A stopped container still
# counts as referencing its volume, so a co-tenant's paused work is not at risk.
#
# Deliberately NOT sized with `docker system df -v`, which walks every volume:
# on the box that needed this (718 volumes) that call alone ran over five
# minutes, and a cleanup that costs more than it reclaims will not be run.
docker_unused_volumes() { docker_df_reclaimable "Local Volumes"; }

self_test() {
  # The one thing worth asserting: size_of must not abort the script on a
  # missing path. That was the whole failure mode of an earlier `set -e` bug in
  # check.sh — a guarded command in a branch, silently taking the script down.
  local out
  out="$(size_of /definitely/not/here)"
  test "$out" = "0" || { printf 'self-test FAIL: size_of on missing path -> %s\n' "$out"; exit 1; }
  # Every size feeds $(( )), so one integer and nothing else. A root-owned tree
  # is the case that actually broke this: it returned two lines and $(( )) died.
  for probe in /var/cache/apt/archives /var/log / "$HOME/.cache"; do
    out="$(size_of "$probe")"
    case "$out" in
      ''|*[!0-9]*) printf 'self-test FAIL: size_of %s -> %q\n' "$probe" "$out"; exit 1 ;;
    esac
    test "$(printf '%s' "$out" | wc -l)" -eq 0 \
      || { printf 'self-test FAIL: size_of %s emitted multiple lines\n' "$probe"; exit 1; }
  done
  # Assert the PARSE, not just that a number came out: the bug this replaces
  # returned a perfectly numeric 0 from a failed conversion, so a self-test that
  # only checked for digits passed while the reclaim never ran.
  for probe in '5.228GB=5228000000' '344.1kB=344100' '15.98GB=15980000000' \
               '2.5GiB=2684354560' '0B=0'; do
    got="$(docker_bytes "${probe%%=*}")"
    [ "$got" = "${probe#*=}" ] \
      || { printf 'self-test FAIL: docker_bytes %s -> %s, want %s\n' \
             "${probe%%=*}" "$got" "${probe#*=}"; exit 1; }
  done
  # Not calling docker here: the parse is what broke, and it is covered above.
  DOCKER_DF="$(printf 'Images\t0B (0%%)\nBuild Cache\t5.228GB\nLocal Volumes\t15.98GB (62%%)\n')"
  out="$(docker_build_cache)"
  [ "$out" = 5228000000 ] \
    || { printf 'self-test FAIL: build cache row -> %s, want 5228000000\n' "$out"; exit 1; }
  out="$(docker_unused_volumes)"
  [ "$out" = 15980000000 ] \
    || { printf 'self-test FAIL: volumes row -> %s, want 15980000000\n' "$out"; exit 1; }
  DOCKER_DF=""
  out="$(vscode_servers_reclaimable)"
  case "$out" in ''|*[!0-9]*) printf 'self-test FAIL: non-numeric reclaimable -> %s\n' "$out"; exit 1 ;; esac
  printf 'self-test OK\n'
  exit 0
}

test "$SELF_TEST" -eq 1 && self_test

docker_df_load
BEFORE_PCT="$(root_pct)"
printf 'root filesystem: %s%% used\n' "$BEFORE_PCT"
if [ "$APPLY" -eq 1 ]; then printf 'reclaiming:\n'; else printf 'reclaimable (dry run — pass --apply to delete):\n'; fi

plan "docker build cache" "$(docker_build_cache)" docker builder prune -f
plan "docker unused volumes" "$(docker_unused_volumes)" docker volume prune -f
plan "apt archives" "$(size_of /var/cache/apt/archives)" sudo apt-get clean
plan "go build cache" "$(size_of "$HOME/.cache/go-build")" go clean -cache
plan "rust toolchain downloads" "$(size_of "$HOME/.cache/puccinialin")" rm -rf "$HOME/.cache/puccinialin"
plan "pip cache" "$(size_of "$HOME/.cache/pip")" rm -rf "$HOME/.cache/pip"
plan "vscode VSIX cache" "$(size_of "$HOME/.vscode-server/data/CachedExtensionVSIXs")" \
  rm -rf "$HOME/.vscode-server/data/CachedExtensionVSIXs"
plan "vscode old servers" "$(vscode_servers_reclaimable)" prune_vscode_servers

printf '  %-28s %8s\n' "TOTAL" "$(human "$TOTAL")"

if [ "$APPLY" -eq 1 ]; then
  # Journals and the uv cache are pruned rather than deleted: both keep entries
  # that are still referenced, and both have a first-class subcommand for it.
  journalctl --vacuum-size=32M >/dev/null 2>&1 || sudo journalctl --vacuum-size=32M >/dev/null 2>&1 || true
  command -v uv >/dev/null 2>&1 && uv cache prune >/dev/null 2>&1 || true
  printf 'root filesystem: %s%% used -> %s%% used\n' "$BEFORE_PCT" "$(root_pct)"
fi
