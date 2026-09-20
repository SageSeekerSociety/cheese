#!/usr/bin/env bash
# Cloud SSH forwards own device connection ingress and are released separately
# from the rolling application tier. Drain execution before replacing them.
set -euo pipefail

if [ -f "$HOME/ops/deploy.env" ]; then
  set -a; . "$HOME/ops/deploy.env"; set +a
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
env_file="${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}"
[ -r "$env_file" ] || { echo "backend env file not readable: $env_file" >&2; exit 1; }
read_setting() {
  local key="$1" value
  value="$(awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$env_file")"
  case "$value" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac
  printf '%s' "$value"
}
owner_secret="${DEVICE_CONNECTION_SECRET:-}"
[ -n "$owner_secret" ] || owner_secret="$(read_setting DEVICE_CONNECTION_SECRET)"
[ -n "$owner_secret" ] || owner_secret="$(read_setting JWT_SECRET)"
[ -n "$owner_secret" ] || { echo "device connection secret is not configured" >&2; exit 1; }
owner_url="http://127.0.0.1:${DEVICE_CONNECTION_PORT:-18083}"
owner_status() {
  local path="$1"
  printf 'silent\nshow-error\nmax-time = 3\nrequest = POST\noutput = /dev/null\nwrite-out = %%{http_code}\nheader = "X-Device-Connection-Secret: %s"\nurl = "%s%s"\n' \
    "$owner_secret" "$owner_url" "$path" | curl --config -
}
owner_post() {
  local path="$1"
  printf 'silent\nshow-error\nfail\nmax-time = 3\nrequest = POST\nheader = "X-Device-Connection-Secret: %s"\nurl = "%s%s"\n' \
    "$owner_secret" "$owner_url" "$path" | curl --config -
}
online_managed_device_generations() {
  local managed
  managed="$(docker exec -i cheese-backend-1 /app/.venv/bin/python - <<'PY'
import asyncio
from sqlalchemy import select
from app.core.db import async_session_factory
from app.domain.device.models import DeviceRow

async def main():
    async with async_session_factory() as session:
        values = await session.scalars(
            select(DeviceRow.device_id).where(DeviceRow.cloud_control_private.is_(True))
        )
        print("\n".join(sorted(values)))
asyncio.run(main())
PY
)"
  [ -n "$managed" ] || return 0
  printf 'silent\nshow-error\nfail\nmax-time = 5\nheader = "X-Device-Connection-Secret: %s"\nurl = "%s/internal/device-connection/snapshot"\n' \
    "$owner_secret" "$owner_url" | curl --config - \
    | MANAGED_DEVICES="$managed" python3 -c '
import json, os, sys
managed = set(os.environ["MANAGED_DEVICES"].splitlines())
snapshot = json.load(sys.stdin)
print("\n".join(sorted(
    "{}\t{}".format(item["device_id"], item.get("connection_generation", 0))
    for item in snapshot["devices"]
    if item["online"] and item["device_id"] in managed
)))'
}

drained=false
resume_owner() {
  [ "$drained" = true ] || return 0
  owner_post /internal/device-connection/release-resume >/dev/null 2>&1 || true
}
trap resume_owner EXIT

# Same waiver the owner's own release carries, for the same reason: an idle
# owner never arrives on a platform anybody is using, because `call_executor`
# is held open under a shield and re-polled about once a second. Without it the
# forwards cannot be released at all — the owner sat unreleasable for two days
# that way while #1114 waited for it. Default unchanged; this is opt-in.
interrupt="${DEVICE_CONNECTION_INTERRUPT:-0}"
for attempt in $(seq 1 240); do
  status="$(owner_status /internal/device-connection/release-drain)" || {
    echo "device connection owner drain request failed" >&2
    exit 1
  }
  case "$status" in
    200) drained=true; break ;;
    409)
      if [ "$interrupt" = 1 ]; then
        echo "device connection owner is busy; interrupting its in-flight calls as asked" >&2
        break
      fi
      if [ "$attempt" -eq 240 ]; then
        echo "device connection owner remained busy; cloud control release stopped" >&2
        exit 1
      fi
      sleep 0.25
      ;;
    *) echo "device connection owner drain returned HTTP $status" >&2; exit 1 ;;
  esac
done

# Sample after admission is closed: a connection that changed while waiting for
# idle is the baseline, not evidence that this maintenance restored its tunnel.
# A machine already offline must not make this release wait forever.
expected_devices="$(online_managed_device_generations)"
bash "$HERE/install-cloud-control.sh"
for attempt in $(seq 1 60); do
  current_devices="$(online_managed_device_generations)" || current_devices=""
  missing="$(EXPECTED_DEVICES="$expected_devices" CURRENT_DEVICES="$current_devices" python3 -c '
import os
def values(name):
    return dict(line.split("\t", 1) for line in os.environ[name].splitlines() if line)
expected, current = values("EXPECTED_DEVICES"), values("CURRENT_DEVICES")
print("\n".join(sorted(
    device for device, generation in expected.items()
    if device not in current or current[device] == generation
)))' )"
  [ -z "$missing" ] && break
  if [ "$attempt" -eq 60 ]; then
    echo "cloud control forwards did not restore every previously online device" >&2
    exit 1
  fi
  sleep 1
done
owner_post /internal/device-connection/release-resume >/dev/null
drained=false
