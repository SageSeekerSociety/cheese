#!/usr/bin/env bash
# Provision a permanent 4G swapfile on the bare-metal build boxes (dev + prod).
#
# The boxes have ~8G RAM and no swap, and the frontend (vite) build over ~4000
# modules OOM-kills without headroom. deploy-blue-green.sh used to add/remove a
# temp swapfile around each build (needing swapon/swapoff sudo every deploy) —
# a workaround. Permanent swap makes the build reliable and lets the deploy
# script drop that dance entirely. Idempotent; safe to re-run.
set -euo pipefail
SWAP=/swapfile
SIZE=4G

if grep -q "^$SWAP " /proc/swaps 2>/dev/null; then
  echo "swap already active: $SWAP"; cat /proc/swaps; exit 0
fi
if [ ! -f "$SWAP" ]; then
  echo "creating $SIZE $SWAP"
  sudo fallocate -l "$SIZE" "$SWAP" 2>/dev/null || sudo dd if=/dev/zero of="$SWAP" bs=1M count=4096 status=none
  sudo chmod 600 "$SWAP"
  sudo mkswap "$SWAP" >/dev/null
fi
sudo swapon "$SWAP"
grep -qE "^$SWAP[[:space:]]" /etc/fstab || echo "$SWAP none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
echo "swap enabled + persisted in /etc/fstab:"
cat /proc/swaps
