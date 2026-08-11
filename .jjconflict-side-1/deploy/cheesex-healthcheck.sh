#!/bin/sh
set -eu

# A dependency outage must not cause a restart storm. /health is the cheap
# process/liveness probe; /readyz remains the dependency-aware deployment gate.
state=/run/cheesex-healthcheck.failures
if /usr/bin/curl --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:8099/health >/dev/null; then
  printf '0\n' > "$state"
  exit 0
fi

failures=0
if test -r "$state"; then
  read -r failures < "$state" || failures=0
fi
case "$failures" in
  ''|*[!0-9]*) failures=0 ;;
esac
failures=$((failures + 1))
printf '%s\n' "$failures" > "$state"

if test "$failures" -ge 3; then
  /usr/bin/logger -t cheesex-healthcheck \
    'local health failed three times; restarting cheesex.service'
  /usr/bin/systemctl restart cheesex.service
  printf '0\n' > "$state"
fi
