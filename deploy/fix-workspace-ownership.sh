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
set -euo pipefail

AGENT_UID="${AGENT_UID:-1000}"
AGENT_GID="${AGENT_GID:-1000}"
MARKER=".cheese-uid-$AGENT_UID"

log() { echo "[fix-ownership $(date '+%H:%M:%S')] $*"; }

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
  # `! -uid` keeps a re-run cheap (nothing to write once it is correct) and
  # keeps the walk honest about what it changed. -print is deliberate: an
  # operator reading the deploy log should see the size of the handover.
  docker run --rm --user 0 --entrypoint sh \
    -v "$path:/target" "$IMAGE" -c "
      set -eu
      changed=\$(find /target \\( ! -uid $AGENT_UID -o ! -gid $AGENT_GID \\) -print | wc -l)
      find /target \\( ! -uid $AGENT_UID -o ! -gid $AGENT_GID \\) \
        -exec chown -h $AGENT_UID:$AGENT_GID {} +
      : > /target/$MARKER
      chown $AGENT_UID:$AGENT_GID /target/$MARKER
      echo \"\$changed entries re-owned\"
    " || { log "ERROR: ownership handover failed for $path"; exit 1; }
done

log "done"
