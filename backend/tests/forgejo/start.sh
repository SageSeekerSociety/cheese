#!/usr/bin/env bash
set -euo pipefail
umask 077
here="$(cd "$(dirname "$0")" && pwd)"
dc() { docker --context colima compose -p cheese-forge1286-test -f "$here/compose.yml" "$@"; }
dc up -d
for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:33086/api/v1/version >/dev/null; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:33086/api/v1/version
if [ ! -s "$here/admin-token" ]; then
  test_password="$(openssl rand -hex 32)Aa1!"
  dc exec -T forgejo forgejo --config /var/lib/gitea/custom/conf/app.ini admin user create \
    --username cheese-platform --password "$test_password" \
    --email cheese-platform@users.invalid --admin --must-change-password=false
  dc exec -T forgejo forgejo --config /var/lib/gitea/custom/conf/app.ini admin user generate-access-token \
    --username cheese-platform --token-name cheese-test-admin --scopes all --raw > "$here/admin-token"
fi
