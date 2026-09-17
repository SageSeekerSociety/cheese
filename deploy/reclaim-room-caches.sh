#!/usr/bin/env bash
# Reclaim the package caches a room kept before it shared its project's store.
#
# Every room of a project now installs out of one store (`~/.cheese/store/<p>`),
# so the copies a room made under its own HOME are read by nothing. The launcher
# removes them the next time that room starts — but a room only starts when
# someone uses it, and the rooms holding the most disk are precisely the ones
# nobody is using. Measured on dev 2026-09-17: of 102 checkouts, 3 had been
# touched in seven days. So the self-healing path reaches the rooms with the
# least to give back, and this is how the rest are reached.
#
# What comes back is not uniform, measured the same day:
#
#   ~/.npm/_cacache   792MiB   100% reclaimed
#   ~/.cache/pip      240MiB   100% reclaimed
#   ~/.cache/uv       1.5GiB    12% reclaimed
#   pnpm store        3.0GiB     2% reclaimed
#
# The first two are download caches nothing links to, so every byte comes back.
# uv's and pnpm's are content-addressed stores that an install HARDLINKS out of:
# most of their bytes are also in a `.venv`/`node_modules` that stays, so
# deleting them drops a link count and frees nothing. For a room whose checkout
# is already gone there is no venv holding anything, and all of it comes back.
#
# What this does NOT touch, and why each one would be a bug:
#
#   .local/share/uv   the managed INTERPRETER. A venv reaches it by absolute
#                     symlink and `pyvenv.cfg`'s `home =` names it; removing it
#                     strands every venv in the room until a plain `uv run`.
#   .claude           transcripts. Deleting a room's conversation is archival's
#                     job, and archival verifies the backend holds it first
#                     (`agent/resource_cleanup.py`). Never here.
#   .cheese           the platform's own state: this room's hook spool, its
#                     drain credential, its tmux session pointer.
#
# Unlike `dev-box-disk-cleanup.sh`, which reclaims whatever a box regenerates
# and coordinates with nothing, every path here has a writer: the project's
# setup script, run under a flock by `agent/environment_runner.py`. So this
# takes that same lock per room and skips a room that is installing right now —
# deleting a cache mid-install is how you get a half-linked venv.
#
# Reports by default. Pass --apply to actually delete.

set -euo pipefail

APPLY=0
SELF_TEST=0
ROOT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --self-test) SELF_TEST=1 ;;
    --root) shift; ROOT="${1:-}" ;;
    --root=*) ROOT="${1#--root=}" ;;
    -h|--help) sed -n '2,44p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

# The device home root as the connector expands it. A machine that hosts rooms
# runs the connector as its owner, so this is that owner's home — which is NOT
# necessarily the user a deploy runs as. The resolved path is printed below
# rather than assumed, so a wrong guess shows up in the deploy log as "0 rooms"
# next to the directory it looked in, instead of as silence.
: "${CHEESE_DEVICE_HOME:=$HOME/.cheese}"
test -n "$ROOT" || ROOT="$CHEESE_DEVICE_HOME/home"

# Relative to one room's home. Kept in step with `agent/machine_launcher.py`,
# which removes this same list at launch.
ROOM_CACHES=(
  ".cache/uv"
  ".cache/pip"
  ".npm/_cacache"
  ".local/share/pnpm/store"
  "Library/Caches/uv"
  "Library/Caches/pip"
  "Library/pnpm/store"
)

human() { numfmt --to=iec --suffix=B "${1:-0}" 2>/dev/null || printf '%sB' "${1:-0}"; }

# Size of a path in bytes, 0 when absent or unreadable. Every caller feeds this
# into $(( )), so it MUST emit exactly one integer and nothing else — the same
# trap `dev-box-disk-cleanup.sh` documents, where a root-owned directory made
# `du` emit a partial line and turned $(( )) into a syntax error.
size_of() {
  test -e "$1" || { printf '0'; return 0; }
  local n
  n="$(du -sxb "$1" 2>/dev/null | head -1 | cut -f1)"
  case "$n" in
    ''|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$n" ;;
  esac
}

