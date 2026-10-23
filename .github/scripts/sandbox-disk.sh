#!/usr/bin/env bash
# Give the sandbox tests a directory on an XFS filesystem with project quotas,
# as a platform host's data disk is (`enrollment.DATA_DISK`): no sandbox runs
# without a disk limit, so tests/unit/test_sandbox_network.py makes its homes
# there (`CHEESE_TEST_SANDBOX_DISK`). A sparse file on a loop device, one per
# runner slot, since two slots of a self-hosted runner run at once; mounted
# once and kept, like the packages `ensure-apt.sh` installs, so a later run on
# the same runner reuses it.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
"$here/ensure-apt.sh" xfsprogs
slot="${CHEESE_CI_SLOT:-0}"
image="/var/lib/cheese-ci/sandbox-disk-$slot.img"
mount="/mnt/cheese-ci-sandbox-disk-$slot"
if ! mountpoint -q "$mount"; then
  sudo mkdir -p "$(dirname "$image")" "$mount"
  sudo rm -f "$image"
  sudo truncate -s 4G "$image"
  sudo mkfs.xfs -q "$image"
  sudo mount -o loop,prjquota "$image" "$mount"
fi
sudo install -d -o "$(id -u)" -g "$(id -g)" "$mount/tests"
echo "CHEESE_TEST_SANDBOX_DISK=$mount/tests" >> "$GITHUB_ENV"
