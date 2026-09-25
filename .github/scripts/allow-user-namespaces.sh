#!/usr/bin/env bash
# A room's Claude Code session runs in a user and mount namespace of its own,
# where the project is at the executor's path (`client.py enter`). Ubuntu 24.04
# lets an unprivileged process create the user namespace but takes away the
# capabilities that make it useful unless this AppArmor restriction is off; the
# session host (Debian) has no such restriction. Every job that launches a
# session runs this first, and it proves the namespace works before the tests
# find out the hard way.
set -euo pipefail

knob=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
if [ -e "$knob" ] && [ "$(cat "$knob")" != 0 ]; then
  sudo -n sysctl -qw kernel.apparmor_restrict_unprivileged_userns=0
fi
unshare --user --map-current-user --mount true
