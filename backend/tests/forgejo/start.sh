#!/usr/bin/env bash
set -euo pipefail
umask 077
here="$(cd "$(dirname "$0")" && pwd)"
port="${FORGEJO_TEST_PORT:-33086}"
token_file="${FORGEJO_TEST_TOKEN_FILE:-$here/admin-token}"
dc() { docker --context "${DOCKER_CONTEXT:-colima}" compose -p "${FORGEJO_TEST_PROJECT:-cheese-forge1286-test}" -f "$here/compose.yml" "$@"; }
dc up -d
for attempt in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$port/api/v1/version" >/dev/null; then break; fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$port/api/v1/version"
curl -fsS --retry 30 --retry-connrefused --retry-delay 1 \
  "http://127.0.0.1:${FORGEJO_TEST_S3_PORT:-33286}/minio/health/live"
if [ ! -s "$token_file" ]; then
  test_password="$(openssl rand -hex 32)Aa1!"
  dc exec -T forgejo forgejo --config /var/lib/gitea/custom/conf/app.ini admin user create \
    --username cheese-platform --password "$test_password" \
    --email cheese-platform@users.invalid --admin --must-change-password=false
  dc exec -T forgejo forgejo --config /var/lib/gitea/custom/conf/app.ini admin user generate-access-token \
    --username cheese-platform --token-name cheese-test-admin --scopes all --raw > "$token_file"
fi
if [ -n "${GITHUB_ENV:-}" ]; then
  token="$(cat "$token_file")"
  echo "::add-mask::$token"
  {
    echo "FORGEJO_URL=http://127.0.0.1:$port"
    echo "FORGEJO_API_URL=http://127.0.0.1:$port/api/v1"
    echo "FORGEJO_ADMIN_TOKEN=$token"
    echo "S3_ENDPOINT_URL=http://127.0.0.1:${FORGEJO_TEST_S3_PORT:-33286}"
    echo "S3_ACCESS_KEY=e2e-snapshots"
    echo "S3_SECRET_KEY=e2e-snapshots-secret"
    echo "TRANSCRIPT_S3_BUCKET=e2e-snapshots"
  } >> "$GITHUB_ENV"
fi
