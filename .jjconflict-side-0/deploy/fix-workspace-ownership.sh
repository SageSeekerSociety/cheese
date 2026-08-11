#!/usr/bin/env bash
# One-time ownership migration for the backend's host bind mounts.
#
# WHY: the backend process and the in-container agent share one jj store (the
# project's main-repo .jj/.git is bind-mounted into every sandbox container,
# read-write). jj writes its store objects — `.jj/repo/config-id` above all —
# with a hardcoded 0600, so a uid split means whichever side writes first locks
# the other out of EVERY jj command. Backend-side that showed up as a
# project-wide 422 on the file panel; sandbox-side as jj being unusable in the
# container. umask/group/ACL cannot widen a mode the writer sets explicitly, so
# the backend image now runs as the SAME uid as the sandbox's `node` (1000,
# see backend/Dockerfile). Files written by the old uid (1001) must be handed
# over once, or the new backend cannot read its own history.
#
# Idempotent and safe to re-run: it only touches entries whose uid is wrong, and
# drops a marker so a second deploy skips the walk entirely. Runs the chown in a
# throwaway root container rather than requiring sudo on the box — the deploy
# path already has docker and nothing else.
#
# Usage: fix-workspace-ownership.sh <image> <host-path> [<host-path>...]
#   image:      any image present on the box (the backend image is fine)
#   host-path:  a bind-mount source to hand over (workspaces, uploads)
#
# Env:
#   AGENT_UID / AGENT_GID   target ownership (default 1000, must match
#                           app.domain.workspace.service.AGENT_UID)
#   FORCE_OWNERSHIP_FIX     set to 1 to ignore the marker and re-walk
#   SECRET_FILE_PATHS       space-separated single FILES that are mounted into
#                           the backend and must be readable by it — e.g. the
#                           git credential store. Handed over like everything
#                           else (mode untouched), then re-checked.
#   OWNERSHIP_REPORT_FILE   if set, `migrated` or `noop` is written here so the
#                           caller can tell whether this run actually moved
#                           files — a rollback must only hand them back if it did.
set -euo pipefail

AGENT_UID="${AGENT_UID:-1000}"
AGENT_GID="${AGENT_GID:-1000}"
MARKER=".cheese-uid-$AGENT_UID"
MIGRATED=no

log() { echo "[fix-ownership $(date '+%H:%M:%S')] $*"; }

report() {
  [ -n "${OWNERSHIP_REPORT_FILE:-}" ] || return 0
  printf '%s\n' "$1" > "$OWNERSHIP_REPORT_FILE"
}
# Written on every exit path, not just the happy one: a handover that dies
# half-way still moved files, and a rollback that skipped the hand-back because
# the report was missing would leave the old backend locked out of its own data.
trap 'report "$MIGRATED"' EXIT

IMAGE="${1:?usage: fix-workspace-ownership.sh <image> <host-path>...}"
shift
[ "$#" -gt 0 ] || { echo "ERROR: at least one host path is required" >&2; exit 1; }

for path in "$@"; do
  if [ ! -d "$path" ]; then
    log "skip $path (not a directory on this box)"
    continue
  fi
  if [ -e "$path/$MARKER" ] && [ "${FORCE_OWNERSHIP_FIX:-0}" != 1 ]; then
    log "skip $path (already migrated to uid $AGENT_UID)"
    continue
  fi
  log "handing $path over to $AGENT_UID:$AGENT_GID…"
  # From here the path may be half-moved, so the caller is owed the truth even
  # if the next command dies.
  MIGRATED=yes
  # `! -uid` keeps a re-run cheap (nothing to write once it is correct) and
  # keeps the walk honest about what it changed. -print is deliberate: an
  # operator reading the deploy log should see the size of the handover.
  #
  # Every OTHER uid's marker is dropped as part of the move. A marker is a claim
  # about who owns the tree, and after this walk exactly one uid does; leaving a
  # stale `.cheese-uid-1000` behind after handing the tree back to 1001 would
  # make the next deploy skip the walk and start a 1000 backend on 1001 files —
  # the same lockout this script exists to prevent, but now invisible.
  docker run --rm --user 0 --entrypoint sh \
    -v "$path:/target" "$IMAGE" -c "
      set -eu
      changed=\$(find /target \\( ! -uid $AGENT_UID -o ! -gid $AGENT_GID \\) -print | wc -l)
      find /target \\( ! -uid $AGENT_UID -o ! -gid $AGENT_GID \\) \
        -exec chown -h $AGENT_UID:$AGENT_GID {} +
      find /target -maxdepth 1 -name '.cheese-uid-*' ! -name '$MARKER' -exec rm -f {} +
      : > /target/$MARKER
      chown $AGENT_UID:$AGENT_GID /target/$MARKER
      echo \"\$changed entries re-owned\"
    " || { log "ERROR: ownership handover failed for $path"; exit 1; }
done

# Secret files ARE handed over too — this reverses the first version of this
# script, which only checked them.
#
# The reasoning then was "chowning somebody's private credential file out from
# under them is not this script's call". That was wrong about what the file is.
# It is mounted read-only into the backend at a fixed path and chmod 600, so its
# owner must BE the backend's uid — it was 1001 only because the backend was
# 1001. Refusing to move it protected nothing and left the deploy stopped on a
# step whose only remedy was a sudo nobody in the deploy path has: on 2026-08-11
# it aborted deploy-dev after the trees had already changed hands, which is the
# exact half-migrated state the ownership walk exists to avoid (run 31466502982).
#
# The mode is deliberately left alone — 600 before, 600 after; only the owner
# moves. The readability check stays and still fails the deploy loudly, but now
# it runs AFTER the handover, so it only fires on something a chown cannot fix
# (a mount option, an unreadable parent). Silently losing private-repo push to
# the "git prompts fail cleanly" fallback is still the failure being stopped.
for path in ${SECRET_FILE_PATHS:-}; do
  [ -e "$path" ] || { log "skip $path (absent)"; continue; }
  # `/dev/null` is the "feature off" default and every other non-regular file is
  # somebody else's device node: never take ownership of one.
  if [ ! -f "$path" ]; then
    log "skip $path (not a regular file — nothing to hand over)"
    continue
  fi
  if [ "$(stat -c '%u:%g' "$path" 2>/dev/null || echo unknown)" = "$AGENT_UID:$AGENT_GID" ]; then
    log "skip $path (already owned by $AGENT_UID:$AGENT_GID)"
  else
    log "handing $path over to $AGENT_UID:$AGENT_GID (mode unchanged)…"
    # Best-effort: the readability check below is the gate, so a chown that
    # cannot land reports as the thing the operator actually cares about.
    docker run --rm --user 0 --entrypoint sh \
      -v "$path:/secret" "$IMAGE" -c "chown $AGENT_UID:$AGENT_GID /secret" \
      >/dev/null 2>&1 \
      || log "WARNING: could not hand $path over — the check below decides"
  fi
  if docker run --rm --user "$AGENT_UID:$AGENT_GID" --entrypoint sh \
      -v "$path:/probe:ro" "$IMAGE" -c 'head -c 1 /probe >/dev/null 2>&1 || test ! -s /probe' \
      >/dev/null 2>&1; then
    log "ok: $path is readable as $AGENT_UID"
  else
    log "ERROR: $path is still not readable as uid $AGENT_UID after the handover —"
    log "       the backend would silently lose whatever it configures. Fix with:"
    log "           sudo chown $AGENT_UID:$AGENT_GID $path"
    exit 1
  fi
done

log "done"