# Is this room installing right now? The answer is the room's own lock, the one
# `environment_runner.run()` takes before it runs a line of the setup script.
# Taking it here is not just a probe: holding it for the deletion is what stops
# a setup from starting halfway through.
#
# No lock file means no setup has ever run in this room, so there is nothing to
# race. A machine without flock(1) — a Mac — is reported and skipped rather than
# swept unlocked, because "probably idle" is not a basis for rm -rf.
HAVE_FLOCK=0
command -v flock >/dev/null 2>&1 && HAVE_FLOCK=1

reclaim_room() { # room_dir -> prints reclaimed bytes, or "busy" / "nolock"
  local room="$1" lock="$1/.cheese-environment/lock" bytes=0 path
  for path in "${ROOM_CACHES[@]}"; do
    bytes=$((bytes + $(size_of "$room/$path")))
  done
  if [ "$bytes" -eq 0 ]; then printf '0'; return 0; fi
  if [ "$APPLY" -eq 0 ]; then printf '%s' "$bytes"; return 0; fi
  if [ -f "$lock" ]; then
    if [ "$HAVE_FLOCK" -eq 0 ]; then printf 'nolock'; return 0; fi
    (
      exec 9<"$lock"
      flock -n 9 || exit 75
      for path in "${ROOM_CACHES[@]}"; do
        rm -rf -- "${room:?}/$path"
      done
    ) || { printf 'busy'; return 0; }
  else
    for path in "${ROOM_CACHES[@]}"; do
      rm -rf -- "${room:?}/$path"
    done
  fi
  printf '%s' "$bytes"
}

# A room whose checkout is gone can give back everything, not the fraction above
# — but its interpreter, its node install and its transcripts are all still
# here, and only archival may remove those. Counting them is how that list gets
# made; `deploy/README-room-cleanup.md` is what acts on it.
has_checkout() { # room_dir project resource
  # Two shapes, because two exist: `DeviceChannel._work_dir` puts a room's cwd
  # at `<room home>/room`, while `device_work_dir` names `~/.cheese/work/<p>/<r>`.
  test -d "$1/room" && return 0
  test -d "$CHEESE_DEVICE_HOME/work/$2/$3" && return 0
  return 1
}

