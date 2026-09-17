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

docker_build_cache() {
  command -v docker >/dev/null 2>&1 || { printf '0'; return 0; }
  local n
  n="$(docker system df --format '{{.Type}}\t{{.Reclaimable}}' 2>/dev/null \
    | awk -F'\t' '$1=="Build Cache"{print $2; exit}' \
    | sed 's/ *(.*//' \
    | numfmt --from=iec 2>/dev/null | head -1)"
  case "$n" in
    ''|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$n" ;;
  esac
}

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
  out="$(docker_build_cache)"
  case "$out" in ''|*[!0-9]*) printf 'self-test FAIL: docker_build_cache -> %q\n' "$out"; exit 1 ;; esac
  out="$(vscode_servers_reclaimable)"
  case "$out" in ''|*[!0-9]*) printf 'self-test FAIL: non-numeric reclaimable -> %s\n' "$out"; exit 1 ;; esac
  printf 'self-test OK\n'
  exit 0
}

test "$SELF_TEST" -eq 1 && self_test

BEFORE_PCT="$(root_pct)"
printf 'root filesystem: %s%% used\n' "$BEFORE_PCT"
if [ "$APPLY" -eq 1 ]; then printf 'reclaiming:\n'; else printf 'reclaimable (dry run — pass --apply to delete):\n'; fi

plan "docker build cache" "$(docker_build_cache)" docker builder prune -f
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
