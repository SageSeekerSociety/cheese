#!/usr/bin/env bash
# Exercise retirement against fake host commands, preserving unrelated schedules.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheese-prune-retirement.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT
mkdir -p "$RUN_DIR/bin"
export CRON_FILE="$RUN_DIR/crontab" HOST_CALLS="$RUN_DIR/calls"
export PATH="$RUN_DIR/bin:$PATH"
cat > "$RUN_DIR/bin/crontab" <<'EOF'
#!/bin/sh
if [ "$1" = '-l' ]; then
  if [ "${CRON_ERROR:-}" ]; then echo "$CRON_ERROR" >&2; exit 1; fi
  cat "$CRON_FILE"
else
  cp "$1" "$CRON_FILE"
  echo crontab-installed >> "$HOST_CALLS"
fi
EOF
cat > "$RUN_DIR/bin/systemctl" <<'EOF'
#!/bin/sh
echo "systemctl $*" >> "$HOST_CALLS"
if [ "$*" = 'show ci-docker-prune.timer --property=LoadState --value' ]; then
  echo "${TIMER_LOAD:-loaded}"
else
  echo 'ActiveState=active'
  echo 'UnitFileState=enabled'
fi
EOF
cat > "$RUN_DIR/bin/sudo" <<'EOF'
#!/bin/sh
echo "sudo $*" >> "$HOST_CALLS"
EOF
cat > "$RUN_DIR/bin/docker" <<'EOF'
#!/bin/sh
echo "docker $*" >> "$HOST_CALLS"
exit 99
EOF
chmod +x "$RUN_DIR/bin/"*
fail() { echo "FAIL: $*" >&2; exit 1; }
printf '%s\n' \
  '# unrelated schedules survive verbatim' \
  '0 4 * * * /home/ci/backup.sh' \
  '0 5 * * 0 docker system prune -af --filter until=168h >/dev/null 2>&1' \
  '0 6 * * 0 docker system prune -af --filter label=owned' > "$CRON_FILE"
cp "$CRON_FILE" "$RUN_DIR/original"
bash "$ROOT/deploy/ci-runner/retire-docker-prune.sh" "$RUN_DIR/backup"
cmp "$RUN_DIR/original" "$RUN_DIR/backup/crontab"
sed '3d' "$RUN_DIR/original" > "$RUN_DIR/expected"
cmp "$RUN_DIR/expected" "$CRON_FILE"
grep -qx 'sudo -n systemctl disable --now ci-docker-prune.timer' "$HOST_CALLS"
grep -qx 'ActiveState=active' "$RUN_DIR/backup/timer-state"
if grep -Eq '^docker |sudo .*ci-docker-prune.service' "$HOST_CALLS"; then
  fail 'retirement invoked Docker or stopped the cleanup service'
fi

# Absent timer and no legacy cron: no mutation is needed.
: > "$HOST_CALLS"
TIMER_LOAD=not-found bash "$ROOT/deploy/ci-runner/retire-docker-prune.sh" "$RUN_DIR/again"
if grep -Eq '^sudo |crontab-installed|^docker ' "$HOST_CALLS"; then
  fail 'absent timer or unrelated cron was mutated'
fi
CRON_ERROR='no crontab for ci' TIMER_LOAD=not-found \
  bash "$ROOT/deploy/ci-runner/retire-docker-prune.sh" "$RUN_DIR/absent"
if CRON_ERROR='permission denied' bash "$ROOT/deploy/ci-runner/retire-docker-prune.sh" "$RUN_DIR/error"; then
  fail 'unreadable crontab was treated as an empty one'
fi
# The installed fleet has no crontab executable; its systemd timer still retires.
mkdir "$RUN_DIR/no-cron-bin"
for tool in mkdir dirname grep cat; do
  ln -s "$(command -v "$tool")" "$RUN_DIR/no-cron-bin/$tool"
done
cp "$RUN_DIR/bin/systemctl" "$RUN_DIR/bin/sudo" "$RUN_DIR/no-cron-bin/"
: > "$HOST_CALLS"
PATH="$RUN_DIR/no-cron-bin" /bin/bash "$ROOT/deploy/ci-runner/retire-docker-prune.sh" "$RUN_DIR/no-cron"
grep -qx 'crontab command absent' "$RUN_DIR/no-cron/crontab-status"
grep -qx 'sudo -n systemctl disable --now ci-docker-prune.timer' "$HOST_CALLS"
if bash "$ROOT/deploy/ci-runner/retire-docker-prune.sh" "$RUN_DIR/backup"; then
  fail 'an existing backup was overwritten'
fi
cmp "$RUN_DIR/original" "$RUN_DIR/backup/crontab"
echo 'CI prune retirement: all cases passed'
