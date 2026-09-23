#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$root/tmp"
scratch="$(mktemp -d "$root/tmp/test-metering-proxy.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
mkdir "$scratch/bin" "$scratch/target"

cat > "$scratch/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MOCK_DOCKER_CALLS"
if [ "$1" = inspect ]; then
  printf 'true\n'
elif [[ " $* " == *' up -d --force-recreate --no-deps metering-proxy '* ]]; then
  if [ -f "$MOCK_FAIL_ONCE" ]; then
    rm "$MOCK_FAIL_ONCE"
    exit 1
  fi
fi
EOF
cat > "$scratch/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf '407'
exit 56
EOF
chmod +x "$scratch/bin/docker" "$scratch/bin/curl"

export MOCK_DOCKER_CALLS="$scratch/docker-calls"
export MOCK_FAIL_ONCE="$scratch/fail-once"
export PATH="$scratch/bin:$PATH"
touch "$scratch/target/.env"
for file in billing_addon.py cheese_billing_core.py control_answers.json compose.yml; do
  cp "$root/deploy/metering-proxy/$file" "$scratch/target/$file"
done

bash "$root/deploy/deploy-metering-proxy.sh" "$scratch/target" test-revision
! grep -q ' up -d ' "$MOCK_DOCKER_CALLS"

printf '\n# old addon\n' >> "$scratch/target/billing_addon.py"
bash "$root/deploy/deploy-metering-proxy.sh" "$scratch/target" test-revision
cmp "$root/deploy/metering-proxy/billing_addon.py" "$scratch/target/billing_addon.py"
grep -q ' up -d --force-recreate --no-deps metering-proxy' "$MOCK_DOCKER_CALLS"
grep -q 'old addon' "$scratch/target"/deploy-backups/*/billing_addon.py

printf '\n# restore this file\n' >> "$scratch/target/billing_addon.py"
touch "$MOCK_FAIL_ONCE"
if bash "$root/deploy/deploy-metering-proxy.sh" "$scratch/target" test-revision; then
  echo 'expected a deployment failure' >&2
  exit 1
fi
grep -q 'restore this file' "$scratch/target/billing_addon.py"
grep -q 'status=rollback-complete' "$scratch/target"/deploy-logs/*.log
printf 'metering proxy deployment: no-op, update, and rollback passed\n'
