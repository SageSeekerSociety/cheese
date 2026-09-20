#!/usr/bin/env bash
# Install the nightly cache reclaim. Run it ON the box, as the user whose
# caches fill the disk — that user is written into the unit, because the script
# reclaims things under HOME and root's copies of them are empty.
#
# Without this, `dev-box-disk-cleanup.sh` only ever runs when a person
# remembers, and `cheesex-disk-pressure-guard` is no substitute: it waits for
# 85% and then removes sandbox containers, never a cache. So the disk climbs and
# the brake it climbs toward cannot reclaim what is actually taking the space.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)

user=${SUDO_USER:-$(id -un)}
if [ "$user" = root ]; then
  printf 'refusing to install for root: the caches this reclaims live under a\nreal user HOME. Re-run as that user (sudo keeps SUDO_USER).\n' >&2
  exit 2
fi
home=$(eval echo "~$user")
[ -d "$home" ] || { printf 'no home directory for %s\n' "$user" >&2; exit 2; }

elevate=()
if [ "$(id -u)" != 0 ]; then
  elevate=(sudo -n)
fi

"${elevate[@]}" install -d /usr/local/lib/cheese
"${elevate[@]}" install -m 0755 "$here/dev-box-disk-cleanup.sh" \
  /usr/local/lib/cheese/dev-box-disk-cleanup.sh

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sed -e "s|@CLEANUP_USER@|$user|" -e "s|@CLEANUP_HOME@|$home|" \
  "$here/systemd/cheese-disk-cleanup.service" > "$tmp/cheese-disk-cleanup.service"
cp "$here/systemd/cheese-disk-cleanup.timer" "$tmp/"

"${elevate[@]}" install -m 0644 "$tmp/cheese-disk-cleanup.service" \
  "$tmp/cheese-disk-cleanup.timer" /etc/systemd/system/
"${elevate[@]}" systemctl daemon-reload
"${elevate[@]}" systemctl enable --now cheese-disk-cleanup.timer
"${elevate[@]}" systemctl is-active --quiet cheese-disk-cleanup.timer

printf 'installed for %s (HOME=%s)\n' "$user" "$home"
systemctl list-timers --no-pager cheese-disk-cleanup.timer | tail -2
