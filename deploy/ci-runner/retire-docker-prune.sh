#!/usr/bin/env bash
# Retire only the CI fleet's unattended global Docker reclamation.
# A running job in the other slot may still be pulling an image.
set -euo pipefail
export LC_ALL=C

backup="${1:?usage: retire-docker-prune.sh BACKUP_DIRECTORY}"
mkdir -p "$(dirname "$backup")"
mkdir "$backup"
if ! command -v crontab >/dev/null 2>&1; then
  echo 'crontab command absent' > "$backup/crontab-status"
elif crontab -l > "$backup/crontab" 2> "$backup/crontab.stderr"; then
  legacy='0 5 * * 0 docker system prune -af --filter until=168h >/dev/null 2>&1'
  awk -v legacy="$legacy" '$0 != legacy' "$backup/crontab" > "$backup/crontab.next"
  if ! cmp -s "$backup/crontab" "$backup/crontab.next"; then
    crontab "$backup/crontab.next"
  fi
elif ! grep -q 'no crontab for' "$backup/crontab.stderr"; then
  cat "$backup/crontab.stderr" >&2
  exit 1
fi

systemctl show ci-docker-prune.timer --property=LoadState --value > "$backup/timer-load-state"
if ! grep -qx 'not-found' "$backup/timer-load-state"; then
  systemctl show ci-docker-prune.timer > "$backup/timer-state"
  # Stop the timer, never an already running cleanup service.
  sudo -n systemctl disable --now ci-docker-prune.timer
fi
echo "retired scheduled Docker prune; previous configuration saved in $backup"
