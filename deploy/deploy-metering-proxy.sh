#!/usr/bin/env bash
# Deploy the metering addon from the checked-out commit to the dev box's
# persistent Compose directory. The .env, credentials, and usage log stay there.
set -Eeuo pipefail

target_dir="${1:?usage: deploy-metering-proxy.sh <target-dir> <commit-sha>}"
revision="${2:?usage: deploy-metering-proxy.sh <target-dir> <commit-sha>}"
source_dir="$(cd "$(dirname "$0")/metering-proxy" && pwd)"
files=(billing_addon.py cheese_billing_core.py control_answers.json compose.yml)

test -f "$target_dir/.env"
for file in "${files[@]}"; do
  test -f "$source_dir/$file"
  test -f "$target_dir/$file"
done

mkdir -p "$target_dir/deploy-logs" "$target_dir/deploy-backups"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$revision-$$"
log_file="$target_dir/deploy-logs/$run_id.log"
: > "$log_file"
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$log_file"; }
compose() { docker compose -p metering-proxy --env-file "$target_dir/.env" -f "$target_dir/compose.yml" "$@"; }
ready() {
  local response
  test "$(docker inspect --format '{{.State.Running}}' cheese-metering-proxy)" = true || return 1
  response="$(curl --noproxy '' --proxy http://127.0.0.1:8444 --insecure \
    --max-time 3 --silent --output /dev/null --write-out '%{http_connect}' \
    https://api.anthropic.com/v1/messages || true)"
  test "$response" = 407
}

log "status=start revision=$revision source=$source_dir target=$target_dir"
compose config -q >/dev/null
changed=false
for file in "${files[@]}"; do
  if cmp -s "$source_dir/$file" "$target_dir/$file"; then
    log "item=$file status=skip reason=identical"
  else
    changed=true
    log "item=$file status=change source_sha256=$(sha256sum "$source_dir/$file" | cut -d' ' -f1) target_sha256=$(sha256sum "$target_dir/$file" | cut -d' ' -f1)"
  fi
done
if [ "$changed" = false ]; then
  log "status=complete action=no-op"
  exit 0
fi
if ! ready; then
  log "status=preflight-failed reason=existing-proxy-not-ready"
  exit 1
fi
log "status=preflight-ready container=cheese-metering-proxy"

backup_dir="$target_dir/deploy-backups/$run_id"
mkdir "$backup_dir"
for file in "${files[@]}"; do
  cp -p "$target_dir/$file" "$backup_dir/$file"
  log "item=$file status=backup path=$backup_dir/$file"
done

rollback() {
  local status=$? attempt
  trap - ERR
  log "status=rollback reason=deploy-error exit=$status"
  for file in "${files[@]}"; do
    cp -p "$backup_dir/$file" "$target_dir/$file" || log "item=$file status=rollback-copy-failed"
    rm -f "$target_dir/.$file.$run_id.next"
  done
  if ! compose up -d --force-recreate --no-deps metering-proxy; then
    log "status=rollback-failed backup=$backup_dir"
    exit "$status"
  fi
  for attempt in $(seq 1 15); do
    if ready; then
      log "status=rollback-complete attempt=$attempt backup=$backup_dir"
      exit "$status"
    fi
    sleep 2
  done
  log "status=rollback-failed reason=readiness backup=$backup_dir"
  exit "$status"
}
trap rollback ERR

for file in "${files[@]}"; do
  if ! cmp -s "$source_dir/$file" "$target_dir/$file"; then
    cp -p "$source_dir/$file" "$target_dir/.$file.$run_id.next"
    mv -f "$target_dir/.$file.$run_id.next" "$target_dir/$file"
    log "item=$file status=installed"
  fi
done
compose config -q >/dev/null
log "status=restart-start container=cheese-metering-proxy"
compose up -d --force-recreate --no-deps metering-proxy
for attempt in $(seq 1 15); do
  if ready; then
    log "status=complete action=updated attempt=$attempt backup=$backup_dir"
    exit 0
  fi
  sleep 2
done
false