run_sweep() {
  local rooms=0 swept=0 busy=0 nolock=0 orphans=0 total=0 result
  local project resource room
  if [ ! -d "$ROOT" ]; then
    printf 'no room storage at %s — nothing to reclaim\n' "$ROOT"
    return 0
  fi
  if [ "$APPLY" -eq 1 ]; then
    printf 'reclaiming room caches under %s\n' "$ROOT"
  else
    printf 'reclaimable room caches under %s (dry run — pass --apply):\n' "$ROOT"
  fi
  for room in "$ROOT"/*/*; do
    test -d "$room" || continue
    resource="$(basename "$room")"
    project="$(basename "$(dirname "$room")")"
    rooms=$((rooms + 1))
    has_checkout "$room" "$project" "$resource" || orphans=$((orphans + 1))
    result="$(reclaim_room "$room")"
    case "$result" in
      busy) busy=$((busy + 1)) ;;
      nolock) nolock=$((nolock + 1)) ;;
      0) ;;
      *) swept=$((swept + 1)); total=$((total + result)) ;;
    esac
  done
  local verb=reclaimable
  test "$APPLY" -eq 0 || verb=reclaimed
  printf '  %s rooms, %s with caches to drop, %s %s\n' \
    "$rooms" "$swept" "$(human "$total")" "$verb"
  test "$busy" -eq 0 || printf '  %s skipped: installing right now\n' "$busy"
  test "$nolock" -eq 0 || printf '  %s skipped: no flock(1) on this machine\n' "$nolock"
  # Not actionable by this script, deliberately — printed because it is the
  # input to the one mechanism that CAN reclaim a whole room.
  test "$orphans" -eq 0 || printf \
    '  %s rooms have no checkout — archival reclaims those in full (deploy/README-room-cleanup.md)\n' \
    "$orphans"
}

self_test() {
  local tmp room out
  tmp="$(mktemp -d)"
  trap 'rm -rf -- "$tmp"' EXIT

  # size_of must not abort the script on a missing path — the failure mode the
  # sibling script documents, where a guarded command under `set -e` took the
  # whole run down.
  out="$(size_of /definitely/not/here)"
  test "$out" = "0" || { printf 'self-test FAIL: size_of missing -> %s\n' "$out"; exit 1; }
  for probe in / "$HOME" /var/log; do
    out="$(size_of "$probe")"
    case "$out" in
      ''|*[!0-9]*) printf 'self-test FAIL: size_of %s -> %q\n' "$probe" "$out"; exit 1 ;;
    esac
    test "$(printf '%s' "$out" | wc -l)" -eq 0 \
      || { printf 'self-test FAIL: size_of %s emitted multiple lines\n' "$probe"; exit 1; }
  done

  # An idle room gives its caches back and keeps everything else.
  room="$tmp/home/proj/idle"
  mkdir -p "$room/.cache/uv" "$room/.cache/pip" "$room/.npm/_cacache" \
    "$room/.local/share/pnpm/store" "$room/.local/share/uv/python" \
    "$room/.claude/projects" "$room/.cheese" "$room/.cheese-environment"
  for f in .cache/uv/a .cache/pip/a .npm/_cacache/a .local/share/pnpm/store/a \
    .local/share/uv/python/keep .claude/projects/t.jsonl .cheese/cheese-drain.env; do
    printf 'x' > "$room/$f"
  done
  : > "$room/.cheese-environment/lock"

  # A room mid-install keeps everything, because a cache is only garbage once
  # nothing is writing to it.
  local busy="$tmp/home/proj/busy"
  mkdir -p "$busy/.cache/uv" "$busy/.cheese-environment"
  printf 'x' > "$busy/.cache/uv/a"
  : > "$busy/.cheese-environment/lock"
  command -v flock >/dev/null 2>&1 || {
    printf 'self-test SKIP: no flock(1), cannot test the busy-room guard\n'
    exit 0
  }
  flock -n "$busy/.cheese-environment/lock" -c 'sleep 30' &
  local holder=$!
  # Wait for the child to actually hold it; testing against a lock nobody has
  # yet would pass for the wrong reason.
  local waited=0
  while flock -n "$busy/.cheese-environment/lock" -c true 2>/dev/null; do
    sleep 0.1
    waited=$((waited + 1))
    test "$waited" -lt 50 || { printf 'self-test FAIL: lock never taken\n'; exit 1; }
  done

  ROOT="$tmp/home" CHEESE_DEVICE_HOME="$tmp" APPLY=1
  out="$(run_sweep)"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true

  for gone in .cache/uv .cache/pip .npm/_cacache .local/share/pnpm/store; do
    test ! -e "$room/$gone" || { printf 'self-test FAIL: kept %s\n' "$gone"; exit 1; }
  done
  for kept in .local/share/uv/python/keep .claude/projects/t.jsonl \
    .cheese/cheese-drain.env; do
    test -e "$room/$kept" || { printf 'self-test FAIL: removed %s\n' "$kept"; exit 1; }
  done
  test -e "$busy/.cache/uv/a" \
    || { printf 'self-test FAIL: swept a room that was installing\n'; exit 1; }
  case "$out" in
    *"installing right now"*) ;;
    *) printf 'self-test FAIL: busy room not reported\n%s\n' "$out"; exit 1 ;;
  esac
  case "$out" in
    *"no checkout"*) ;;
    *) printf 'self-test FAIL: orphan rooms not reported\n%s\n' "$out"; exit 1 ;;
  esac
  printf 'self-test OK\n'
  exit 0
}

test "$SELF_TEST" -eq 1 && self_test
run_sweep
