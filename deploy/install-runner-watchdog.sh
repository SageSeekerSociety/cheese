#!/usr/bin/env bash
# Install the runner watchdog for the runner this script is running under.
# Usage: install-runner-watchdog.sh RUNNER_ROOT
# RUNNER_ROOT is the runner install directory; its `.service` file (written by
# the runner's own svc.sh) names the systemd unit to watch.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
root=${1:?usage: install-runner-watchdog.sh RUNNER_ROOT}
[ -f "$root/.service" ] || { printf "no %s/.service: runner is not installed as a service\n" "$root" >&2; exit 2; }
unit=$(head -n 1 "$root/.service")
[ -n "$unit" ] || { printf "%s/.service is empty\n" "$root" >&2; exit 2; }

elevate=()
if [ "$(id -u)" != 0 ]; then
  elevate=(sudo -n)
fi

tmp=$(mktemp -d)
trap "rm -rf \"$tmp\"" EXIT
sed -e "s|@RUNNER_ROOT@|$root|" -e "s|@RUNNER_UNIT@|$unit|" \
  "$here/systemd/cheese-runner-watchdog.service" > "$tmp/cheese-runner-watchdog.service"

"${elevate[@]}" install -d /usr/local/lib/cheese
"${elevate[@]}" install -m 0755 "$here/runner-watchdog.sh" /usr/local/lib/cheese/runner-watchdog.sh
"${elevate[@]}" install -m 0644 "$tmp/cheese-runner-watchdog.service" \
  "$here/systemd/cheese-runner-watchdog.timer" /etc/systemd/system/
"${elevate[@]}" systemctl daemon-reload
"${elevate[@]}" systemctl enable --now cheese-runner-watchdog.timer
"${elevate[@]}" systemctl is-active --quiet cheese-runner-watchdog.timer
