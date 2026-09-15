#!/usr/bin/env bash
# Runs before EVERY job this runner picks up (ACTIONS_RUNNER_HOOK_JOB_STARTED).
#
# What it exists for: a forwarded-project FUSE mount whose server process was
# killed does not go away. The directory stays occupied and answers every stat
# with ENOTCONN, and `actions/checkout` walks the workspace before it clones — so
# one left behind wedges every later job on that machine. Seen on 2026-09-15: four
# jobs in a row died in a 15-minute timeout with an empty workspace, no git
# process and no open socket, while the machine reported itself online and idle.
#
# BEFORE a job, not after, deliberately. The run that leaves one behind is a run
# that was killed — the acceptance suite kills a case at 300s, and a cancelled job
# takes its processes with it — so it never reaches its own cleanup. A cleanup
# that ran after a job would also have to walk the same dead mount to find it.
#
# Only mounts that do not answer are released: with more than one runner slot per
# machine, a mount that answers belongs to a job running right now.
set -uo pipefail

while read -r point; do
  [ -n "$point" ] || continue
  if timeout 5 stat "$point" >/dev/null 2>&1; then
    continue
  fi
  for unmount in fusermount3 fusermount; do
    command -v "$unmount" >/dev/null 2>&1 || continue
    "$unmount" -u "$point" 2>/dev/null || "$unmount" -uz "$point" 2>/dev/null || true
  done
  echo "job-started hook: released a dead forwarded mount at $point"
done < <(mount 2>/dev/null | awk '$1 == "FuseProject" { print $3 }')

# The other way this machine stops being able to run a job. Kept in its own script
# because it is a different failure with a different trigger; both belong here
# because "before the next job" is when a machine can be repaired at all.
guard="$(dirname "$0")/disk-guard.sh"
[ -x "$guard" ] && "$guard"

exit 0
