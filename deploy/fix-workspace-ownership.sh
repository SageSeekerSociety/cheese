#!/usr/bin/env bash
# One-time ownership migration for the backend's host bind mounts.
#
# WHY: the backend process and the in-container agent share one git store (the
# project's main-repo .git is bind-mounted into every sandbox container,
# read-write) and BOTH commit into it — the agent's own commit is how a topic
# branch moves. git creates object directories 0755 and loose objects 0444,
# owned by whoever wrote them, so a uid split leaves the second side able to
# read everything and add nothing. Backend-side that showed up as a
# project-wide 422 on the file panel; sandbox-side as an agent whose work could
# not leave the container. So the backend image runs as the SAME uid as the
# sandbox's `node` (1000, see backend/Dockerfile). Files written by the old uid
# (1001) must be handed over once, or the new backend cannot read its own
# history.
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
  log "handing $path over to ${AGENT_UID}:${AGENT_GID}…"
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

log "done"
