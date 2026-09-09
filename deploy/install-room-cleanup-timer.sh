#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
elevate=()
if [ "$(id -u)" != 0 ]; then
  elevate=(sudo -n)
fi
"${elevate[@]}" install -d /usr/local/lib/cheese
"${elevate[@]}" install -m 0755 "$here/trigger-room-cleanup.sh" /usr/local/lib/cheese/trigger-room-cleanup.sh
"${elevate[@]}" install -m 0644 "$here/systemd/cheese-room-cleanup.service" "$here/systemd/cheese-room-cleanup.timer" /etc/systemd/system/
"${elevate[@]}" systemctl daemon-reload
"${elevate[@]}" systemctl enable --now cheese-room-cleanup.timer
"${elevate[@]}" systemctl is-active --quiet cheese-room-cleanup.timer
