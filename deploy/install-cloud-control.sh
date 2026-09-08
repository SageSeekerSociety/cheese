#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd "$(dirname "$0")" && pwd)
target_dir="$HOME/.local/lib/cheese-cloud-control"
unit_dir="$HOME/.config/systemd/user"
test "$(loginctl show-user "$(id -un)" -p Linger --value)" = yes
mkdir -p "$target_dir" "$unit_dir"
install -m 755 "$source_dir/cloud-control.py" "$target_dir/cloud-control.py.next"
mv "$target_dir/cloud-control.py.next" "$target_dir/cloud-control.py"
install -m 644 "$source_dir/systemd/cheese-cloud-control.service" "$unit_dir/"
systemctl --user daemon-reload
systemctl --user enable cheese-cloud-control.service
systemctl --user restart cheese-cloud-control.service
systemctl --user is-active --quiet cheese-cloud-control.service
sha256sum "$target_dir/cloud-control.py"
