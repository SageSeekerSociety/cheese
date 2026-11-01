#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${1:-all}"
FAKE_BIN="$ROOT/deploy/tests/fakes/app-tier"
export APP_TIER_REAL_PYTHON="$(command -v python3)"
# The real docker CLI, for the one case that reads compose's real model.
export APP_TIER_REAL_DOCKER_BIN="$(command -v docker || true)"
mkdir -p "$ROOT/.tmp"
forge_test_env="$(mktemp "$ROOT/.tmp/forge-env.XXXXXX")"
printf 'FRONTEND_URL=https://cheese.example\n' > "$forge_test_env"
export BACKEND_ENV_FILE="$forge_test_env"
export FORGE_EVENTS_ENV_FILE="$forge_test_env.events"
export FORGEJO_URL=https://cheese.example/forge/

# deploy-docker.sh CREATES the claude binary cache (it must exist before the
# bind mount, or docker makes it root-owned and the backend cannot write it).
# Its default is a real path on the dev box, which a CI runner has neither
# reason nor permission to create — so every deploy in this file gets one
# inside the test tree. Exported once rather than per case: a future test that
# forgets it would not fail here, it would fail on someone's machine.
export CLAUDE_CACHE_HOST_PATH="$ROOT/.tmp/claude-cache-$$"
# And the pi build cache beside it.
export PI_CACHE_HOST_PATH="$ROOT/.tmp/pi-cache-$$"
# And the enrolled machines' toolchain cache beside those two.
export TOOLCHAIN_CACHE_HOST_PATH="$ROOT/.tmp/toolchain-cache-$$"
export APPHOME_HOST_PATH="$ROOT/.tmp/apphome-$$"
trap 'rm -rf "$ROOT/.tmp/claude-cache-$$" "$ROOT/.tmp/pi-cache-$$" "$ROOT/.tmp/toolchain-cache-$$" "$ROOT/.tmp/apphome-$$"; rm -f "$forge_test_env" "$FORGE_EVENTS_ENV_FILE"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

test_deploy_rejects_absent_frontend() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/app-tier-deploy.XXXXXX")"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=frontend_absent \
    APP_TIER_MAIN_SHA=testsha \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml"; then
    rm -rf "$run_dir"
    fail "deploy succeeded with no frontend container"
  fi
  rm -rf "$run_dir"
  echo "PASS: deploy rejects an absent frontend"
}

test_deploy_accepts_healthy_pair() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/app-tier-deploy.XXXXXX")"
  if ! PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null; then
    rm -rf "$run_dir"
    fail "deploy rejected one healthy current backend/frontend pair"
  fi
  rm -rf "$run_dir"
  echo "PASS: deploy accepts one healthy current backend/frontend pair"
}

test_deploy_requires_released_collab() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/app-tier-collab.XXXXXX")"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=collab_absent \
    APP_TIER_MAIN_SHA=testsha \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "deploy succeeded with the collaboration service missing"
  fi
  if ! PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=collab_unreleased \
    APP_TIER_MAIN_SHA=testsha \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "deploy refused a release that predates the collaboration service"
  fi
  rm -rf "$run_dir"
  echo "PASS: collab is required exactly when the release has it"
}

test_deploy_keeps_connection_owner_running() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/connection-owner.XXXXXX")"
  docker_log="$run_dir/docker.log"
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=stable_owner \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null

  if grep -E 'up -d --no-deps (--force-recreate )?device-connection|release-drain' "$docker_log" >/dev/null; then
    rm -rf "$run_dir"
    fail "business deploy drained or recreated a device connection owner that maps the release's schema"
  fi
  grep -F 'up -d backend frontend' "$docker_log" >/dev/null || {
    rm -rf "$run_dir"
    fail "business deploy did not update the app tier"
  }
  rm -rf "$run_dir"
  echo "PASS: business deploy leaves the device connection owner running"
}

# line_of <pattern> <file>: the first line matching, or nothing.
line_of() {
  { grep -n -F -- "$1" "$2" || true; } | head -n 1 | cut -d: -f1
}

# The owners outlive app releases, so a release whose migrations drop what an
# owner's image still maps would leave that owner failing every query that
# loads it. Such an owner is drained before the migrations and recreated on
# the release after them; one whose schema the migrations leave alone keeps
# running, whatever its models map that the release's no longer do.
test_deploy_releases_owners_behind_the_schema() {
  local run_dir docker_log drained migrated device_back preview_back
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_SCENARIO=stable_owner \
    APP_TIER_OWNER_SCHEMA="topics topics.id topics.compute_config" \
    APP_TIER_PENDING_TOUCHES=$'pending c4e9a2f7d153\ntopics.compute_config' \
    DEVICE_CONNECTION_SECRET=test-owner-secret >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "deploy failed with owners behind the schema"; }
  drained="$(line_of 'device-connection/release-drain' "$docker_log")"
  migrated="$(line_of 'alembic upgrade head' "$docker_log")"
  device_back="$(line_of 'up -d --no-deps --force-recreate device-connection' "$docker_log")"
  preview_back="$(line_of 'up -d --no-deps --force-recreate preview-connection' "$docker_log")"
  [ -n "$drained" ] && [ -n "$migrated" ] && [ "$drained" -lt "$migrated" ] \
    || { rm -rf "$run_dir"; fail "the device owner was not drained before the migrations"; }
  [ -n "$device_back" ] && [ "$device_back" -gt "$migrated" ] \
    || { rm -rf "$run_dir"; fail "the device owner was not recreated after the migrations"; }
  [ -n "$preview_back" ] && [ "$preview_back" -gt "$migrated" ] \
    || { rm -rf "$run_dir"; fail "the preview owner was not recreated after the migrations"; }
  grep -q -- '--force-recreate backend' "$docker_log" \
    || { rm -rf "$run_dir"; fail "the deploy did not roll the app tier"; }
  grep -F 'device connection owner maps schema the migrations change or the database lacks: topics.compute_config' "$run_dir/deploy.log" >/dev/null \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "the deploy did not say which schema held the owner"; }
  rm -rf "$run_dir"
  echo "PASS: owners that map schema the migrations drop are released across them"
}

# The first half of a drop only stops mapping a column (.claude/rules/migrations.md);
# the column stays until a later release's migration drops it. Until then no
# owner query can fail on it, so nothing is gained by closing their connections:
# not when the release merely maps less, and not when its migrations change
# other tables.
test_deploy_leaves_owners_the_migrations_spare() {
  local run_dir docker_log touches
  for touches in "" $'pending ea1b19de526b\nroom_locks\ntasks.compute_config'; do
    run_dir="$(new_rollout_run_dir)"
    docker_log="$run_dir/docker.log"
    rollout_run "$run_dir" env APP_TIER_SCENARIO=stable_owner \
      APP_TIER_OWNER_SCHEMA="topics topics.id topics.compute_config tasks tasks.id" \
      APP_TIER_PENDING_TOUCHES="$touches" \
      DEVICE_CONNECTION_SECRET=test-owner-secret >"$run_dir/deploy.log" 2>&1 \
      || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "deploy failed with owners the migrations spare"; }
    ! grep -E 'release-drain|force-recreate (device|preview)-connection' "$docker_log" >/dev/null \
      || { rm -rf "$run_dir"; fail "an owner was released although the migrations (${touches:-none}) change nothing it maps"; }
    grep -q -- '--force-recreate backend' "$docker_log" \
      || { rm -rf "$run_dir"; fail "the deploy did not roll the app tier"; }
    rm -rf "$run_dir"
  done
  echo "PASS: owners whose schema the migrations leave alone keep running"
}

# Migrations commit one by one: a deploy whose second migration failed keeps
# the first one's drop and resumes the old owner on it, and the next deploy no
# longer lists that migration. The database itself shows the owner is behind.
test_deploy_releases_owners_the_database_left_behind() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_SCENARIO=stable_owner \
    APP_TIER_OWNER_SCHEMA="topics topics.id topics.compute_config" \
    APP_TIER_DB_SCHEMA="topics topics.id" \
    DEVICE_CONNECTION_SECRET=test-owner-secret >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "deploy failed with an owner behind the database"; }
  grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "an owner mapping a column the database no longer has was left running"; }
  grep -F 'topics.compute_config (not in the database)' "$run_dir/deploy.log" >/dev/null \
    || { rm -rf "$run_dir"; fail "the deploy did not say what the owner maps that the database lacks"; }
  rm -rf "$run_dir"
  echo "PASS: an owner mapping what the database no longer has is released"
}

# A migration whose changes cannot be read is not proof that it spares them.
test_deploy_releases_owners_when_migrations_unreadable() {
  local run_dir docker_log released
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_SCENARIO=stable_owner \
    APP_TIER_PENDING_TOUCHES_BROKEN=1 \
    DEVICE_CONNECTION_SECRET=test-owner-secret >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "deploy failed when the migrations could not be read"; }
  for released in 'device-connection/release-drain' 'force-recreate device-connection' 'force-recreate preview-connection'; do
    grep -F "$released" "$docker_log" >/dev/null \
      || { rm -rf "$run_dir"; fail "owners were left on migrations nobody could read ($released missing)"; }
  done
  rm -rf "$run_dir"
  echo "PASS: migrations that cannot be read release the owners"
}

test_failed_migration_resumes_the_drained_owner() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  if rollout_run "$run_dir" env APP_TIER_SCENARIO=stable_owner \
    APP_TIER_OWNER_SCHEMA="topics topics.id topics.compute_config" \
    APP_TIER_PENDING_TOUCHES=$'pending c4e9a2f7d153\ntopics.compute_config' \
    APP_TIER_DOCKER_FAIL_MATCH='alembic upgrade head' \
    DEVICE_CONNECTION_SECRET=test-owner-secret >"$run_dir/deploy.log" 2>&1; then
    rm -rf "$run_dir"
    fail "deploy succeeded although its migrations failed"
  fi
  grep -F 'device-connection/release-resume' "$docker_log" >/dev/null \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "the owner drained for failed migrations was left draining"; }
  ! grep -E 'force-recreate (device|preview)-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "an owner was moved onto a release whose migrations failed"; }
  rm -rf "$run_dir"
  echo "PASS: a failed migration lets the drained owner take calls again"
}

test_deploy_applies_journal_retention() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/journal-retention.XXXXXX")"
  docker_log="$run_dir/docker.log"
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=stable_owner \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null

  grep -F "sudo install -D -m 0644 $ROOT/deploy/journald-cheese.conf /etc/systemd/journald.conf.d/cheese.conf" "$docker_log" >/dev/null || {
    rm -rf "$run_dir"
    fail "deploy did not install the journal retention"
  }
  grep -Fx 'systemctl restart systemd-journald' "$docker_log" >/dev/null || {
    rm -rf "$run_dir"
    fail "deploy installed the journal retention without restarting journald"
  }
  rm -rf "$run_dir"
  echo "PASS: deploy applies the journal retention"
}

test_local_deploy_installs_owner_from_verified_backend_image() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/connection-owner-local.XXXXXX")"
  docker_log="$run_dir/docker.log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    DEPLOY_APP_IMAGE_SOURCE=local BACKEND_IMAGE=repo/backend:local \
    FRONTEND_IMAGE=repo/frontend:local COLLAB_IMAGE=repo/collab:local DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null
  grep -F 'owner-up-env DEVICE_CONNECTION_IMAGE=repo/backend:local' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "local first install did not use the verified backend image for owner"; }
  rm -rf "$run_dir"
  echo "PASS: local first install starts owner from the verified backend image"
}

test_owner_release_reuses_box_config_and_stops_when_busy() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/connection-owner-release.XXXXXX")"
  mkdir -p "$run_dir/ops"
  docker_log="$run_dir/docker.log"
  cat > "$run_dir/ops/deploy.env" <<EOF
COMPOSE_OVERLAYS=docker-compose.subscription.yml
BACKEND_ENV_FILE=$run_dir/backend.env
BACKEND_IMAGE=repo/backend:box-pinned
DEVICE_CONNECTION_SECRET=test-owner-secret
DEPLOY_APP_IMAGE_SOURCE=local
EOF
  : > "$run_dir/backend.env"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null
  grep -F -- '-f '"$ROOT"'/deploy/compose/docker-compose.subscription.yml' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "owner release ignored the box compose overlay"; }
  grep -F 'image inspect repo/backend:box-pinned' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "owner release ignored the pinned local backend image"; }
  grep -F 'up -d --no-deps --force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "owner release did not isolate its recreate"; }
  ! grep -F 'test-owner-secret' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "owner release logged its internal secret"; }

  : > "$docker_log"
  if PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=409 APP_TIER_CURL_DRAIN_COUNT_FILE="$run_dir/drain-count" \
    HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "owner release proceeded while executor calls were active"
  fi
  ! grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "busy owner was recreated"; }
  # The first drain request already stopped the owner taking calls, so a
  # release that gives up must hand it back.
  grep -F 'release-resume' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "owner left draining after a release that gave up"; }

  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=409,409,200 APP_TIER_CURL_DRAIN_COUNT_FILE="$run_dir/drain-success-count" \
    HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null
  [ "$(cat "$run_dir/drain-success-count")" = 3 ] \
    || { rm -rf "$run_dir"; fail "owner release did not retry busy drain"; }
  grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "owner release did not recreate after drain became idle"; }

  : > "$docker_log"
  if PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=500 APP_TIER_CURL_DRAIN_COUNT_FILE="$run_dir/drain-error-count" \
    HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "owner release retried a non-busy drain failure"
  fi
  [ "$(cat "$run_dir/drain-error-count")" = 1 ] \
    || { rm -rf "$run_dir"; fail "owner release retried non-409 drain response"; }
  ! grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "owner was recreated after non-409 drain response"; }

  # DEVICE_CONNECTION_INTERRUPT=1 waives the wait, and only the wait: it takes
  # one look at the drain, and a busy answer no longer stops the release.
  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=409 APP_TIER_CURL_DRAIN_COUNT_FILE="$run_dir/drain-interrupt-count" \
    DEVICE_CONNECTION_INTERRUPT=1 HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null
  grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "interrupting release did not recreate a busy owner"; }
  [ "$(cat "$run_dir/drain-interrupt-count")" = 1 ] \
    || { rm -rf "$run_dir"; fail "interrupting release polled a busy drain more than once"; }

  # `late` (what a deploy uses when its migrations would break the owner) waits
  # the whole drain, then interrupts what still runs instead of giving up.
  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=409 APP_TIER_CURL_DRAIN_COUNT_FILE="$run_dir/drain-late-count" \
    DEVICE_CONNECTION_INTERRUPT=late HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1 \
    || { rm -rf "$run_dir"; fail "a late-interrupting release gave up on a busy owner"; }
  [ "$(cat "$run_dir/drain-late-count")" = 180 ] \
    || { rm -rf "$run_dir"; fail "a late-interrupting release did not wait the whole drain"; }
  grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "a late-interrupting release did not recreate a busy owner"; }

  # Waiving the wait is not waiving the error handling: a drain that answers
  # something other than 200/409 still stops the release.
  : > "$docker_log"
  if PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=500 APP_TIER_CURL_DRAIN_COUNT_FILE="$run_dir/drain-interrupt-error" \
    DEVICE_CONNECTION_INTERRUPT=1 HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "interrupting release ignored a broken drain endpoint"
  fi
  ! grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "interrupting release recreated after a broken drain endpoint"; }

  : > "$docker_log"
  if PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_DOCKER_FAIL_MATCH=force-recreate HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "owner release succeeded after its recreate failed"
  fi
  grep -F 'release-resume' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "failed owner release left the old owner draining"; }
  rm -rf "$run_dir"
  echo "PASS: owner release waits for atomic idle drain and stops safely on failure"
}

# Releasing an owner onto the image it already runs changes nothing it serves
# and still closes every connection it holds. A wedged one is still replaced.
test_owner_release_of_the_running_image_keeps_connections() {
  local run_dir docker_log owner
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/owner-same-image.XXXXXX")"
  docker_log="$run_dir/docker.log"
  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" APP_TIER_SCENARIO=stable_owner \
    APP_TIER_RUNNING_IMAGE=sha256:same APP_TIER_RELEASE_IMAGE_ID=sha256:same \
    DEVICE_CONNECTION_SECRET=test-owner-secret PREVIEW_CONNECTION_INTERRUPT=1 HOME="$run_dir" \
    bash -c '"$1/deploy/release-device-connection.sh" testsha "$2" && "$1/deploy/release-preview-connection.sh" testsha "$2"' \
      _ "$ROOT" "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null \
    || { rm -rf "$run_dir"; fail "a release of the running image failed"; }
  ! grep -E 'release-drain|force-recreate (device|preview)-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "an owner was drained or recreated onto the image it already runs"; }

  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" APP_TIER_SCENARIO=stable_owner \
    APP_TIER_RUNNING_IMAGE=sha256:same APP_TIER_RELEASE_IMAGE_ID=sha256:same \
    APP_TIER_CURL_FAIL_MATCH=/healthz DEVICE_CONNECTION_SECRET=test-owner-secret HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1 || true
  grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "an unhealthy owner on the release's image was not replaced"; }

  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" APP_TIER_SCENARIO=stable_owner \
    APP_TIER_RUNNING_IMAGE=sha256:old APP_TIER_RELEASE_IMAGE_ID=sha256:new \
    DEVICE_CONNECTION_SECRET=test-owner-secret HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null \
    || { rm -rf "$run_dir"; fail "a release of a new image failed"; }
  grep -F 'force-recreate device-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "an owner was not moved onto a new image"; }
  # Same image, different configuration (a rotated secret, a changed env file).
  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" APP_TIER_SCENARIO=stable_owner \
    APP_TIER_RUNNING_IMAGE=sha256:same APP_TIER_RELEASE_IMAGE_ID=sha256:same \
    APP_TIER_RUNNING_CONFIG_HASH=before APP_TIER_CONFIG_HASH=after \
    DEVICE_CONNECTION_SECRET=test-owner-secret PREVIEW_CONNECTION_INTERRUPT=1 HOME="$run_dir" \
    bash -c '"$1/deploy/release-device-connection.sh" testsha "$2" && "$1/deploy/release-preview-connection.sh" testsha "$2"' \
      _ "$ROOT" "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null \
    || { rm -rf "$run_dir"; fail "a release of a changed configuration failed"; }
  for owner in device preview; do
    grep -F "force-recreate $owner-connection" "$docker_log" >/dev/null \
      || { rm -rf "$run_dir"; fail "the $owner owner kept a configuration the release changed"; }
  done

  # The deploy's own drain across its migrations is not a release to skip.
  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" APP_TIER_SCENARIO=stable_owner \
    APP_TIER_RUNNING_IMAGE=sha256:same APP_TIER_RELEASE_IMAGE_ID=sha256:same \
    DEVICE_CONNECTION_SECRET=test-owner-secret HOME="$run_dir" \
    "$ROOT/deploy/release-device-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" drain >/dev/null \
    || { rm -rf "$run_dir"; fail "the deploy's drain step failed"; }
  grep -F 'release-drain' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "the deploy's drain step was skipped as a release of the running image"; }

  # A wedged preview owner on the release's image is replaced.
  : > "$docker_log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" APP_TIER_SCENARIO=stable_owner \
    APP_TIER_RUNNING_IMAGE=sha256:same APP_TIER_RELEASE_IMAGE_ID=sha256:same \
    APP_TIER_CURL_FAIL_MATCH=/healthz PREVIEW_CONNECTION_INTERRUPT=1 HOME="$run_dir" \
    "$ROOT/deploy/release-preview-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1 || true
  grep -F 'force-recreate preview-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "an unhealthy preview owner on the release's image was not replaced"; }
  rm -rf "$run_dir"
  echo "PASS: an owner release of the image already running keeps its connections"
}

test_cloud_control_has_an_independent_drained_release() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/cloud-control-release.XXXXXX")"
  mkdir -p "$run_dir/ops" "$run_dir/bin"
  docker_log="$run_dir/docker.log"
  cat > "$run_dir/ops/deploy.env" <<EOF
BACKEND_ENV_FILE=$run_dir/backend.env
DEVICE_CONNECTION_SECRET=test-owner-secret
EOF
  : > "$run_dir/backend.env"
  cat > "$run_dir/bin/loginctl" <<'EOF'
#!/usr/bin/env bash
printf 'yes\n'
EOF
  cat > "$run_dir/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
printf 'systemctl %s\n' "$*" >> "${APP_TIER_DOCKER_LOG:?}"
if [ -n "${APP_TIER_SYSTEMCTL_FAIL_MATCH:-}" ] && [[ "$*" == *"$APP_TIER_SYSTEMCTL_FAIL_MATCH"* ]]; then exit 1; fi
EOF
  chmod +x "$run_dir/bin/loginctl" "$run_dir/bin/systemctl"

  PATH="$run_dir/bin:$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CLOUD_DEVICES=cloud-device \
    APP_TIER_SNAPSHOT_ONLINE_SEQUENCE=old-online,old-online,offline,new-online \
    APP_TIER_SNAPSHOT_COUNT_FILE="$run_dir/snapshot-count" HOME="$run_dir" \
    "$ROOT/deploy/release-cloud-control.sh" >/dev/null
  grep -F 'release-drain' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "cloud control release did not drain owner"; }
  grep -F 'systemctl --user restart cheese-cloud-control.service' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "cloud control was not restarted by its release"; }
  grep -F 'release-resume' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "cloud control release did not resume owner"; }
  [ "$(cat "$run_dir/snapshot-count")" = 4 ] \
    || { rm -rf "$run_dir"; fail "cloud control release did not wait for forwards to reconnect"; }
  ! grep -F 'test-owner-secret' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "cloud control release logged its internal secret"; }

  : > "$docker_log"
  if PATH="$run_dir/bin:$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=409 APP_TIER_CURL_DRAIN_COUNT_FILE="$run_dir/busy-count" \
    HOME="$run_dir" "$ROOT/deploy/release-cloud-control.sh" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "cloud control release proceeded while owner remained busy"
  fi
  ! grep -F 'restart cheese-cloud-control.service' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "busy cloud control release restarted its service"; }

  # DEVICE_CONNECTION_INTERRUPT=1 waives the wait, and only the wait: one look
  # at the drain, and a busy answer no longer stops the release. Without it the
  # forwards cannot be released at all while anyone is using the platform.
  : > "$docker_log"
  PATH="$run_dir/bin:$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=409 \
    APP_TIER_CURL_DRAIN_COUNT_FILE="$run_dir/interrupt-count" \
    APP_TIER_CLOUD_DEVICES=cloud-device \
    APP_TIER_SNAPSHOT_ONLINE_SEQUENCE=old-online,old-online,offline,new-online \
    APP_TIER_SNAPSHOT_COUNT_FILE="$run_dir/interrupt-snapshots" \
    DEVICE_CONNECTION_INTERRUPT=1 HOME="$run_dir" \
    "$ROOT/deploy/release-cloud-control.sh" >/dev/null
  grep -F 'restart cheese-cloud-control.service' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "interrupting cloud control release did not restart it"; }
  [ "$(cat "$run_dir/interrupt-count")" = 1 ] \
    || { rm -rf "$run_dir"; fail "interrupting release polled a busy drain more than once"; }

  # Waiving the wait is not waiving the error handling.
  : > "$docker_log"
  if PATH="$run_dir/bin:$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_CURL_DRAIN_STATUSES=500 DEVICE_CONNECTION_INTERRUPT=1 \
    HOME="$run_dir" "$ROOT/deploy/release-cloud-control.sh" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "interrupting cloud control release ignored a broken drain endpoint"
  fi
  ! grep -F 'restart cheese-cloud-control.service' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "interrupting release restarted after a broken drain"; }

  : > "$docker_log"
  if PATH="$run_dir/bin:$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_SYSTEMCTL_FAIL_MATCH=restart HOME="$run_dir" \
    "$ROOT/deploy/release-cloud-control.sh" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "cloud control release succeeded after restart failed"
  fi
  grep -F 'release-resume' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "failed cloud control release left owner draining"; }

  ! grep -Fq 'install-cloud-control.sh' "$ROOT/.github/workflows/deploy-dev.yml" \
    || { rm -rf "$run_dir"; fail "ordinary app deploy still restarts cloud control"; }
  rm -rf "$run_dir"
  echo "PASS: cloud control releases separately after atomic owner drain"
}

test_deploy_warns_when_the_session_base_will_not_survive() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/session-base.XXXXXX")"
  envf="$run_dir/backend.env"
  printf 'FRONTEND_URL=https://cheese.example\nAGENT_SESSION_API_BASE=http://172.17.0.1:18081\n' > "$envf"
  out="$(PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    BACKEND_ENV_FILE="$envf" \
    BACKEND_PORT=18081 \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" 2>&1)"
  case "$out" in
    *"AGENT_SESSION_API_BASE=http://172.17.0.1:18081 names :18081"*) ;;
    *)
      rm -rf "$run_dir"
      fail "deploy said nothing about a session address it is about to take down"
      ;;
  esac
  # And it must stay quiet for an address the deploy leaves alone — a warning
  # on every deploy is a warning nobody reads.
  printf 'FRONTEND_URL=https://cheese.example\nAGENT_SESSION_API_BASE=http://172.17.0.1:8081\n' > "$envf"
  out="$(PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    BACKEND_ENV_FILE="$envf" \
    BACKEND_PORT=18081 \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" 2>&1)"
  case "$out" in
    *AGENT_SESSION_API_BASE*)
      rm -rf "$run_dir"
      fail "deploy warned about a session address that survives it"
      ;;
  esac
  rm -rf "$run_dir"
  echo "PASS: deploy names a session address its own release would cut off"
}

test_rollout_installs_connection_route_without_recreating_api_front() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  printf '%s\n' 'events {}' 'http { include /etc/nginx/active/backend.conf; }' \
    > "$run_dir/nginx.conf"
  rollout_run "$run_dir" env >/dev/null 2>&1 || {
    rm -rf "$run_dir"
    fail "rollout could not install the connection-owner route"
  }
  grep -Fq 'location = /connector/agent' "$run_dir/nginx.conf" || {
    rm -rf "$run_dir"
    fail "api-front config still routes device sockets through the backend"
  }
  grep -Fq 'location = /api/connector/agent' "$run_dir/nginx.conf" || {
    rm -rf "$run_dir"
    fail "public device sockets still fall through the stable API ingress"
  }
  grep -Fq 'location ~ ^/api/topics/[^/]+/execution/[^/]+$' "$run_dir/nginx.conf" || {
    rm -rf "$run_dir"
    fail "public execution requests still fall through the stable API ingress"
  }
  grep -Fq 'location ~ ^/topics/[^/]+/execution/[^/]+$' "$run_dir/nginx.conf" || {
    rm -rf "$run_dir"
    fail "api-front config still routes execution requests through the backend"
  }
  awk '
    /location ~ \^\/topics\/\[\^\/\]\+\/execution\/\[\^\/\]\+\$/ { in_execution=1 }
    in_execution && /client_max_body_size 100m;/ { large_body=1 }
    in_execution && /^    }/ { exit !large_body }
    END { if (!in_execution || !large_body) exit 1 }
  ' "$run_dir/nginx.conf" || {
    rm -rf "$run_dir"
    fail "stable execution route rejects request bodies that the former backend route accepted"
  }
  grep -F 'exec cheese-api-front nginx -s reload' "$docker_log" >/dev/null || {
    rm -rf "$run_dir"
    fail "api-front did not gracefully reload the connection-owner route"
  }
  if grep -Eq '(rm|stop|up).*cheese-api-front' "$docker_log"; then
    rm -rf "$run_dir"
    fail "business rollout recreated api-front while installing the route"
  fi
  rm -rf "$run_dir"
  echo "PASS: rollout installs the owner route with a graceful api-front reload"
}

# Every api-front reload retires the workers carrying the box's device links,
# terminals and tunnels, so a deploy reloads it only when what api-front would
# load differs from what it loaded last: not for a comment, and always for a
# config an earlier deploy wrote to disk but never got to load.
test_api_front_reloads_only_for_a_new_config() {
  local run_dir docker_log reloads
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  # The running config differs from the release's in a comment only.
  { printf '# a note that changes nothing\n'; cat "$ROOT/deploy/llm-tunnel/nginx.conf"; } > "$run_dir/nginx.conf"
  rollout_run "$run_dir" env >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "deploy failed"; }
  ! grep -Fx 'exec cheese-api-front nginx -s reload' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "api-front was reloaded for a comment"; }
  cmp -s "$ROOT/deploy/llm-tunnel/nginx.conf" "$run_dir/nginx.conf" \
    || { rm -rf "$run_dir"; fail "the release's api-front config was not installed"; }

  # A deploy stopped after writing the files and before the reload: what is on
  # disk is current, what api-front runs is not.
  printf 'what an interrupted deploy left loaded\n' > "$run_dir/active/.api-front-loaded"
  : > "$docker_log"
  rollout_run "$run_dir" env >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "deploy failed"; }
  reloads="$(grep -cFx 'exec cheese-api-front nginx -s reload' "$docker_log" || true)"
  [ "$reloads" = 1 ] \
    || { rm -rf "$run_dir"; fail "api-front was reloaded $reloads times for a config it had not loaded, not once"; }

  # Loaded now: the next release with the same config leaves it alone.
  : > "$docker_log"
  rollout_run "$run_dir" env >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "deploy failed"; }
  ! grep -Fx 'exec cheese-api-front nginx -s reload' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "api-front was reloaded again for the config it had just loaded"; }

  # Something else wrote and loaded another config since (a release of an older
  # commit, tls-renew.sh): the record no longer says what api-front runs.
  sed -i.bak 's/^worker_shutdown_timeout .*/worker_shutdown_timeout 30s;/' "$run_dir/nginx.conf"
  : > "$docker_log"
  rollout_run "$run_dir" env >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; rm -rf "$run_dir"; fail "deploy failed"; }
  grep -Fx 'exec cheese-api-front nginx -s reload' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "api-front kept a config another writer loaded, because the record was stale"; }
  rm -rf "$run_dir"
  echo "PASS: api-front is reloaded only for a config it has not loaded"
}

# The certificate renewal re-renders the front door. Without the preview
# owner's port it would send the preview tunnels back to the rolling frontend
# until the next deploy put them back.
test_certificate_renewal_keeps_the_preview_owner_route() {
  local run_dir docker_log domain=cheese.example
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 18086 18080 18087 >/dev/null
  mkdir -p "$run_dir/tls/letsencrypt/live/$domain"
  : > "$run_dir/tls/cloudflare.ini"
  printf 'certificate\n' > "$run_dir/tls/letsencrypt/live/$domain/fullchain.pem"
  printf 'key\n' > "$run_dir/tls/letsencrypt/live/$domain/privkey.pem"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" TLS_STATE_DIR="$run_dir/tls" HOME="$run_dir" \
    bash "$ROOT/deploy/llm-tunnel/tls-renew.sh" "$run_dir/active" 18080 "$domain" >"$run_dir/renew.log" 2>&1 \
    || { cat "$run_dir/renew.log"; rm -rf "$run_dir"; fail "certificate renewal failed"; }
  grep -Fx 'exec cheese-api-front nginx -s reload' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "the renewal did not reload api-front"; }
  grep -F 'proxy_pass http://127.0.0.1:18087;' "$run_dir/active/sites-frontend.conf" >/dev/null \
    || { rm -rf "$run_dir"; fail "the renewal moved the front door's preview tunnel off the owner"; }
  rm -rf "$run_dir"
  echo "PASS: a certificate renewal keeps the front door's preview tunnel on the owner"
}

test_deploy_retains_ci_service_images() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/ci-service-images.XXXXXX")"
  docker_log="$run_dir/docker.log"
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null

  grep -Fq 'image inspect mirror.gcr.io/paradedb/paradedb' "$docker_log" || \
    fail "deploy never checked whether the CI postgres image is present"
  grep -Fq \
    'create --name cheese-ci-postgres-image-retainer --label com.cheese.image-retainer=ci-postgres --entrypoint /bin/true mirror.gcr.io/paradedb/paradedb' \
    "$docker_log" || fail "deploy did not retain the CI postgres image"
  grep -Fq \
    'create --name cheese-ci-redis-image-retainer --label com.cheese.image-retainer=ci-redis --entrypoint /bin/true mirror.gcr.io/valkey/valkey' \
    "$docker_log" || fail "deploy did not retain the CI redis image"

  retain_line="$(grep -nF 'create --name cheese-ci-redis-image-retainer' \
    "$docker_log" | tail -n 1 | cut -d: -f1)"
  prune_line="$(grep -nF 'image prune -af' "$docker_log" | head -n 1 | cut -d: -f1)"
  [ -n "$retain_line" ] && [ -n "$prune_line" ] && \
    [ "$retain_line" -lt "$prune_line" ] || \
    fail "CI service images were not retained before pruning"

  rm -rf "$run_dir"
  echo "PASS: deploy retains CI service-container images before pruning"
}

test_deploy_delivers_private_executor() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/private-executor.XXXXXX")"
  docker_log="$run_dir/docker.log"
  mkdir -p "$run_dir/images"
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_IMAGE_STORE="$run_dir/images" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null

  grep -Fqx 'pull ghcr.io/sageseekersociety/cheese/private-executor:testsha' \
    "$docker_log" || fail "deploy did not pull this commit's private executor"
  # The backend starts a private chat with `docker run <local name>`, so that
  # name, not the registry one, has to be there once the deploy has pruned.
  PATH="$FAKE_BIN:$PATH" APP_TIER_IMAGE_STORE="$run_dir/images" \
    docker image inspect cheese-private-executor:9.9.9 || \
    fail "the executor's local name did not survive the deploy's image prune"

  rm -rf "$run_dir"
  echo "PASS: deploy delivers the private executor under its local name"
}

test_private_executor_failure_does_not_fail_deploy() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/private-executor-missing.XXXXXX")"
  docker_log="$run_dir/docker.log"
  if ! PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_DOCKER_FAIL_MATCH='pull ghcr.io/sageseekersociety/cheese/private-executor' \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1; then
    rm -rf "$run_dir"
    fail "an unavailable private executor failed the deploy"
  fi
  grep -F 'WARNING: private-executor image unavailable' "$run_dir/out" >/dev/null \
    || { rm -rf "$run_dir"; fail "an unavailable private executor left no warning"; }
  rm -rf "$run_dir"
  echo "PASS: an unavailable private executor only warns"
}

test_local_app_images_skip_registry_pull() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/local-app-images.XXXXXX")"
  docker_log="$run_dir/docker.log"
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    BACKEND_IMAGE=repo/backend:testsha \
    FRONTEND_IMAGE=repo/frontend:testsha COLLAB_IMAGE=repo/collab:testsha \
    DEPLOY_APP_IMAGE_SOURCE=local \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null

  grep -Fqx 'image inspect repo/backend:testsha' "$docker_log" || \
    fail "local deploy did not verify the backend image"
  grep -Fqx 'image inspect repo/frontend:testsha' "$docker_log" || \
    fail "local deploy did not verify the frontend image"
  if grep -F 'pull backend frontend' "$docker_log" >/dev/null; then
    fail "local deploy unexpectedly pulled app images from the registry"
  fi
  rm -rf "$run_dir"
  echo "PASS: local deployment verifies images without registry pull"
}

test_local_app_images_must_exist() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/local-app-missing.XXXXXX")"
  docker_log="$run_dir/docker.log"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=local_image_missing \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    BACKEND_IMAGE=repo/backend:testsha \
    FRONTEND_IMAGE=repo/frontend:testsha COLLAB_IMAGE=repo/collab:testsha \
    DEPLOY_APP_IMAGE_SOURCE=local \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "local deploy continued with a missing frontend image"
  fi
  if grep -F 'run --rm backend' "$docker_log" >/dev/null; then
    rm -rf "$run_dir"
    fail "local deploy reached migration after image verification failed"
  fi
  rm -rf "$run_dir"
  echo "PASS: local deployment rejects a missing image before migration"
}

test_pull_retries_transient_failure() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/pull-retry.XXXXXX")"
  docker_log="$run_dir/docker.log"
  # Five dropped connections in a row: a burst, well inside the budget.
  if ! PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_PULL_FAIL_COUNT=5 \
    APP_TIER_PULL_COUNTER="$run_dir/pull.count" \
    DEPLOY_PULL_BACKOFF_CAP_SECONDS=0 \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1; then
    cat "$run_dir/out" >&2
    rm -rf "$run_dir"
    fail "deploy gave up on a pull that would have succeeded within the budget"
  fi

  attempts="$(grep -c 'pull backend frontend$' "$docker_log" || true)"
  [ "$attempts" = 6 ] || fail "expected 6 pull attempts, saw $attempts"
  grep -q 'image pull: succeeded on attempt 6' "$run_dir/out" || \
    fail "the retry that finally worked was not reported"

  # A dropped connection is no reason to free space: pruning between attempts
  # would delete whatever an earlier attempt had already finished pulling.
  first_prune="$(grep -nF 'image prune -af' "$docker_log" | head -n 1 | cut -d: -f1)"
  last_pull="$(grep -n 'pull backend frontend$' "$docker_log" | tail -n 1 | cut -d: -f1)"
  [ -z "$first_prune" ] || [ "$first_prune" -gt "$last_pull" ] || \
    fail "a pull that failed on the network pruned images before the retry"
  rm -rf "$run_dir"
  echo "PASS: a burst of failed pulls is retried within the budget, keeping what was pulled"
}

test_pull_on_a_full_disk_reclaims_before_the_retry() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/pull-disk-full.XXXXXX")"
  docker_log="$run_dir/docker.log"
  if ! PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_PULL_FAIL_COUNT=2 \
    APP_TIER_PULL_COUNTER="$run_dir/pull.count" \
    APP_TIER_PULL_FAIL_MESSAGE='failed to register layer: write /var/lib/docker/overlay2/x/diff/y: no space left on device' \
    DEPLOY_PULL_BACKOFF_CAP_SECONDS=0 \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1; then
    cat "$run_dir/out" >&2
    rm -rf "$run_dir"
    fail "deploy gave up on a pull that a reclaim would have let through"
  fi

  first_prune="$(grep -nF 'image prune -af' "$docker_log" | head -n 1 | cut -d: -f1)"
  last_pull="$(grep -n 'pull backend frontend$' "$docker_log" | tail -n 1 | cut -d: -f1)"
  [ -n "$first_prune" ] && [ "$first_prune" -lt "$last_pull" ] || \
    fail "a pull that failed on a full disk was retried without a reclaim in between"
  rm -rf "$run_dir"
  echo "PASS: a pull that failed on a full disk reclaims before the retry"
}

test_exhausted_pull_retries_still_fail_the_deploy() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/pull-exhausted.XXXXXX")"
  docker_log="$run_dir/docker.log"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_PULL_FAIL_COUNT=99 \
    APP_TIER_PULL_COUNTER="$run_dir/pull.count" \
    DEPLOY_PULL_BUDGET_SECONDS=3 \
    DEPLOY_PULL_BACKOFF_CAP_SECONDS=1 \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1; then
    rm -rf "$run_dir"
    fail "deploy reported success after every pull attempt failed"
  fi

  attempts="$(grep -c 'pull backend frontend$' "$docker_log" || true)"
  [ "$attempts" -gt 1 ] || fail "the pull was not retried before the budget ran out ($attempts attempt)"
  summary="$(grep -o 'image pull failed after [0-9]* attempts in [0-9]*s, the pulls. 3s budget spent' "$run_dir/out" || true)"
  [ -n "$summary" ] || fail "the failure did not name the attempts, the time spent and the budget"
  reported="$(printf '%s\n' "$summary" | sed 's/.*after \([0-9]*\) attempts in \([0-9]*\)s.*/\1 \2/')"
  [ "${reported% *}" = "$attempts" ] || fail "the failure reported ${reported% *} attempts, the log shows $attempts"
  [ "${reported#* }" -le 3 ] || fail "the pulls went on for ${reported#* }s, past the 3s budget"
  grep -q 'attempt 1 exited 1; attempt 2 exited 1' "$run_dir/out" || \
    fail "the failure did not report each attempt's outcome"
  if grep -F 'run --rm backend' "$docker_log" >/dev/null; then
    fail "deploy proceeded to migration after the pull was given up on"
  fi
  grep -F 'image prune -af' "$docker_log" >/dev/null || \
    fail "the abandoned deploy left its pulled layers on disk"
  rm -rf "$run_dir"
  echo "PASS: pulls stop when the budget is spent, still abort the deploy, and still reclaim"
}

test_ghcr_login_retries_dropped_connections() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/login-retry.XXXXXX")"
  docker_log="$run_dir/docker.log"
  token='ghs_fake_login_token_4f1d'
  # Five resets in a row, as on dev deploy run 40138894358, then ghcr answers.
  if ! PATH="$FAKE_BIN:$PATH" \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_GHCR_TOKEN="$token" \
    APP_TIER_LOGIN_FAIL_COUNT=5 \
    APP_TIER_LOGIN_COUNTER="$run_dir/login.count" \
    APP_TIER_LOGIN_SECRET_SEEN="$run_dir/login.secret" \
    GHCR_USERNAME=owner \
    GHCR_TOKEN="$token" \
    DEPLOY_PULL_BACKOFF_CAP_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/ghcr-login.sh" > "$run_dir/out" 2>&1; then
    cat "$run_dir/out" >&2
    rm -rf "$run_dir"
    fail "the ghcr login gave up on a registry that answered within the budget"
  fi

  # docker got the token on stdin on every attempt, and it shows up neither in
  # what the login printed nor on any docker command line.
  attempts="$(grep -cxF "$token" "$run_dir/login.secret" || true)"
  [ "$attempts" = 6 ] || fail "expected the token on stdin for 6 login attempts, saw $attempts"
  grep -q 'ghcr login: succeeded on attempt 6' "$run_dir/out" || \
    fail "the login that finally worked was not reported"
  if grep -qF "$token" "$run_dir/out" "$docker_log"; then
    fail "the ghcr token appeared in the login's output or on a docker command line"
  fi
  rm -rf "$run_dir"
  echo "PASS: a ghcr login that hits dropped connections is retried within the budget, token on stdin only"
}

test_ghcr_login_refused_is_not_retried() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/login-refused.XXXXXX")"
  # The budget is short so that, were the refusal retried, the case would fail
  # in seconds rather than spin for ten minutes.
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_GHCR_TOKEN=the-token-ghcr-accepts \
    APP_TIER_LOGIN_SECRET_SEEN="$run_dir/login.secret" \
    GHCR_USERNAME=owner \
    GHCR_TOKEN=a-token-ghcr-refuses \
    DEPLOY_PULL_BUDGET_SECONDS=5 \
    DEPLOY_PULL_BACKOFF_CAP_SECONDS=1 \
    HOME="$run_dir" \
    "$ROOT/deploy/ghcr-login.sh" > "$run_dir/out" 2>&1; then
    rm -rf "$run_dir"
    fail "the ghcr login succeeded although ghcr refused its credential"
  fi

  attempts=0
  [ ! -f "$run_dir/login.secret" ] || attempts="$(wc -l < "$run_dir/login.secret" | tr -d ' ')"
  [ "$attempts" = 1 ] || fail "a refused credential was tried $attempts times, expected once"
  grep -q 'ghcr login was refused by the registry' "$run_dir/out" || \
    fail "the failure did not say the registry refused the credential"
  rm -rf "$run_dir"
  echo "PASS: a ghcr login refused for its credential fails at once"
}

test_pull_refused_is_not_retried() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/pull-refused.XXXXXX")"
  docker_log="$run_dir/docker.log"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_PULL_FAIL_COUNT=99 \
    APP_TIER_PULL_COUNTER="$run_dir/pull.count" \
    APP_TIER_PULL_FAIL_MESSAGE='Error response from daemon: Head "https://ghcr.io/v2/x/manifests/testsha": denied: denied' \
    DEPLOY_PULL_BUDGET_SECONDS=5 \
    DEPLOY_PULL_BACKOFF_CAP_SECONDS=1 \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1; then
    rm -rf "$run_dir"
    fail "deploy succeeded although ghcr refused the pull"
  fi

  attempts="$(cat "$run_dir/pull.count")"
  [ "$attempts" = 1 ] || fail "a refused pull was tried $attempts times, expected once"
  grep -q 'image pull was refused by the registry' "$run_dir/out" || \
    fail "the failure did not say the registry refused the pull"
  rm -rf "$run_dir"
  echo "PASS: a pull the registry refuses fails the deploy at once"
}

test_failed_deploy_reclaims_disk() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/failed-reclaim.XXXXXX")"
  docker_log="$run_dir/docker.log"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=frontend_absent \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1; then
    rm -rf "$run_dir"
    fail "deploy succeeded with no frontend container"
  fi
  grep -F 'image prune -af' "$docker_log" >/dev/null || \
    fail "a deploy that failed its health check reclaimed nothing"
  grep -q 'reclaiming disk on the way out' "$run_dir/out" || \
    fail "the exit-path reclaim did not announce itself in the deploy log"
  rm -rf "$run_dir"
  echo "PASS: a deploy failing after the pull still reclaims disk on the way out"
}

test_deploy_logs_disk_watermarks() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/disk-watermark.XXXXXX")"
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1

  for when in 'before pull' 'after pull' 'after reclaim, successful deploy'; do
    grep -q "disk ($when) .* avail=.*GiB use=" "$run_dir/out" || \
      fail "no disk watermark logged for: $when"
  done
  rm -rf "$run_dir"
  echo "PASS: deploy logs disk watermarks around the pull and after reclaim"
}

test_rollback_restores_exact_previous_images() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/exact-rollback.XXXXXX")"
  docker_log="$run_dir/docker.log"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=rollback \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_PREVIOUS_RELEASE=prevrelease \
    APP_RELEASE=thisrelease \
    BACKEND_IMAGE=repo/backend:testsha \
    FRONTEND_IMAGE=repo/frontend:testsha COLLAB_IMAGE=repo/collab:testsha \
    DEPLOY_APP_IMAGE_SOURCE=local \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "rollback scenario unexpectedly passed health checks"
  fi
  # An image named outright is not the release's, so it does not claim to be.
  grep -Fqx \
    'compose-up-env BACKEND_IMAGE=repo/backend:testsha FRONTEND_IMAGE=repo/frontend:testsha IMAGE_TAG=testsha APP_RELEASE=' \
    "$docker_log" || fail "an image named by BACKEND_IMAGE was started as the release"
  grep -Fqx \
    'compose-up-env BACKEND_IMAGE=repo/backend:oldsha FRONTEND_IMAGE=repo/frontend:oldsha IMAGE_TAG=oldsha APP_RELEASE=prevrelease' \
    "$docker_log" || fail "rollback did not restore the exact previous images and the release they ran as"
  rm -rf "$run_dir"
  echo "PASS: rollback restores exact previous image references"
}

# Without app-router the frontend waits on the backend's healthcheck (/readyz),
# so a build that never becomes ready fails `compose up` itself. That must
# still end in the rollback, not in an exit that leaves the build in place.
test_unready_backend_without_slots_still_rolls_back() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/unready-rollback.XXXXXX")"
  docker_log="$run_dir/docker.log"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=rollback \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_DOCKER_FAIL_SUFFIX='up -d backend frontend' \
    BACKEND_IMAGE=repo/backend:testsha \
    FRONTEND_IMAGE=repo/frontend:testsha COLLAB_IMAGE=repo/collab:testsha \
    DEPLOY_APP_IMAGE_SOURCE=local \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >"$run_dir/release.log" 2>&1; then
    rm -rf "$run_dir"
    fail "a release whose compose up failed was reported as deployed"
  fi
  grep -Fq 'HEALTH CHECK FAILED' "$run_dir/release.log" \
    || { cat "$run_dir/release.log"; fail "a failed compose up skipped the health check"; }
  grep -Fqx \
    'compose-up-env BACKEND_IMAGE=repo/backend:oldsha FRONTEND_IMAGE=repo/frontend:oldsha IMAGE_TAG=oldsha APP_RELEASE=' \
    "$docker_log" || fail "a failed compose up did not roll back to the previous images"
  # Only the release's own `up` fails (FAIL_SUFFIX); the rollback's longer one
  # must have been issued and, in the fake, succeeded.
  grep -q 'up -d backend frontend collab$' "$docker_log" \
    || fail "the rollback after a failed compose up never brought the previous release up"
  ! grep -q 'up -d --no-deps collab' "$docker_log" \
    || fail "collab was replaced although the backend never came up"
  rm -rf "$run_dir"
  echo "PASS: an unready backend on a box without slots still rolls back"
}

# Every commit's images carry that commit's tag, so naming collab, the two
# renderers and the forge event relay by tag recreated them on every release,
# even when build.yml had only promoted an unchanged image or rebuilt identical
# inputs. Each recreate closes every open document, every page a renderer
# holds and every deployment's event socket. A release whose images carry the
# content already running must leave compose nothing to recreate: the model
# compose builds for those services is the same as the last release's.
test_unchanged_services_keep_their_containers() {
  local run_dir real_docker sha content code hashes=() service chosen=() variable
  real_docker="${APP_TIER_REAL_DOCKER_BIN:-}"
  if [ -z "$real_docker" ] || ! "$real_docker" compose version >/dev/null 2>&1; then
    fail "this case needs a real docker CLI with compose (none found on PATH)"
  fi
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/unchanged-services.XXXXXX")"
  # Two releases of the same content, then one whose images and relay code differ.
  for release in same:aaaaaaa same:bbbbbbb changed:ccccccc; do
    sha="${release#*:}"
    content=old-layers code=running-code
    [ "${release%%:*}" = same ] || content=new-layers code=release-code
    : > "$run_dir/docker.log"
    PATH="$FAKE_BIN:$PATH" APP_TIER_SCENARIO=healthy APP_TIER_MAIN_SHA="$sha" \
      APP_TIER_DOCKER_LOG="$run_dir/docker.log" \
      APP_TIER_RUNNING_SERVICES="collab browser-render office-render forge-events" \
      APP_TIER_PULLED_CONTENT="$content" APP_TIER_RELEASE_CODE="$code" \
      DEPLOY_HEALTH_ATTEMPTS=1 DEPLOY_HEALTH_INTERVAL_SECONDS=0 HOME="$run_dir" \
      "$ROOT/deploy/deploy-docker.sh" "$sha" "$ROOT/deploy/compose/docker-compose.base.yml" \
      > "$run_dir/release.log" 2>&1 \
      || { cat "$run_dir/release.log"; rm -rf "$run_dir"; fail "release $sha failed"; }
    # What compose would build each service from, given the references the
    # release chose: compose recreates a container exactly when this changes.
    mapfile -t chosen < <(sed -n 's/^[a-z-]*-up-env //p' "$run_dir/docker.log")
    # The checks outside a deploy judge the digests by what it recorded.
    for variable in COLLAB_IMAGE FORGE_EVENTS_IMAGE; do
      grep -Fx "$(printf '%s\n' "${chosen[@]}" | grep "^$variable=" | tail -n 1)" "$run_dir/ops/app-tier-pins" >/dev/null \
        || { rm -rf "$run_dir"; fail "release $sha did not record the $variable it chose"; }
    done
    hashes+=("$(env -i PATH="$PATH" HOME="$run_dir" IMAGE_TAG="$sha" \
      FORGE_EVENTS_ENV_FILE="$FORGE_EVENTS_ENV_FILE" BACKEND_ENV_FILE="$BACKEND_ENV_FILE" \
      "${chosen[@]}" \
      "$real_docker" compose -f "$ROOT/deploy/compose/docker-compose.base.yml" \
        -f "$ROOT/deploy/forge-events/compose.yml" -p cheese \
        config --hash 'collab,browser-render,office-render,forge-events' 2>&1 | sort)")
  done
  [ "${hashes[0]}" = "${hashes[1]}" ] || {
    printf 'first:\n%s\nsecond:\n%s\n' "${hashes[0]}" "${hashes[1]}" >&2
    rm -rf "$run_dir"
    fail "a release of unchanged content gave compose a different model, which recreates the containers"
  }
  for service in collab browser-render office-render forge-events; do
    [ "$(printf '%s\n' "${hashes[1]}" | grep "^$service ")" != "$(printf '%s\n' "${hashes[2]}" | grep "^$service ")" ] || {
      rm -rf "$run_dir"
      fail "$service kept its container although its image changed"
    }
  done
  rm -rf "$run_dir"
  echo "PASS: unchanged collab, renderers and event relay keep their containers across releases"
}

# A release that fails its health check returns collab to the image that ran
# before it, not the one it pinned for itself.
test_rollback_returns_collab_to_the_previous_image() {
  local run_dir
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/collab-rollback.XXXXXX")"
  if PATH="$FAKE_BIN:$PATH" APP_TIER_SCENARIO=rollback APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$run_dir/docker.log" APP_TIER_RUNNING_SERVICES=collab \
    DEPLOY_HEALTH_ATTEMPTS=1 DEPLOY_HEALTH_INTERVAL_SECONDS=0 HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha "$ROOT/deploy/compose/docker-compose.base.yml" \
    > "$run_dir/release.log" 2>&1; then
    rm -rf "$run_dir"
    fail "rollback scenario unexpectedly passed health checks"
  fi
  grep -Fqx 'collab-up-env COLLAB_IMAGE=ghcr.io/sageseekersociety/cheese/collab@sha256:pulled' "$run_dir/docker.log" \
    || { cat "$run_dir/release.log"; rm -rf "$run_dir"; fail "the release did not start collab on the image it pulled"; }
  [ "$(grep '^collab-up-env ' "$run_dir/docker.log" | tail -n 1)" \
    = 'collab-up-env COLLAB_IMAGE=ghcr.io/sageseekersociety/cheese/collab@sha256:previous' ] \
    || { rm -rf "$run_dir"; fail "the rollback left collab on the failed release's image"; }
  rm -rf "$run_dir"
  echo "PASS: a rollback returns collab to the image that ran before the release"
}

# A box with an app-router (ACTIVE_BACKEND_DIR) releases by rollout: the idle
# slot comes up beside the serving one, app-router is switched once, the old
# slot drains and is stopped. These tests pin that order and the failures that
# must leave the serving slot alone.
rollout_run() {
  local run_dir="$1"
  shift
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$run_dir/docker.log" \
    ACTIVE_BACKEND_DIR="$run_dir/active" \
    API_FRONT_CONF="$run_dir/nginx.conf" \
    BACKEND_PORT=18081 \
    BACKEND_PORT_NEXT=18082 \
    DEPLOY_DRAIN_SECONDS=31 \
    DEPLOY_BACKEND_START_TIMEOUT=3 \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$@" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml"
}

new_rollout_run_dir() {
  mkdir -p "$ROOT/.tmp"
  local dir
  dir="$(mktemp -d "$ROOT/.tmp/rollout.XXXXXX")"
  mkdir -p "$dir/active"
  printf 'upstream backend_active { server 127.0.0.1:18081; }\n' > "$dir/active/backend.conf"
  cp "$ROOT/deploy/llm-tunnel/nginx.conf" "$dir/nginx.conf"
  # Seed the preview routing file the way deploy-docker.sh will (owner mode), so
  # a rollout here sees it already current and does NOT reload api-front for it.
  # That is what keeps these tests about the backend/ingress switches; the
  # one-time reload a first cutover performs is exercised by its own test.
  bash "$ROOT/deploy/llm-tunnel/configure-preview.sh" owner "$dir/active" 18087 >/dev/null
  : > "$dir/docker.log"
  printf '%s' "$dir"
}

# Nth log line matching a pattern (1-based), or "" if there is no Nth match.
nth_log_line() { grep -n -- "$2" "$1" 2>/dev/null | sed -n "${3}p" | cut -d: -f1 || true; }
last_log_line() { grep -n -- "$2" "$1" 2>/dev/null | tail -n 1 | cut -d: -f1 || true; }

test_rollout_recovers_after_forge_stops_backend() {
  local run_dir mode
  for mode in first retry; do
    run_dir="$(new_rollout_run_dir)"
    mkdir -p "$run_dir/apphome/forge-migration"
    if [ "$mode" = retry ]; then
      touch "$run_dir/apphome/forge-migration/cutover-pending"
    fi
    rollout_run "$run_dir" env APPHOME_HOST_PATH="$run_dir/apphome" \
      APP_TIER_FORGE_CHECK="$([ "$mode" = first ] && echo 2 || echo 0)" \
      APP_TIER_ROUTER_NEEDS_BACKEND=1 >"$run_dir/release.log" 2>&1 \
      || { cat "$run_dir/release.log"; fail "forge $mode could not recover the stopped backend"; }
    grep -F ':18085/readyz' "$run_dir/docker.log" >/dev/null \
      || fail "recovered release never checked the routed backend"
    [ ! -f "$run_dir/apphome/forge-migration/cutover-pending" ] \
      || fail "recovered release retained the cutover guard"
    rm -rf "$run_dir"
    echo "PASS: routed backend recovers after forge $mode"
  done
}

# One release, one switch: the idle backend slot comes up and answers /readyz,
# app-router is pointed at it in a single reload, the backend traffic left hands
# its work over and drains, and only then is it stopped. Every reload retires a
# generation of app-router workers, and with them the WebSockets they carry, so
# the number of reloads is the number of times a room's connection moves.
# A box with an api pool (backend/app/serve.py) waits for the new slot's pool
# before the switch and points app-router's reads at it, its realtime process
# the backup; a slot whose image runs no pool — a rollback to a release from
# before it — is not waited for, and its reads go to the realtime process.
test_rollout_with_an_api_pool() {
  local run_dir docker_log ready switched
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env API_POOL_WORKERS=3 \
    APP_TIER_BACKEND_CMD='["python","-m","app.serve"]' >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "a rollout with an api pool did not succeed"; }
  ready="$(log_line "$docker_log" ':18092/readyz')"
  switched="$(log_line "$docker_log" 'exec cheese-app-router nginx -s reload')"
  [ -n "$ready" ] && [ "$ready" -lt "$switched" ] \
    || fail "app-router was switched before the new slot's api pool answered /readyz"
  grep -Fqx 'upstream backend_pool_active { server 127.0.0.1:18092; server 127.0.0.1:18082 backup; }' \
    "$run_dir/active/backend-pool.conf" \
    || fail "reads do not go to the new slot's pool: $(cat "$run_dir/active/backend-pool.conf")"

  : > "$docker_log"
  rollout_run "$run_dir" env API_POOL_WORKERS=3 >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "a rollout to an image without a pool failed"; }
  ! grep -q ':18091/readyz' "$docker_log" \
    || fail "the rollout waited for a pool the image does not run"
  grep -q 'runs no api pool' "$run_dir/release.log" \
    || fail "the rollout did not say the slot runs no pool"
  grep -Fqx 'upstream backend_active { server 127.0.0.1:18081; }' "$run_dir/active/backend.conf" \
    || fail "the release to an image without a pool did not switch"

  : > "$docker_log"
  rollout_run "$run_dir" env >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "a rollout without a pool failed"; }
  grep -Fqx 'upstream backend_pool_active { server 127.0.0.1:18082; }' "$run_dir/active/backend-pool.conf" \
    || fail "a box without a pool still sends reads elsewhere: $(cat "$run_dir/active/backend-pool.conf")"
  rm -rf "$run_dir"
  echo "PASS: an api pool is waited for when the image runs one, and reads follow it"
}

test_rollout_switches_once() {
  local run_dir docker_log up healthy reload drain stopped removed reloads
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_BACKEND_CONTAINER=blue-container >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "rollout deploy did not succeed"; }
  up="$(log_line "$docker_log" 'up -d --no-deps --force-recreate backend-b')"
  healthy="$(log_line "$docker_log" ':18082/readyz')"
  reload="$(log_line "$docker_log" 'exec cheese-app-router nginx -s reload')"
  reloads="$(grep -c 'exec cheese-app-router nginx -s reload' "$docker_log" || true)"
  drain="$(log_line "$docker_log" 'sleep 31')"
  stopped="$(log_line "$docker_log" 'stop --time 60 blue-container')"
  removed="$(log_line "$docker_log" ' rm -f backend$')"
  [ -n "$up" ] || fail "the idle backend slot was never started"
  [ "$reloads" = 1 ] || fail "a release reloaded app-router $reloads times, not once"
  [ "$up" -lt "$healthy" ] && [ "$healthy" -lt "$reload" ] \
    || fail "app-router was pointed at the new backend before it answered /readyz"
  [ "$reload" -lt "$drain" ] && [ -n "$stopped" ] && [ "$drain" -lt "$stopped" ] \
    || fail "the old backend was stopped before traffic left it and drained"
  [ -n "$removed" ] && [ "$stopped" -lt "$removed" ] \
    || fail "the old backend was removed without being stopped first, which cuts its handover off"
  ! grep -q -- '--force-recreate backend$' "$docker_log" \
    || fail "the serving backend slot was recreated"
  grep -Fqx 'upstream backend_active { server 127.0.0.1:18082; }' "$run_dir/active/backend.conf" \
    || fail "app-router does not point at the new backend: $(cat "$run_dir/active/backend.conf")"
  ! grep -q 'exec cheese-api-front nginx -s reload' "$docker_log" \
    || fail "business rollout retired the persistent ingress workers"

  # The next release goes back into the first slot, the same way.
  : > "$docker_log"
  rollout_run "$run_dir" env >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "the release after it did not succeed"; }
  grep -q -- 'up -d --no-deps --force-recreate backend$' "$docker_log" \
    || fail "the second release did not start the first slot"
  grep -q ':18081/readyz' "$docker_log" || fail "the first slot was not health-checked"
  [ "$(grep -c 'exec cheese-app-router nginx -s reload' "$docker_log")" = 1 ] \
    || fail "the second release did not switch exactly once"
  grep -q ' rm -f backend-b$' "$docker_log" || fail "the second release left the old slot behind"
  grep -Fqx 'upstream backend_active { server 127.0.0.1:18081; }' "$run_dir/active/backend.conf" \
    || fail "app-router does not point back at the first slot: $(cat "$run_dir/active/backend.conf")"
  rm -rf "$run_dir"
  echo "PASS: a release moves backend traffic once, alternating between the two slots"
}

# The running work moves with the traffic, once. The backend traffic has left is
# told to hand it over (SIGUSR1) and keeps answering through the drain; the
# backend traffic moved to holds every new turn until then.
test_rollout_moves_running_work_with_the_traffic() {
  local run_dir docker_log reload told drain
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_BACKEND_CONTAINER=blue-container >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "rollout deploy did not succeed"; }
  reload="$(log_line "$docker_log" 'exec cheese-app-router nginx -s reload')"
  told="$(log_line "$docker_log" 'kill --signal USR1 blue-container')"
  drain="$(log_line "$docker_log" 'sleep 31')"
  [ -n "$told" ] || fail "the backend traffic left was never told to hand its work over"
  [ "$(grep -c 'kill --signal USR1' "$docker_log")" = 1 ] || fail "a release handed the running work over more than once"
  [ "$reload" -lt "$told" ] && [ "$told" -lt "$drain" ] \
    || fail "the old backend was told to hand over before traffic left it, or only after the drain"
  rm -rf "$run_dir"
  echo "PASS: the backend traffic leaves hands its running work over, once"
}

# SIGUSR1's default action ends a process. A backend from before the handler —
# the one running when the first release carrying it goes out — must not get it.
test_rollout_never_signals_a_backend_without_the_handler() {
  local run_dir
  run_dir="$(new_rollout_run_dir)"
  rollout_run "$run_dir" env APP_TIER_BACKEND_CONTAINER=blue-container APP_TIER_NO_HANDOVER_SIGNAL=1 \
    >"$run_dir/release.log" 2>&1 || { cat "$run_dir/release.log"; fail "rollout deploy did not succeed"; }
  grep -q 'handover-signal-probe' "$run_dir/docker.log" || fail "the rollout never asked whether the backend takes the signal"
  ! grep -q 'kill --signal USR1' "$run_dir/docker.log" \
    || fail "a backend that does not catch SIGUSR1 was sent it, which kills it"
  grep -q 'stop --time 60 blue-container' "$run_dir/docker.log" || fail "the old backend was not stopped"
  rm -rf "$run_dir"
  echo "PASS: a backend without the handover handler is left to hand over at stop"
}

# A model stream relayed through /llm/v1 cannot move to the next backend, and
# stopping the backend relaying it ends it. The backend traffic has left is
# asked how many it still relays, on its own port, and is stopped only once
# none are left; the old frontend does not wait for that.
test_rollout_stops_the_old_backend_after_its_model_streams() {
  local run_dir docker_log drain first_probe last_probe stopped
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_BACKEND_CONTAINER=blue-container \
    APP_TIER_MODEL_STREAMS=2,1,0 APP_TIER_MODEL_STREAMS_COUNT_FILE="$run_dir/streams" \
    DEPLOY_MODEL_STREAM_POLL_SECONDS=7 >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "rollout deploy did not succeed"; }
  drain="$(log_line "$docker_log" 'sleep 31')"
  first_probe="$(log_line "$docker_log" ':18081/_internal/llm/streams')"
  last_probe="$(last_log_line "$docker_log" ':18081/_internal/llm/streams')"
  stopped="$(log_line "$docker_log" 'stop --time 60 blue-container')"
  [ -n "$first_probe" ] || fail "the old backend was never asked about its model streams on its own port"
  [ "$(grep -c '/_internal/llm/streams' "$docker_log")" = 3 ] \
    || fail "the wait did not ask until the old backend had no model stream left"
  [ "$drain" -lt "$first_probe" ] && [ -n "$stopped" ] && [ "$last_probe" -lt "$stopped" ] \
    || fail "the old backend was stopped while it still relayed model streams"
  grep -Fq 'finished its model streams after 14s' "$run_dir/release.log" \
    || { cat "$run_dir/release.log"; fail "the release does not say how long the model streams held it"; }

  # The next release waits on the slot it is leaving, the second one.
  : > "$docker_log"
  rm -f "$run_dir/streams"
  rollout_run "$run_dir" env APP_TIER_BACKEND_B_CONTAINER=green-container \
    APP_TIER_MODEL_STREAMS=1,0 APP_TIER_MODEL_STREAMS_COUNT_FILE="$run_dir/streams" \
    DEPLOY_MODEL_STREAM_POLL_SECONDS=7 >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "the release after it did not succeed"; }
  grep -q ':18082/_internal/llm/streams' "$docker_log" \
    || fail "the second release did not ask the slot it was leaving"
  ! grep -q ':18081/_internal/llm/streams' "$docker_log" \
    || fail "the second release asked the slot traffic had just moved to"
  rm -rf "$run_dir"
  echo "PASS: the old backend is stopped only once its model streams have ended"
}

# A stream that outlives the bound is ended by the stop: a release never keeps
# the old backend longer than the handover pause, the drain and
# DEPLOY_MODEL_STREAM_GRACE_SECONDS after the switch. (The fakes' drain takes
# no time, so the wait itself sees most of that window.)
test_rollout_stops_a_model_stream_at_its_bound() {
  local run_dir docker_log polls
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_BACKEND_CONTAINER=blue-container \
    APP_TIER_MODEL_STREAMS=1 APP_TIER_MODEL_STREAMS_COUNT_FILE="$run_dir/streams" \
    DEPLOY_HANDOVER_AFTER_SECONDS=0 DEPLOY_MODEL_STREAM_POLL_SECONDS=7 \
    DEPLOY_MODEL_STREAM_GRACE_SECONDS=14 >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "rollout deploy did not succeed"; }
  polls="$(grep -c '^sleep 7$' "$docker_log" || true)"
  # 31 s drain + 14 s bound, polled every 7 s: at most 7 polls.
  [ "$polls" -ge 1 ] && [ "$polls" -le 7 ] || fail "the wait polled $polls times, past its bound"
  grep -q 'stop --time 60 blue-container' "$docker_log" || fail "the old backend was not stopped at the bound"
  grep -Eq 'still relays 1 model stream\(s\) after [0-9]+s; stopping it ends them' "$run_dir/release.log" \
    || { cat "$run_dir/release.log"; fail "the release does not say it ended a model stream"; }
  rm -rf "$run_dir"
  echo "PASS: a model stream past its bound is ended by the stop"
}

# The old backend's model streams are waited for only once this release has
# passed its health check: a release that then fails it rolls back at once,
# stopping the old backend without waiting, so the previous release can take
# that slot back.
test_rollout_waits_for_model_streams_only_after_the_health_check() {
  local run_dir docker_log healthy probe
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_BACKEND_CONTAINER=blue-container \
    APP_TIER_MODEL_STREAMS=1,0 APP_TIER_MODEL_STREAMS_COUNT_FILE="$run_dir/streams" \
    DEPLOY_MODEL_STREAM_POLL_SECONDS=7 >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "rollout deploy did not succeed"; }
  healthy="$(log_line "$docker_log" 'ps -a --no-trunc --filter label=com.docker.compose.project=cheese')"
  probe="$(log_line "$docker_log" '/_internal/llm/streams')"
  [ -n "$healthy" ] && [ -n "$probe" ] && [ "$healthy" -lt "$probe" ] \
    || fail "the release waited on the old backend's model streams before its own health check"
  grep -Fq 'finished its model streams after 7s' "$run_dir/release.log" \
    || { cat "$run_dir/release.log"; fail "the release did not wait for the streams after a healthy check"; }

  # A release that fails its health check does not wait.
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  if rollout_run "$run_dir" env APP_TIER_SCENARIO=frontend_absent APP_TIER_BACKEND_CONTAINER=blue-container \
    APP_TIER_MODEL_STREAMS=1 APP_TIER_MODEL_STREAMS_COUNT_FILE="$run_dir/streams" \
    DEPLOY_MODEL_STREAM_POLL_SECONDS=7 >"$run_dir/release.log" 2>&1; then
    fail "a release that failed its health check reported success"
  fi
  grep -q 'HEALTH CHECK FAILED' "$run_dir/release.log" || { cat "$run_dir/release.log"; fail "the release did not fail its health check"; }
  ! grep -q '^sleep 7$' "$docker_log" || fail "a failed release waited on the old backend's model streams"
  grep -Fq 'stopping blue-container without waiting for its model streams: this release failed its health check' "$run_dir/release.log" \
    || { cat "$run_dir/release.log"; fail "the failed release did not stop the old backend at once"; }
  grep -q 'stop --time 60 blue-container' "$docker_log" || fail "the old backend was left running"
  rm -rf "$run_dir"
  echo "PASS: model streams are waited for after the health check, and not by a failed release"
}

# One lost probe is not the end of the old backend's streams: the wait asks
# again, and gives up only after several in a row.
test_model_stream_wait_survives_a_lost_probe() {
  local dir out
  mkdir -p "$ROOT/.tmp"
  dir="$(mktemp -d "$ROOT/.tmp/stream-wait.XXXXXX")"
  out="$(PATH="$FAKE_BIN:$PATH" APP_TIER_MODEL_STREAMS=1,x,1,0 \
    APP_TIER_MODEL_STREAMS_COUNT_FILE="$dir/streams" DEPLOY_MODEL_STREAM_POLL_SECONDS=7 \
    bash "$ROOT/deploy/wait-model-streams.sh" 18081 old-backend 60 7)"
  printf '%s\n' "$out" | grep -Fq 'old-backend finished its model streams after 21s' \
    || { printf '%s\n' "$out"; rm -rf "$dir"; fail "one lost probe ended the wait"; }
  rm -f "$dir/streams"
  out="$(PATH="$FAKE_BIN:$PATH" APP_TIER_MODEL_STREAMS=1,x \
    APP_TIER_MODEL_STREAMS_COUNT_FILE="$dir/streams" DEPLOY_MODEL_STREAM_POLL_SECONDS=7 \
    bash "$ROOT/deploy/wait-model-streams.sh" 18081 old-backend 60 7)"
  rm -rf "$dir"
  printf '%s\n' "$out" | grep -Fq 'has not said how many model streams it relays for 3 probes' \
    || { printf '%s\n' "$out"; fail "a backend that stopped answering held the wait"; }
  echo "PASS: the model-stream wait outlasts a lost probe, and not three"
}

# A backend from before the count answers nothing about its model streams; it
# is stopped after the drain exactly as before, without waiting.
test_rollout_does_not_wait_on_a_backend_without_the_count() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_BACKEND_CONTAINER=blue-container \
    DEPLOY_MODEL_STREAM_POLL_SECONDS=7 >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "rollout deploy did not succeed"; }
  ! grep -q '^sleep 7$' "$docker_log" || fail "the release waited on a backend that does not count its streams"
  grep -q 'stop --time 60 blue-container' "$docker_log" || fail "the old backend was not stopped"
  grep -Fq 'does not say how many model streams it relays' "$run_dir/release.log" \
    || { cat "$run_dir/release.log"; fail "the release does not say it did not wait"; }
  rm -rf "$run_dir"
  echo "PASS: a backend without the stream count is stopped without waiting"
}

# The worker that carries a model stream through app-router belongs to the
# generation the release's reload retires. It must outlast the longest the
# release keeps the old backend: the handover pause, the drain, the model-stream
# bound and the backend's stop grace, or it ends the stream the wait kept alive.
test_app_router_outlasts_the_model_stream_wait() {
  local script="$ROOT/deploy/deploy-docker.sh" router timeout handover drain grace stop
  default_of() { { grep -o "\${$1:-[0-9]*}" "$script" || true; } | head -n 1 | tr -dc '0-9'; }
  handover="$(default_of DEPLOY_HANDOVER_AFTER_SECONDS)"
  drain="$(default_of DEPLOY_DRAIN_SECONDS)"
  grace="$(default_of DEPLOY_MODEL_STREAM_GRACE_SECONDS)"
  stop="$(default_of DEPLOY_BACKEND_STOP_GRACE_SECONDS)"
  router="$ROOT/deploy/llm-tunnel/app-router.conf"
  timeout="$(sed -n 's/^worker_shutdown_timeout \([0-9]*\)s;$/\1/p' "$router")"
  for value in "$handover" "$drain" "$grace" "$stop" "$timeout"; do
    [ -n "$value" ] || fail "could not read a deadline (handover=$handover drain=$drain grace=$grace stop=$stop router=$timeout)"
  done
  [ "$timeout" -gt $((handover + drain + grace + stop)) ] \
    || fail "app-router retires its workers after ${timeout}s, before the ${handover}+${drain}+${grace}+${stop}s a release keeps the old backend"
  echo "PASS: app-router's old workers outlast the longest a release keeps the old backend"
}

# app-router names a port that is neither slot: something else is serving, and
# the release must not guess which container to stop.
test_rollout_refuses_an_upstream_it_did_not_write() {
  local run_dir
  run_dir="$(new_rollout_run_dir)"
  printf 'upstream backend_active { server 127.0.0.1:18099; }\n' > "$run_dir/active/backend.conf"
  if rollout_run "$run_dir" env >"$run_dir/release.log" 2>&1; then
    fail "a release went ahead with app-router pointing at neither slot"
  fi
  grep -q 'names neither :18081 nor :18082' "$run_dir/release.log" \
    || fail "the refusal did not say which upstream it found"
  ! grep -q -- '--force-recreate backend' "$run_dir/docker.log" || fail "a slot was started anyway"
  ! grep -q 'nginx -s reload' "$run_dir/docker.log" || fail "app-router was reloaded anyway"
  rm -rf "$run_dir"
  echo "PASS: a release refuses an app-router upstream that is neither slot"
}

# With a rolling frontend, its idle slot comes up beside the backend's and both
# move in the one reload. The release whose old frontend still published the
# box's :8080 and :80 hands them to app-router once that frontend has stopped.
test_frontend_rollout_switches_with_the_backend() {
  local run_dir docker_log backend_up frontend_up reload collab drain stopped removed ports_reload
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  # A box already behind app-router, its frontend still the compose one on :8080.
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 18086 18080 18087
  printf 'upstream frontend_active { server 127.0.0.1:8080; }\n' > "$run_dir/active/frontend.conf"
  grep -Fq 'location ~ ^/api/topics/[^/]+/execution/[^/]+$' "$run_dir/active/sites-frontend.conf" \
    || fail "stable frontend ingress still sends public execution through the rolling frontend"
  rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" APP_TIER_BACKEND_CONTAINER=blue-container \
    APP_TIER_FRONTEND_CONTAINER=green-frontend >"$run_dir/deploy.log" 2>&1 || { cat "$run_dir/deploy.log"; fail "frontend rollout failed"; }
  backend_up="$(log_line "$docker_log" 'up -d --no-deps --force-recreate backend-b')"
  frontend_up="$(log_line "$docker_log" 'up -d --no-deps --force-recreate frontend-b')"
  reload="$(log_line "$docker_log" 'exec cheese-app-router nginx -s reload')"
  collab="$(log_line "$docker_log" 'up -d --no-deps collab')"
  drain="$(log_line "$docker_log" 'sleep 31')"
  # Its traffic has moved, and the box's ports are closed while it stops.
  stopped="$(log_line "$docker_log" 'stop --time 5 green-frontend')"
  removed="$(log_line "$docker_log" ' rm -f frontend$')"
  ports_reload="$(nth_log_line "$docker_log" 'exec cheese-app-router nginx -s reload' 2)"
  [ -n "$frontend_up" ] || fail "the idle frontend slot was never started"
  [ "$backend_up" -lt "$reload" ] && [ "$frontend_up" -lt "$reload" ] \
    || fail "app-router was switched before both new slots were up"
  grep -q 'curl -fsS -m 3 http://127.0.0.1:18084/' "$docker_log" || fail "the new frontend was not checked"
  [ "$reload" -lt "$collab" ] && [ "$collab" -lt "$drain" ] || fail "collab was not replaced between the switch and the old frontend's stop"
  [ -n "$stopped" ] && [ "$drain" -lt "$stopped" ] && [ "$stopped" -lt "$removed" ] \
    || fail "the old frontend was not stopped gracefully after the drain, before removal"
  [ -n "$ports_reload" ] && [ "$stopped" -lt "$ports_reload" ] && [ "$ports_reload" -lt "$removed" ] \
    || fail "app-router took the box's frontend ports before the old frontend let go of them, or after it was removed"
  [ "$(grep -c 'exec cheese-app-router nginx -s reload' "$docker_log")" = 2 ] \
    || fail "the first release did not reload exactly twice (switch, ports)"
  grep -Fq 'server 127.0.0.1:18084;' "$run_dir/active/frontend.conf" || fail "frontend proxy did not move to the new slot"
  grep -Fq 'server 127.0.0.1:18082;' "$run_dir/active/backend.conf" || fail "backend did not move in the same switch"
  # In frontend.conf, which every app-router.conf includes, so a release of an
  # older commit installing its own keeps them.
  grep -Fq 'listen 0.0.0.0:8080;' "$run_dir/active/frontend.conf" && grep -Fq 'listen 0.0.0.0:80;' "$run_dir/active/frontend.conf" \
    || fail "app-router does not serve the box's :8080 and :80"
  ! grep -q -E '(run|up) .*cheese-frontend-next' "$docker_log" || fail "a temporary frontend was started"

  # The next release goes back into the first slots, on loopback, with one reload.
  : > "$docker_log"
  rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" APP_TIER_FRONTEND_B_CONTAINER=frontend-b-container \
    >/dev/null 2>&1 || fail "second rollout failed"
  grep -q -- 'up -d --no-deps --force-recreate frontend$' "$docker_log" || fail "the second release did not use the first frontend slot"
  grep -q 'stop --time 120 frontend-b-container' "$docker_log" || fail "the old frontend slot was not given its full graceful stop"
  grep -Fq 'listen 0.0.0.0:8080;' "$run_dir/active/frontend.conf" || fail "the second release dropped the box's ports"
  grep -q 'curl -fsS -m 3 http://127.0.0.1:18088/' "$docker_log" || fail "the first frontend slot was not checked on its loopback port"
  [ "$(grep -c 'exec cheese-app-router nginx -s reload' "$docker_log")" = 1 ] || fail "the second release did not switch exactly once"
  grep -Fq 'server 127.0.0.1:18088;' "$run_dir/active/frontend.conf" || fail "frontend proxy did not return to the first slot"
  ! grep -q 'exec cheese-api-front nginx -s reload' "$docker_log" \
    || fail "ordinary frontend/backend release reloaded the persistent ingress"
  rm -rf "$run_dir"
  echo "PASS: the frontend moves with the backend in one reload, and the box's ports stay served"
}

test_frontend_rollout_rejects_unhealthy_next() {
  local run_dir
  run_dir="$(new_rollout_run_dir)"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 18086 18080 18087
  printf 'upstream frontend_active { server 127.0.0.1:8080; }\n' > "$run_dir/active/frontend.conf"
  if rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" APP_TIER_CURL_FAIL_MATCH=:18084/ >/dev/null 2>&1; then
    fail "unhealthy frontend was accepted"
  fi
  ! grep -q 'exec cheese-app-router nginx -s reload' "$run_dir/docker.log" || fail "app-router was switched without a healthy frontend"
  grep -q 'rm -f -s backend-b frontend-b' "$run_dir/docker.log" || fail "the new slots were left running beside the old ones"
  ! grep -q -E ' rm -f (backend|frontend)$' "$run_dir/docker.log" || fail "the serving slots were removed"
  grep -Fq 'server 127.0.0.1:8080;' "$run_dir/active/frontend.conf" || fail "frontend proxy moved to unhealthy successor"
  grep -Fq 'server 127.0.0.1:18081;' "$run_dir/active/backend.conf" || fail "backend moved although the frontend could not"
  ! grep -q 'listen' "$run_dir/active/frontend.conf" || fail "app-router took the frontend ports although the old frontend still holds them"
  rm -rf "$run_dir"
  echo "PASS: failed frontend startup leaves the old frontend and backend serving"
}

# If app-router cannot take the box's ports once the old frontend has let go of
# them, the ports go back to that frontend, and the next release retries at its
# switch, once the slot it replaces has freed them.
test_failed_port_takeover_gives_the_ports_back() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 18086 18080 18087
  printf 'upstream frontend_active { server 127.0.0.1:8080; }\n' > "$run_dir/active/frontend.conf"
  if rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" APP_TIER_FRONTEND_CONTAINER=green-frontend \
      APP_TIER_ROUTER_RELOAD_FAIL_FROM=2 >"$run_dir/deploy.log" 2>&1; then
    fail "a release whose port takeover failed reported success"
  fi
  grep -q 'green-frontend serves them again' "$run_dir/deploy.log" || { cat "$run_dir/deploy.log"; fail "the failure did not say who serves the ports"; }
  [ "$(log_line "$docker_log" 'stop --time 5 green-frontend')" -lt "$(log_line "$docker_log" 'start green-frontend')" ] \
    || fail "the old frontend was not started again after the failed takeover"
  ! grep -q ' rm -f .*frontend$' "$docker_log" || fail "the frontend that serves the ports again was removed"
  ! grep -q 'listen' "$run_dir/active/frontend.conf" || fail "frontend.conf still claims the ports app-router could not take"
  grep -Fq 'server 127.0.0.1:18084;' "$run_dir/active/frontend.conf" || fail "app-router lost the new frontend"
  : > "$docker_log"
  rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; fail "the next release did not recover"; }
  grep -q -- 'up -d --no-deps --force-recreate frontend$' "$docker_log" || fail "the next release did not replace the restarted frontend"
  grep -Fq 'listen 0.0.0.0:8080;' "$run_dir/active/frontend.conf" || fail "the next release did not take the ports at its switch"
  [ "$(grep -c 'exec cheese-app-router nginx -s reload' "$docker_log")" = 1 ] || fail "the recovering release did not switch exactly once"
  rm -rf "$run_dir"
  echo "PASS: a failed port takeover gives the ports back, and the next release takes them"
}

# app-router listens on what FRONTEND_PORT_DIRECT names for the compose
# frontend. A form nginx cannot listen on must stop the release before anything
# is touched, not in the middle of a switch; a bare port is the 0.0.0.0 one.
test_frontend_direct_port_is_checked_before_anything() {
  local run_dir
  run_dir="$(new_rollout_run_dir)"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 18086 18080 18087
  printf 'upstream frontend_active { server 127.0.0.1:8080; }\n' > "$run_dir/active/frontend.conf"
  cp "$run_dir/active/frontend.conf" "$run_dir/frontend.conf.before"
  if rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" FRONTEND_PORT_DIRECT=80-81 \
      >"$run_dir/deploy.log" 2>&1; then
    fail "a release went ahead with a FRONTEND_PORT_DIRECT nginx cannot listen on"
  fi
  grep -q "FRONTEND_PORT_DIRECT='80-81' is not a port" "$run_dir/deploy.log" || { cat "$run_dir/deploy.log"; fail "the refusal did not name the value"; }
  [ ! -s "$run_dir/docker.log" ] || { cat "$run_dir/docker.log"; fail "docker was used before the value was checked"; }
  cmp -s "$run_dir/frontend.conf.before" "$run_dir/active/frontend.conf" || fail "frontend.conf changed although the release refused"
  : > "$run_dir/docker.log"
  rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" FRONTEND_PORT_DIRECT=80 APP_TIER_FRONTEND_CONTAINER=green-frontend \
    >"$run_dir/deploy.log" 2>&1 || { cat "$run_dir/deploy.log"; fail "a bare FRONTEND_PORT_DIRECT was refused"; }
  grep -Fq 'listen 0.0.0.0:80;' "$run_dir/active/frontend.conf" || fail "a bare port did not become 0.0.0.0:80: $(cat "$run_dir/active/frontend.conf")"
  rm -rf "$run_dir"
  echo "PASS: FRONTEND_PORT_DIRECT is checked before anything is touched, and a bare port is accepted"
}

# A one-off successor an interrupted release before the two slots left behind:
# removed by the next release when app-router does not send traffic to it, and
# a refusal before anything changes when it does.
test_rollout_clears_an_interrupted_release() {
  local run_dir leftover
  for leftover in cheese-backend-next cheese-frontend-next; do
    run_dir="$(new_rollout_run_dir)"
    rollout_run "$run_dir" env APP_TIER_LEFTOVERS="$leftover" >"$run_dir/release.log" 2>&1 \
      || { cat "$run_dir/release.log"; fail "a release did not clear a leftover $leftover nobody serves from"; }
    grep -q "removing $leftover" "$run_dir/release.log" || fail "$leftover was not removed"
    [ "$(log_line "$run_dir/docker.log" "rm -f $leftover")" -lt "$(log_line "$run_dir/docker.log" 'alembic')" ] \
      || fail "$leftover was removed only after the migrations"
    rm -rf "$run_dir"
  done
  run_dir="$(new_rollout_run_dir)"
  printf 'upstream backend_active { server 127.0.0.1:18082; }\n' > "$run_dir/active/backend.conf"
  if rollout_run "$run_dir" env APP_TIER_LEFTOVERS=cheese-backend-next >"$run_dir/release.log" 2>&1; then
    fail "a release went ahead while a leftover cheese-backend-next serves"
  fi
  grep -q 'cheese-backend-next, left by an interrupted release, is what app-router serves' "$run_dir/release.log" \
    || fail "the refusal did not say that the leftover serves"
  ! grep -q -E -- '--force-recreate|nginx -s reload|alembic|rm -f cheese-backend-next' "$run_dir/docker.log" \
    || fail "something changed beside a serving leftover"
  rm -rf "$run_dir"
  echo "PASS: a leftover successor is removed when idle and refused when serving"
}

# The second backend slot, written from compose's real merged model in the
# script's own order, must be the first one with another port: on 2026-10-05 a
# copy written before the office editor and collab secrets were exported started
# with both empty. Uses the real `docker compose config` (no daemon needed).
test_second_slot_matches_the_first() {
  local run_dir real_docker
  real_docker="${APP_TIER_REAL_DOCKER_BIN:-}"
  [ -n "$real_docker" ] && "$real_docker" compose version >/dev/null 2>&1 \
    || fail "this case needs a real docker CLI with compose (none found on PATH)"
  run_dir="$(new_rollout_run_dir)"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 18086 18080 18087
  printf 'upstream frontend_active { server 127.0.0.1:8080; }\n' > "$run_dir/active/frontend.conf"
  rollout_run "$run_dir" env APP_TIER_REAL_DOCKER="$real_docker" APP_TIER_SLOT_MODEL="$run_dir/model.json" \
    ACTIVE_FRONTEND_DIR="$run_dir/active" \
    COMPOSE_OVERLAYS=docker-compose.subscription.yml FORGEJO_WEBHOOK_HOSTS=relay.example \
    >"$run_dir/release.log" 2>&1 || { cat "$run_dir/release.log"; fail "rollout with the real compose model failed"; }
  [ -s "$run_dir/model.json" ] || fail "the second slot was never started"
  "$APP_TIER_REAL_PYTHON" - "$run_dir/model.json" <<'PY' || fail "the second backend slot differs from the first"
import json, sys
services = json.load(open(sys.argv[1]))["services"]
first, second = services["backend"], services["backend-b"]
differ = sorted(k for k in set(first) | set(second) if first.get(k) != second.get(k))
if differ != ["networks", "ports"]:
    for key in differ:
        if key == "environment":
            env_a, env_b = first.get(key) or {}, second.get(key) or {}
            for name in sorted(set(env_a) | set(env_b)):
                if env_a.get(name) != env_b.get(name):
                    print(f"environment {name}: first={'set' if env_a.get(name) else repr(env_a.get(name))} second={'set' if env_b.get(name) else repr(env_b.get(name))}")
        else:
            print(f"{key}: {first.get(key)!r} != {second.get(key)!r}")
    sys.exit(1)
for name in ("COLLAB_SECRET", "OFFICE_EDITOR_JWT_SECRET"):
    if not second["environment"].get(name):
        print(f"{name} is empty in the second slot"); sys.exit(1)
if second["networks"]["default"]["aliases"] != ["backend"]:
    print("the second slot does not answer to `backend`"); sys.exit(1)
front, front_b = services["frontend"], services["frontend-b"]
differ = sorted(k for k in set(front) | set(front_b) if front.get(k) != front_b.get(k))
if differ != ["depends_on", "networks", "ports"]:
    print("frontend slots differ in", differ); sys.exit(1)
if front_b["networks"]["default"]["aliases"] != ["frontend"]:
    print("the second frontend slot does not answer to `frontend`"); sys.exit(1)
published = sorted((p["host_ip"], p["published"]) for p in front["ports"])
if published != [("127.0.0.1", "18088")]:
    print("the first frontend slot still publishes", published); sys.exit(1)
PY
  rm -rf "$run_dir"
  echo "PASS: the second slots are the first with another port, secrets included"
}

test_rollout_leaves_the_running_backend_alone_when_next_never_comes_up() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  if rollout_run "$run_dir" env APP_TIER_BACKEND_CONTAINER=blue-container APP_TIER_CURL_FAIL_MATCH=:18082/readyz >/dev/null 2>&1; then
    fail "rollout succeeded although the next backend never answered /readyz"
  fi
  grep -q 'up -d --no-deps --force-recreate backend-b' "$docker_log" \
    || fail "the next backend was never started"
  ! grep -q 'nginx -s reload' "$docker_log" \
    || fail "a reload happened although the next backend was unhealthy"
  ! grep -q -E 'stop --time 60 blue-container|kill --signal USR1| rm -f backend$' "$docker_log" \
    || fail "the running backend was touched although nothing healthy could replace it"
  grep -Fqx 'upstream backend_active { server 127.0.0.1:18081; }' "$run_dir/active/backend.conf" \
    || fail "app-router was moved off the running backend"
  grep -q 'rm -f -s backend-b' "$docker_log" || fail "the failed next backend was not cleaned up"
  rm -rf "$run_dir"
  echo "PASS: an unhealthy next backend leaves the running one untouched"
}

# A failed health check after the switch puts the previous release back the same
# way: into the idle slot, with the images it ran, in one more switch.
test_rollout_rollback_returns_to_the_previous_slot() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  if rollout_run "$run_dir" env APP_TIER_SCENARIO=rollback APP_RELEASE=thisrelease \
      APP_TIER_PREVIOUS_RELEASE=prevrelease >"$run_dir/release.log" 2>&1; then
    fail "rollback scenario unexpectedly passed health checks"
  fi
  grep -Fqx 'slot-up-env backend-b BACKEND_IMAGE= FRONTEND_IMAGE= IMAGE_TAG=testsha APP_RELEASE=thisrelease' "$docker_log" \
    || fail "the release did not start its own images, as its own release, in the idle slot"
  grep -Fqx 'slot-up-env backend BACKEND_IMAGE=repo/backend:oldsha FRONTEND_IMAGE=repo/frontend:oldsha IMAGE_TAG=oldsha APP_RELEASE=prevrelease' "$docker_log" \
    || { cat "$run_dir/release.log"; fail "the rollback did not start the previous images in the first slot"; }
  [ "$(grep -c 'exec cheese-app-router nginx -s reload' "$docker_log")" = 2 ] \
    || fail "release plus rollback did not switch exactly twice"
  grep -Fqx 'upstream backend_active { server 127.0.0.1:18081; }' "$run_dir/active/backend.conf" \
    || fail "app-router does not point at the rolled-back slot: $(cat "$run_dir/active/backend.conf")"
  rm -rf "$run_dir"
  echo "PASS: a rollback moves back into the previous slot with the previous images"
}

# Every other release starts while the second slots serve. The release it
# replaces is the one running there, and a failed health check goes back to it.
test_rollout_rollback_from_the_second_slot() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  printf 'upstream backend_active { server 127.0.0.1:18082; }\n' > "$run_dir/active/backend.conf"
  if rollout_run "$run_dir" env APP_TIER_SCENARIO=rollback APP_TIER_SERVING_SLOT=b \
      >"$run_dir/release.log" 2>&1; then
    fail "rollback scenario unexpectedly passed health checks"
  fi
  grep -q 'deploying sha=testsha (previous=oldsha)' "$run_dir/release.log" \
    || { cat "$run_dir/release.log"; fail "a release started while backend-b served found no previous release"; }
  grep -Fqx 'slot-up-env backend BACKEND_IMAGE= FRONTEND_IMAGE= IMAGE_TAG=testsha APP_RELEASE=' "$docker_log" \
    || fail "the release did not start its own images in the idle first slot"
  grep -Fqx 'slot-up-env backend-b BACKEND_IMAGE=repo/backend:oldsha FRONTEND_IMAGE=repo/frontend:oldsha IMAGE_TAG=oldsha APP_RELEASE=' "$docker_log" \
    || { cat "$run_dir/release.log"; fail "the rollback did not start the previous images in the second slot"; }
  grep -q 'rolled back to oldsha' "$run_dir/release.log" \
    || fail "the release did not say it rolled back"
  rm -rf "$run_dir"
  echo "PASS: a rollback from a release started beside backend-b returns to backend-b"
}

# A release is switched in only once its new slots have been used the way a
# person uses them (deploy/release-smoke.py). A failed smoke test leaves
# app-router and the slots serving exactly as they were.
smoke_rollout_dir() {
  local run_dir
  run_dir="$(new_rollout_run_dir)"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 18086 18080 18087
  printf 'upstream frontend_active { server 127.0.0.1:18088; }\n' > "$run_dir/active/frontend.conf"
  printf '%s' "$run_dir"
}

test_rollout_smoke_failure_keeps_the_serving_slots() {
  local run_dir docker_log check
  for check in provision login task websocket frontend-asset; do
    run_dir="$(smoke_rollout_dir)"
    docker_log="$run_dir/docker.log"
    if rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" \
        APP_TIER_BACKEND_CONTAINER=blue-container APP_TIER_FRONTEND_CONTAINER=green-frontend \
        APP_TIER_SMOKE_FAIL="$check" >"$run_dir/release.log" 2>&1; then
      fail "a release whose $check smoke check failed was deployed"
    fi
    grep -q "smoke $check FAILED" "$run_dir/release.log" \
      || { cat "$run_dir/release.log"; fail "the refusal does not name the $check check"; }
    ! grep -q 'nginx -s reload' "$docker_log" || fail "app-router was switched although the $check check failed"
    grep -Fqx 'upstream backend_active { server 127.0.0.1:18081; }' "$run_dir/active/backend.conf" \
      || fail "backend traffic moved although the $check check failed"
    grep -Fq 'server 127.0.0.1:18088;' "$run_dir/active/frontend.conf" \
      || fail "frontend traffic moved although the $check check failed"
    ! grep -q -E 'kill --signal USR1|stop --time [0-9]+ (blue-container|green-frontend)| rm -f (backend|frontend)$' "$docker_log" \
      || fail "the serving slots were touched although the $check check failed"
    grep -q 'rm -f -s backend-b frontend-b' "$docker_log" || fail "the new slots were left running after the $check check failed"
    rm -rf "$run_dir"
  done
  echo "PASS: a release that fails its smoke test never takes traffic, and the failure names the check"
}

test_rollout_smoke_passes_before_the_switch() {
  local run_dir docker_log smoke_check reload
  run_dir="$(smoke_rollout_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" APP_TIER_BACKEND_CONTAINER=blue-container \
    APP_TIER_FRONTEND_CONTAINER=green-frontend >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "a release that passed its smoke test was refused"; }
  for check in provision frontend-index frontend-asset login me task websocket logout; do
    grep -q "smoke $check ok" "$run_dir/release.log" || fail "the deploy log does not show the $check check"
  done
  smoke_check="$(log_line "$docker_log" ' python - check ')"
  reload="$(log_line "$docker_log" 'exec cheese-app-router nginx -s reload')"
  [ -n "$smoke_check" ] && [ "$smoke_check" -lt "$reload" ] || fail "the smoke test did not run before the switch"
  grep -Fq -- '--backend http://127.0.0.1:18082 --frontend http://127.0.0.1:18084' "$docker_log" \
    || fail "the smoke test did not use the new slots' own ports"
  grep -Fq 'server 127.0.0.1:18082;' "$run_dir/active/backend.conf" || fail "backend traffic did not move after a passing smoke test"
  # The session provision opened reaches the check, and its tokens never reach
  # the deploy log or a command line.
  grep -Fqx 'smoke-session-seen 42 fake-access-token-7f3a fake-refresh-token-9c1e' "$docker_log" \
    || fail "the check was not handed the session provision opened"
  ! grep -qF 'fake-access-token-7f3a' "$run_dir/release.log" || fail "the session's token was written to the deploy log"
  ! grep -v '^smoke-session-seen ' "$docker_log" | grep -qF 'fake-access-token-7f3a' \
    || fail "the session's token appeared on a command line"
  rm -rf "$run_dir"
  echo "PASS: a release that passes its smoke test switches in once, after it"
}

test_rollback_is_not_held_by_a_smoke_test() {
  local run_dir
  run_dir="$(new_rollout_run_dir)"
  if rollout_run "$run_dir" env APP_TIER_SCENARIO=rollback >"$run_dir/release.log" 2>&1; then
    fail "rollback scenario unexpectedly passed health checks"
  fi
  [ "$(grep -c ' python - check ' "$run_dir/docker.log")" = 1 ] \
    || fail "the smoke test did not run exactly once, for the release and not for its rollback"
  grep -q 'smoke: not run for a rollback' "$run_dir/release.log" || fail "the rollback did not say it skipped the smoke test"
  grep -q 'rolled back to oldsha' "$run_dir/release.log" || fail "the release did not roll back"
  rm -rf "$run_dir"
  echo "PASS: a rollback returns to the previous release without a smoke test"
}

test_box_without_slots_skips_the_smoke_test() {
  local run_dir
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/app-tier-smoke.XXXXXX")"
  PATH="$FAKE_BIN:$PATH" APP_TIER_SCENARIO=healthy APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$run_dir/docker.log" DEPLOY_HEALTH_ATTEMPTS=1 DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" "$ROOT/deploy/deploy-docker.sh" testsha \
    "$ROOT/deploy/compose/docker-compose.base.yml" >"$run_dir/release.log" 2>&1 \
    || { cat "$run_dir/release.log"; fail "a box without slots failed to deploy"; }
  grep -q 'smoke: skipped, this box has no app-router slots' "$run_dir/release.log" || fail "the skip was not logged"
  ! grep -q ' python - provision' "$run_dir/docker.log" || fail "a box without slots ran the smoke test"
  rm -rf "$run_dir"
  echo "PASS: a box without app-router slots skips the smoke test and says so"
}

# The preview owner (machine preview tunnels + preview content hosts) is a
# separately released service, like device-connection, and the cutover has one
# rule that matters more than any other: the owner must be up and healthy BEFORE
# api-front points at it and BEFORE any backend restarts rendered in owner mode.
# A helper that dials the tunnel in the window between those steps would find a
# route that is gone (nginx 404 -> the helper exits for good, preview_tunnel.py
# only retries 502/503/504).
test_deploy_starts_preview_owner_before_routes_and_backends() {
  local run_dir docker_log owner_up health reload next_up blue_up
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  # Seed the pre-cutover routing so this deploy performs the real cutover: the
  # routing file changes, api-front installs the owner route and reloads.
  bash "$ROOT/deploy/llm-tunnel/configure-preview.sh" legacy "$run_dir/active" >/dev/null
  rollout_run "$run_dir" env >"$run_dir/deploy.log" 2>&1 || { cat "$run_dir/deploy.log"; fail "owner-mode rollout failed"; }
  owner_up="$(log_line "$docker_log" 'up -d --no-deps preview-connection')"
  health="$(log_line "$docker_log" ':18087/healthz')"
  reload="$(last_log_line "$docker_log" 'exec cheese-api-front nginx -s reload')"
  next_up="$(log_line "$docker_log" '--force-recreate backend')"
  blue_up="$next_up"
  [ -n "$owner_up" ] || fail "owner-mode deploy never started the preview connection owner"
  grep -F 'preview-owner-up-env PREVIEW_CONNECTION_IMAGE=' "$docker_log" >/dev/null \
    || fail "the preview owner was started without the release backend image"
  [ -n "$health" ] || fail "the preview owner was never health-checked"
  [ -n "$reload" ] || fail "the owner cutover did not install the api-front route"
  [ "$owner_up" -lt "$health" ] || fail "the preview owner was probed before it was started"
  [ "$health" -lt "$reload" ] || fail "api-front switched to the owner before it was healthy"
  [ "$reload" -lt "$next_up" ] || fail "a backend rolled out before the owner route was live"
  [ "$health" -lt "$next_up" ] && [ "$health" -lt "$blue_up" ] \
    || fail "a backend restarted in owner mode before the owner was healthy"
  grep -Fqx '  default "127.0.0.1:18087";' "$run_dir/active/preview-routing.conf" \
    || fail "owner mode did not point the tunnel at the owner"
  rm -rf "$run_dir"
  echo "PASS: the preview owner is healthy before any route or backend changes"
}

test_deploy_fails_before_routing_when_preview_owner_unhealthy() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  # The pre-cutover routing must survive a failed owner startup untouched.
  bash "$ROOT/deploy/llm-tunnel/configure-preview.sh" legacy "$run_dir/active" >/dev/null
  if rollout_run "$run_dir" env APP_TIER_CURL_FAIL_MATCH=:18087/ DEPLOY_OWNER_HEALTH_SECONDS=2 \
      >"$run_dir/deploy.log" 2>&1; then
    rm -rf "$run_dir"
    fail "deploy succeeded although the preview owner never answered /healthz"
  fi
  grep -F 'up -d --no-deps preview-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "the preview owner was never started"; }
  ! grep -q 'exec cheese-api-front nginx -s reload' "$docker_log" \
    || { rm -rf "$run_dir"; fail "api-front routes switched despite the unhealthy owner"; }
  ! grep -q -- '--force-recreate backend' "$docker_log" \
    || { rm -rf "$run_dir"; fail "a backend rolled out despite the unhealthy owner"; }
  ! grep -q ' rm -f backend$' "$docker_log" \
    || { rm -rf "$run_dir"; fail "the serving backend was removed despite the unhealthy owner"; }
  grep -Fqx '  default "127.0.0.1:18085";' "$run_dir/active/preview-routing.conf" \
    || { rm -rf "$run_dir"; fail "the preview routing was rewritten despite the failed owner"; }
  rm -rf "$run_dir"
  echo "PASS: an unhealthy preview owner fails the deploy before any route or backend change"
}

test_deploy_keeps_preview_owner_running() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env APP_TIER_SCENARIO=stable_owner >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; fail "business deploy failed with a running preview owner"; }
  ! grep -qE 'up -d --no-deps (--force-recreate )?preview-connection' "$docker_log" \
    || { rm -rf "$run_dir"; fail "business deploy recreated the running preview owner"; }
  grep -F 'leaving preview connection owner' "$run_dir/deploy.log" >/dev/null \
    || { rm -rf "$run_dir"; fail "business deploy did not leave the preview owner alone"; }
  grep -q -- '--force-recreate backend' "$docker_log" \
    || { rm -rf "$run_dir"; fail "business deploy did not roll the app tier"; }
  rm -rf "$run_dir"
  echo "PASS: an ordinary business deploy leaves a running preview owner alone"
}

test_deploy_splits_preview_content_hosts_to_the_owner() {
  local run_dir
  run_dir="$(new_rollout_run_dir)"
  # A box already serving content, in the pre-cutover (no split) form.
  bash "$ROOT/deploy/llm-tunnel/configure-sites.sh" example.net "$run_dir/active"
  rollout_run "$run_dir" env >"$run_dir/deploy.log" 2>&1 || { cat "$run_dir/deploy.log"; fail "owner-mode rollout with a content domain failed"; }
  grep -Fq 'map $host $content_upstream {' "$run_dir/active/sites.conf" \
    || fail "owner mode did not write the content-host split into sites.conf"
  grep -Fq '"~^preview-[0-9a-f]{32}(-[0-9a-f]{22})?\." "127.0.0.1:18087";' "$run_dir/active/sites.conf" \
    || fail "the content-host split did not send preview hosts to the owner"
  grep -Fq 'default "127.0.0.1:18085";' "$run_dir/active/sites.conf" \
    || fail "the content-host split did not keep project hosts on the backend"
  grep -Fq 'proxy_pass http://$content_upstream;' "$run_dir/active/sites.conf" \
    || fail "the wildcard server still proxied content through the backend"
  grep -Fq 'server_name example.net *.example.net;' "$run_dir/active/sites.conf" \
    || fail "the content domain was changed while re-rendering sites.conf"
  rm -rf "$run_dir"
  echo "PASS: owner mode splits preview content hosts off the business backend"
}

test_kill_switch_routes_previews_back() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  # A box fully cut over: owner tunnel, split content hosts, owner front door.
  bash "$ROOT/deploy/llm-tunnel/configure-sites.sh" example.net "$run_dir/active" 18087
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 8080 18080 18087
  rollout_run "$run_dir" env PREVIEW_CONNECTION_MODE=legacy ACTIVE_FRONTEND_DIR="$run_dir/active" \
    >"$run_dir/deploy.log" 2>&1 || { cat "$run_dir/deploy.log"; fail "legacy kill-switch rollout failed"; }
  ! grep -q 'up -d --no-deps preview-connection' "$docker_log" \
    || { rm -rf "$run_dir"; fail "legacy mode still started the preview owner"; }
  grep -Fqx '  default "127.0.0.1:18085";' "$run_dir/active/preview-routing.conf" \
    || { rm -rf "$run_dir"; fail "legacy mode did not route the tunnel back through app-router"; }
  grep -Fq 'proxy_pass http://backend_active;' "$run_dir/active/sites.conf" \
    || { rm -rf "$run_dir"; fail "legacy mode did not put preview content hosts back on the backend"; }
  ! grep -q 'content_upstream' "$run_dir/active/sites.conf" \
    || { rm -rf "$run_dir"; fail "legacy mode left the owner split in sites.conf"; }
  ! grep -q 'preview/tunnel' "$run_dir/active/sites-frontend.conf" \
    || { rm -rf "$run_dir"; fail "legacy mode left the preview tunnel on the owner front door"; }
  rm -rf "$run_dir"
  echo "PASS: the legacy kill switch routes tunnels and content hosts back off the owner"
}

# A healthy /healthz only says the process is up. The owner reads static previews
# and room files straight off settings.workspace_root, which is a HOST path on a
# subscription box — so preview-connection must carry the same host-path mirror backend does. If it
# does not, the owner answers every health probe and fails every preview with a
# path that is not mounted. Pin the deploy's own check of that, and that it runs
# before anything is re-routed or replaced.
test_deploy_fails_when_preview_owner_cannot_see_its_files() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  # A box not yet cut over: the routing and the content hosts must survive a
  # failed owner check untouched.
  bash "$ROOT/deploy/llm-tunnel/configure-preview.sh" legacy "$run_dir/active" >/dev/null
  bash "$ROOT/deploy/llm-tunnel/configure-sites.sh" example.net "$run_dir/active" >/dev/null
  if rollout_run "$run_dir" env \
      APP_TIER_DOCKER_FAIL_MATCH='exec -T preview-connection python' \
      >"$run_dir/deploy.log" 2>&1; then
    rm -rf "$run_dir"
    fail "deploy succeeded although the owner could not see the files it serves"
  fi
  grep -F 'up -d --no-deps preview-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "the preview owner was never started"; }
  grep -F ':18087/healthz' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "the preview owner was never health-checked"; }
  grep -F 'exec -T preview-connection python' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "the deploy never checked the owner's own file paths"; }
  grep -F 'see the files it serves' "$run_dir/deploy.log" >/dev/null \
    || { rm -rf "$run_dir"; fail "the failed owner check did not say why the deploy stopped"; }
  ! grep -q 'exec cheese-api-front nginx -s reload' "$docker_log" \
    || { rm -rf "$run_dir"; fail "api-front routes switched despite the owner's missing files"; }
  ! grep -q -- '--force-recreate backend' "$docker_log" \
    || { rm -rf "$run_dir"; fail "a backend rolled out despite the owner's missing files"; }
  ! grep -q ' rm -f backend$' "$docker_log" \
    || { rm -rf "$run_dir"; fail "the serving backend was removed despite the owner's missing files"; }
  grep -Fqx '  default "127.0.0.1:18085";' "$run_dir/active/preview-routing.conf" \
    || { rm -rf "$run_dir"; fail "the preview routing was rewritten despite the failed owner check"; }
  ! grep -q 'content_upstream' "$run_dir/active/sites.conf" \
    || { rm -rf "$run_dir"; fail "the content-host split was written despite the failed owner check"; }
  rm -rf "$run_dir"
  echo "PASS: an owner that cannot see its files fails the deploy before any route or backend change"
}

# The other half of the kill switch. Flipping to legacy re-points the routes, but
# a still-running owner holds its helpers' connections: leaving it up means new
# tunnel requests land on a backend whose hub is empty while the old helpers stay
# on the owner. The deploy must therefore move the routes back — only AFTER the
# backends have rolled back to legacy, so a redial never hits a backend with no
# tunnel route — and then stop the owner.
test_kill_switch_stops_the_running_owner() {
  local run_dir docker_log backend_up reload stop_line reload_count
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  # A box fully cut over, with the owner still RUNNING when the switch flips.
  # Both files are written exactly as the previous (owner-mode) deploy would have
  # left them, INCLUDING the front door's 18086 upstream, so this pass sees no
  # change and does not reload api-front until it moves the routes back.
  bash "$ROOT/deploy/llm-tunnel/configure-sites.sh" example.net "$run_dir/active" 18087
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 18086 18080 18087
  printf 'upstream frontend_active { server 127.0.0.1:8080; }\n' > "$run_dir/active/frontend.conf"
  rollout_run "$run_dir" env PREVIEW_CONNECTION_MODE=legacy APP_TIER_SCENARIO=stable_owner \
    ACTIVE_FRONTEND_DIR="$run_dir/active" >"$run_dir/deploy.log" 2>&1 \
    || { cat "$run_dir/deploy.log"; fail "legacy flip with a running owner failed"; }
  grep -F 'previews stay on the running owner' "$run_dir/deploy.log" >/dev/null \
    || { rm -rf "$run_dir"; fail "legacy flip did not acknowledge the still-running owner"; }
  grep -F 'returning previews to the business backend' "$run_dir/deploy.log" >/dev/null \
    || { rm -rf "$run_dir"; fail "legacy flip never returned previews to the business backend"; }
  backend_up="$(log_line "$docker_log" '--force-recreate backend')"
  reload="$(last_log_line "$docker_log" 'exec cheese-api-front nginx -s reload')"
  reload_count="$(grep -c 'exec cheese-api-front nginx -s reload' "$docker_log" || true)"
  stop_line="$(log_line "$docker_log" 'stop preview-connection')"
  [ -n "$backend_up" ] || { rm -rf "$run_dir"; fail "the backend never rolled back to legacy"; }
  [ -n "$reload" ] || { rm -rf "$run_dir"; fail "the routes were never moved off the owner"; }
  [ "$reload_count" = 1 ] \
    || { rm -rf "$run_dir"; fail "the transitional pass reloaded api-front $reload_count times, not once at the end"; }
  [ -n "$stop_line" ] || { rm -rf "$run_dir"; fail "the running preview owner was never stopped"; }
  [ "$backend_up" -lt "$reload" ] \
    || { rm -rf "$run_dir"; fail "the routes moved off the owner before the backend served legacy"; }
  [ "$reload" -lt "$stop_line" ] \
    || { rm -rf "$run_dir"; fail "the owner was stopped before the routes moved off it"; }
  grep -Fqx '  default "127.0.0.1:18085";' "$run_dir/active/preview-routing.conf" \
    || { rm -rf "$run_dir"; fail "legacy flip left the tunnel on the owner"; }
  grep -Fq 'proxy_pass http://backend_active;' "$run_dir/active/sites.conf" \
    || { rm -rf "$run_dir"; fail "legacy flip left preview content hosts on the owner"; }
  ! grep -q 'content_upstream' "$run_dir/active/sites.conf" \
    || { rm -rf "$run_dir"; fail "legacy flip left the owner split in sites.conf"; }
  rm -rf "$run_dir"
  echo "PASS: the legacy kill switch stops a running owner only after its routes are back on the backend"
}

test_release_preview_connection_requires_interrupt() {
  local run_dir docker_log
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/preview-owner-release.XXXXXX")"
  docker_log="$run_dir/docker.log"
  : > "$docker_log"
  # No drain endpoint: without the explicit acknowledgement the release refuses.
  if PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" HOME="$run_dir" \
      "$ROOT/deploy/release-preview-connection.sh" testsha \
        "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "preview owner release ran without an interrupt acknowledgement"
  fi
  ! grep -q 'force-recreate preview-connection' "$docker_log" \
    || { rm -rf "$run_dir"; fail "unacknowledged release recreated the owner"; }
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" PREVIEW_CONNECTION_INTERRUPT=1 \
    DEPLOY_APP_IMAGE_SOURCE=registry HOME="$run_dir" \
    "$ROOT/deploy/release-preview-connection.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null \
    || { rm -rf "$run_dir"; fail "acknowledged preview owner release failed"; }
  grep -F 'pull preview-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "preview owner release did not pull the owner image"; }
  grep -F 'up -d --no-deps --force-recreate preview-connection' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "preview owner release did not recreate the owner"; }
  grep -F ':18087/healthz' "$docker_log" >/dev/null \
    || { rm -rf "$run_dir"; fail "preview owner release did not wait for the owner health"; }
  rm -rf "$run_dir"
  echo "PASS: preview owner release demands an explicit interrupt acknowledgement"
}

# Handing the bind mounts to another uid is the one step of a deploy that
# outlives a failure: everything before it can abort and leave the box exactly as
# it was, nothing after it can. On 2026-08-11 it ran third of five and the fourth
# step aborted, so dev sat on a 1001 backend with a 1000 workspace tree — a
# project-wide 422 until the next deploy (run 31466502982). These three tests pin
# the ordering and the undo.
ownership_run() {
  local run_dir="$1" scenario="$2"
  shift 2
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO="$scenario" \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$run_dir/docker.log" \
    BACKEND_IMAGE=repo/backend:testsha \
    FRONTEND_IMAGE=repo/frontend:testsha COLLAB_IMAGE=repo/collab:testsha \
    DEPLOY_APP_IMAGE_SOURCE=local \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    WORKSPACES_HOST_PATH="$run_dir/workspaces" \
    UPLOADS_HOST_PATH="$run_dir/uploads" \
    APPHOME_HOST_PATH="$run_dir/apphome" \
    CLAUDE_CACHE_HOST_PATH="$run_dir/claude-cache" \
    PI_CACHE_HOST_PATH="$run_dir/pi-cache" \
    TOOLCHAIN_CACHE_HOST_PATH="$run_dir/toolchain-cache" \
    HOME="$run_dir" \
    "$@"
}

# Line number of the first log line matching a pattern, or "" if absent. The
# `|| true` is load-bearing: this file runs under `set -e -o pipefail`, so a
# no-match grep would kill the run silently and a regression would read as a
# crash with no message instead of a named failure.
log_line() { grep -n -- "$2" "$1" 2>/dev/null | head -n 1 | cut -d: -f1 || true; }

new_ownership_run_dir() {
  mkdir -p "$ROOT/.tmp"
  local dir
  dir="$(mktemp -d "$ROOT/.tmp/ownership-order.XXXXXX")"
  # claude-cache is deliberately NOT created: the deploy script makes it, and
  # these tests are the only place that would notice if it stopped.
  mkdir -p "$dir/workspaces" "$dir/uploads" "$dir/apphome"
  : > "$dir/docker.log"
  printf '%s' "$dir"
}

test_ownership_handover_is_the_last_step_before_up() {
  run_dir="$(new_ownership_run_dir)"
  log="$run_dir/docker.log"
  ownership_run "$run_dir" healthy \
    "$ROOT/deploy/deploy-docker.sh" testsha \
    "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1 \
    || { rm -rf "$run_dir"; fail "healthy deploy failed"; }

  migrate="$(log_line "$log" 'run --rm backend')"
  handover="$(log_line "$log" ":/target")"
  up="$(log_line "$log" 'up -d backend frontend')"
  [ -n "$migrate" ] && [ -n "$handover" ] && [ -n "$up" ] \
    || { rm -rf "$run_dir"; fail "expected migrate/handover/up in the log"; }
  [ "$migrate" -lt "$handover" ] \
    || { rm -rf "$run_dir"; fail "the handover still runs before the migration — a failed migration would strand the box"; }
  [ "$handover" -lt "$up" ] \
    || { rm -rf "$run_dir"; fail "the handover must precede the swap"; }
  rm -rf "$run_dir"
  echo "PASS: ownership changes hands after the migration, right before the swap"
}

test_rollback_hands_the_mounts_back() {
  run_dir="$(new_ownership_run_dir)"
  log="$run_dir/docker.log"
  if ownership_run "$run_dir" rollback \
    "$ROOT/deploy/deploy-docker.sh" testsha \
    "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "rollback scenario unexpectedly passed health checks"
  fi

  handback="$(log_line "$log" 'chown -h 1001:1001')"
  rollback_up="$(log_line "$log" 'compose-up-env .*IMAGE_TAG=oldsha')"
  [ -n "$handback" ] \
    || { rm -rf "$run_dir"; fail "rolled the images back but not the ownership — the old backend cannot read the tree"; }
  [ -n "$rollback_up" ] && [ "$handback" -lt "$rollback_up" ] \
    || { rm -rf "$run_dir"; fail "the hand-back must finish before the old image starts"; }
  rm -rf "$run_dir"
  echo "PASS: a rollback hands the bind mounts back before starting the old image"
}

test_rollback_leaves_an_already_migrated_box_alone() {
  run_dir="$(new_ownership_run_dir)"
  log="$run_dir/docker.log"
  # Markers say the trees already belong to the current uid, so this deploy moved
  # nothing and the previous image shares that uid. Handing anything back here
  # would be the change that breaks the rollback.
  for dir in workspaces uploads apphome; do : > "$run_dir/$dir/.cheese-uid-1000"; done
  # Made by the deploy script, so it has to be marked after the fact — an
  # unmarked path would make this "nothing moved" scenario move something.
  mkdir -p "$run_dir/claude-cache"; : > "$run_dir/claude-cache/.cheese-uid-1000"
  mkdir -p "$run_dir/pi-cache"; : > "$run_dir/pi-cache/.cheese-uid-1000"
  mkdir -p "$run_dir/toolchain-cache"; : > "$run_dir/toolchain-cache/.cheese-uid-1000"

  if ownership_run "$run_dir" rollback \
    "$ROOT/deploy/deploy-docker.sh" testsha \
    "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "rollback scenario unexpectedly passed health checks"
  fi

  if grep -q '1001:1001' "$log"; then
    rm -rf "$run_dir"
    fail "handed a migrated tree back to 1001 — that is what would break it"
  fi
  grep -q 'compose-up-env .*IMAGE_TAG=oldsha' "$log" \
    || { rm -rf "$run_dir"; fail "the rollback itself did not happen"; }
  rm -rf "$run_dir"
  echo "PASS: a rollback on an already-migrated box touches no ownership"
}

test_operator_rejects_stale_frontend() {
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=frontend_stale \
    APP_TIER_MAIN_SHA=abc1234 \
    CHEESE_DEV_HOST=fake-host \
    "$ROOT/scripts/whats-live.sh"; then
    fail "whats-live reported success with a stale frontend"
  fi
  echo "PASS: operator drift check rejects a stale frontend"
}

test_operator_uses_registry_sha_width() {
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=abc1234 \
    APP_TIER_GIT_SHA=abc1234def5678901234567890123456789abcde \
    CHEESE_DEV_HOST=fake-host \
    "$ROOT/scripts/whats-live.sh" >/dev/null
  echo "PASS: operator drift check uses the registry's 7-character SHA tag"
}

# The only step in this suite that needs Python at all — it reads one `run:`
# block out of a workflow file. Plain python3 is tried first and `uv run` is the
# fallback: everything else here is bash against fakes, so the suite has to be
# runnable where the backend venv does not exist, which is exactly the hosted CI
# job that gates deploy/ changes.
workflow_script() {
  local parser
  mkdir -p "$ROOT/.tmp"
  parser="$(mktemp "$ROOT/.tmp/drift-step.XXXXXX.py")"
  cat > "$parser" <<'PY'
import sys
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text())
# Prefix match: the step has been renamed once already (it gained ", converge on
# drift"), and an exact match silently turned this whole case into a hard error.
for step in workflow["jobs"]["drift"]["steps"]:
    if str(step.get("name", "")).startswith("Compare main with what is running"):
        print(step["run"])
        break
else:
    raise SystemExit("workflow drift step not found")
PY
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    python3 "$parser" "$ROOT/.github/workflows/deploy-drift.yml"
  elif command -v uv >/dev/null 2>&1; then
    (cd "$ROOT/backend" && uv run python "$parser" "$ROOT/.github/workflows/deploy-drift.yml")
  else
    rm -f "$parser"
    fail "no python3 with PyYAML and no uv — cannot read the drift workflow"
  fi
  rm -f "$parser"
}

# The green anchor the drift step measures against: a full sha whose first seven
# characters are the tag the fake docker serves, because the step derives the
# live tag from the anchor rather than from main's tip.
DRIFT_ANCHOR="abc1234def5678901234567890123456789abcde"

# Runs the drift step the way the workflow does: from the repository root, with
# a gh that answers the anchor and the build-run queries, and GITHUB_REPOSITORY
# in the environment. The step exits 0 both when it converged and when it could
# not tell what to converge to, so the cases assert what it did — a match, a
# re-dispatch of a named commit, or a refusal — never the bare exit status.
run_drift_step() {
  local script="$1"
  shift
  ( cd "$ROOT" && PATH="$FAKE_BIN:$PATH" \
    GITHUB_REPOSITORY=example/cheese \
    APP_TIER_ANCHOR_SHA="$DRIFT_ANCHOR" \
    env "$@" bash -c "$script" )
}

test_workflow_rejects_stale_frontend() {
  local script calls out rc=0
  script="$(workflow_script)"
  mkdir -p "$ROOT/.tmp"
  calls="$(mktemp "$ROOT/.tmp/drift-gh.XXXXXX")"
  out="$(mktemp "$ROOT/.tmp/drift-out.XXXXXX")"

  run_drift_step "$script" \
    APP_TIER_SCENARIO=frontend_stale \
    APP_TIER_MAIN_SHA=abc1234 \
    APP_TIER_BUILD_OK=1 \
    APP_TIER_GH_CALLS="$calls" >"$out" 2>&1 || rc=$?

  if grep -q 'live matches main-green' "$out"; then
    cat "$out"
    rm -f "$calls" "$out"
    fail "workflow reported a stale frontend as current"
  fi
  grep -q "workflow run deploy-dev.yml --ref main -f ref=$DRIFT_ANCHOR" "$calls" \
    || { echo "drift step exit: $rc"; cat "$out"; cat "$calls"; rm -f "$calls" "$out"; fail "a stale frontend did not re-fire the green anchor's deploy"; }
  rm -f "$calls" "$out"
  echo "PASS: workflow drift check re-fires the green anchor for a stale frontend"
}

test_workflow_fails_on_a_failed_anchor_build() {
  local script out
  script="$(workflow_script)"
  mkdir -p "$ROOT/.tmp"
  out="$(mktemp "$ROOT/.tmp/drift-out.XXXXXX")"
  if run_drift_step "$script" \
    APP_TIER_SCENARIO=frontend_stale \
    APP_TIER_MAIN_SHA=abc1234 \
    APP_TIER_BUILD_FAILED=1 >"$out" 2>&1; then
    cat "$out"
    rm -f "$out"
    fail "a failed build for the anchor left the drift check green"
  fi
  rm -f "$out"
  echo "PASS: a failed build for the anchor keeps the drift check red"
}

test_workflow_does_not_guess_without_the_anchor() {
  local script calls out rc=0
  script="$(workflow_script)"
  mkdir -p "$ROOT/.tmp"
  calls="$(mktemp "$ROOT/.tmp/drift-gh.XXXXXX")"
  out="$(mktemp "$ROOT/.tmp/drift-out.XXXXXX")"

  run_drift_step "$script" \
    APP_TIER_SCENARIO=frontend_stale \
    APP_TIER_MAIN_SHA=abc1234 \
    APP_TIER_ANCHOR=unreadable \
    APP_TIER_GH_CALLS="$calls" >"$out" 2>&1 || rc=$?

  if grep -q 'live matches main-green' "$out"; then
    cat "$out"
    rm -f "$calls" "$out"
    fail "an unreadable anchor was reported as live matches"
  fi
  if grep -q 'workflow run' "$calls"; then
    cat "$calls"
    rm -f "$calls" "$out"
    fail "guessed a release to fire without the green anchor"
  fi
  grep -q 'could not read refs/heads/main-green' "$out" \
    || { echo "drift step exit: $rc"; cat "$out"; rm -f "$calls" "$out"; fail "an unreadable anchor was skipped without saying so"; }
  rm -f "$calls" "$out"
  echo "PASS: an unreadable anchor is named and nothing is guessed"
}

test_healthy_current_pair_passes() {
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=abc1234 \
    docker ps -a --format \
      '{{.Label "com.docker.compose.service"}}\t{{.Image}}\t{{.State}}\t{{.Status}}' \
    | "$ROOT/deploy/check-app-tier.sh" abc1234
  echo "PASS: one healthy current backend/frontend pair passes"
}

test_missing_forge_fails_health_check() {
  if PATH="$FAKE_BIN:$PATH" APP_TIER_SCENARIO=forgejo_absent \
    APP_TIER_MAIN_SHA=abc1234 docker ps -a --format \
      '{{.Label "com.docker.compose.service"}}\t{{.Image}}\t{{.State}}\t{{.Status}}' \
    | "$ROOT/deploy/check-app-tier.sh" abc1234; then
    fail "healthy app containers hid the missing repository service"
  fi
  echo "PASS: a missing repository service fails deployment health"
}

test_forge_migration_release() {
  local run_dir mode expected check apply result scenario native_stop writer_check migration
  mkdir -p "$ROOT/.tmp"
  for mode in first completed check_failed writers_remain preserved_children apply_failed health_failed retry_health_failed retry_completed; do
    run_dir="$(mktemp -d "$ROOT/.tmp/forge-release.XXXXXX")"
    check=2 apply=0 expected=0 scenario=healthy
    case "$mode" in
      completed) check=0 ;;
      check_failed) check=1; expected=1 ;;
      writers_remain|preserved_children) expected=1 ;;
      apply_failed) apply=1; expected=1 ;;
      health_failed) scenario=rollback; expected=1 ;;
      retry_health_failed)
        scenario=rollback; check=0; expected=1
        mkdir -p "$run_dir/apphome/forge-migration"
        touch "$run_dir/apphome/forge-migration/cutover-pending"
        touch "$run_dir/apphome/forge-migration/restart-host-executor"
        ;;
      retry_completed)
        check=0
        mkdir -p "$run_dir/apphome/forge-migration"
        touch "$run_dir/apphome/forge-migration/cutover-pending"
        touch "$run_dir/apphome/forge-migration/restart-host-executor"
        ;;
    esac
    result=0
    PATH="$FAKE_BIN:$PATH" \
      APP_TIER_DOCKER_LOG="$run_dir/docker.log" \
      APP_TIER_SCENARIO="$scenario" \
      APPHOME_HOST_PATH="$run_dir/apphome" \
      APP_TIER_FORGE_CHECK="$check" APP_TIER_FORGE_APPLY="$apply" \
      APP_TIER_HOST_EXECUTOR=active \
      APP_TIER_HOST_KILL_MODE="$([ "$mode" = preserved_children ] && echo process || echo control-group)" \
      APP_TIER_WORKSPACE_WRITERS="$([ "$mode" = writers_remain ] && echo 1 || echo 0)" \
      COMPOSE_OVERLAYS=docker-compose.subscription.yml \
      FORGEJO_URL=https://forge.example/forge/ FORGEJO_WEBHOOK_HOSTS=relay.example \
      DEPLOY_HEALTH_ATTEMPTS=1 DEPLOY_HEALTH_INTERVAL_SECONDS=0 HOME="$run_dir" \
      "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/release.log" 2>&1 \
      || result=1
    if [ "$result" != "$expected" ]; then
      cat "$run_dir/release.log" >&2
      fail "forge $mode returned $result: $run_dir/release.log"
    fi
    grep -F 'docker-compose.forgejo.yml' "$run_dir/docker.log" >/dev/null || fail "default forge service was not included"
    grep -F 'up -d --no-deps forge-events' "$run_dir/docker.log" >/dev/null || fail "event relay was not started"
    grep -Fx bootstrap "$run_dir/docker.log" >/dev/null || fail "admin provisioning did not run"
    case "$mode" in
      preserved_children)
        if grep -F -- '--apply --writers-stopped' "$run_dir/docker.log" >/dev/null; then
          fail "migration ran while stopping the connector would preserve its children"
        fi
        ;;
      writers_remain)
        grep -F 'systemctl stop cheese.service' "$run_dir/docker.log" >/dev/null || fail "native executor stayed running"
        if grep -F -- '--apply --writers-stopped' "$run_dir/docker.log" >/dev/null; then
          fail "migration ran with remaining workspace users"
        fi
        ;;
      first|apply_failed|health_failed)
        grep -F 'stop backend' "$run_dir/docker.log" >/dev/null || fail "writers stayed running"
        grep -F 'stop legacy-git-container' "$run_dir/docker.log" >/dev/null || fail "Git receiver stayed running"
        grep -F -- '--apply --writers-stopped' "$run_dir/docker.log" >/dev/null || fail "migration did not run"
        native_stop="$(log_line "$run_dir/docker.log" 'systemctl stop cheese.service')"
        writer_check="$(log_line "$run_dir/docker.log" workspace-writers-checked)"
        migration="$(log_line "$run_dir/docker.log" '--apply --writers-stopped')"
        [ "$native_stop" -lt "$writer_check" ] && [ "$writer_check" -lt "$migration" ] \
          || fail "migration started before native writers were quiesced"
        ;;
      completed|check_failed|retry_health_failed|retry_completed)
        if grep -F 'stop backend' "$run_dir/docker.log" >/dev/null; then
          fail "$mode stopped the running backend"
        fi
        ;;
    esac
    if [ "$mode" = health_failed ] || [ "$mode" = retry_health_failed ]; then
      grep -F 'refusing to restart the legacy repository writer' "$run_dir/release.log" >/dev/null || fail "migration allowed a legacy rollback"
      if grep -F 'compose-up-env BACKEND_IMAGE=repo/backend:oldsha' "$run_dir/docker.log" >/dev/null; then
        fail "legacy backend resumed writing after migration"
      fi
    elif [ "$expected" = 1 ] && grep -F 'up -d backend frontend' "$run_dir/docker.log" >/dev/null; then
      fail "$mode started the app despite a failed migration"
    fi
    if [ "$expected" = 0 ] && [ -f "$run_dir/apphome/forge-migration/cutover-pending" ]; then
      fail "successful release retained its cutover guard"
    fi
    if [ "$mode" = first ] || [ "$mode" = retry_completed ]; then
      grep -F 'systemctl start cheese.service' "$run_dir/docker.log" >/dev/null || fail "native executor was not restored"
      [ ! -f "$run_dir/apphome/forge-migration/restart-host-executor" ] || fail "restart receipt was not consumed"
    elif grep -F 'systemctl start cheese.service' "$run_dir/docker.log" >/dev/null; then
      fail "$mode resumed native writers prematurely"
    fi
    rm -rf "$run_dir"
    echo "PASS: forge release $mode"
  done
}

case "$CASE" in
  forge-health) test_missing_forge_fails_health_check ;;
  forge-migration) test_forge_migration_release ;;
  deploy) test_deploy_rejects_absent_frontend ;;
  deploy-healthy) test_deploy_accepts_healthy_pair ;;
  deploy-collab) test_deploy_requires_released_collab ;;
  connection-owner) test_deploy_keeps_connection_owner_running ;;
  connection-owner-local) test_local_deploy_installs_owner_from_verified_backend_image ;;
  journal-retention) test_deploy_applies_journal_retention ;;
  connection-route) test_rollout_installs_connection_route_without_recreating_api_front ;;
  cloud-control-release) test_cloud_control_has_an_independent_drained_release ;;
  ci-service-images) test_deploy_retains_ci_service_images ;;
  private-executor) test_deploy_delivers_private_executor ;;
  private-executor-missing) test_private_executor_failure_does_not_fail_deploy ;;
  local-images) test_local_app_images_skip_registry_pull ;;
  local-images-missing) test_local_app_images_must_exist ;;
  rollback-images) test_rollback_restores_exact_previous_images ;;
  unready-rollback) test_unready_backend_without_slots_still_rolls_back ;;
  ownership-order) test_ownership_handover_is_the_last_step_before_up ;;
  ownership-rollback) test_rollback_hands_the_mounts_back ;;
  ownership-rollback-noop) test_rollback_leaves_an_already_migrated_box_alone ;;
  pull-retry) test_pull_retries_transient_failure ;;
  pull-disk-full) test_pull_on_a_full_disk_reclaims_before_the_retry ;;
  pull-exhausted) test_exhausted_pull_retries_still_fail_the_deploy ;;
  login-retry) test_ghcr_login_retries_dropped_connections ;;
  login-refused) test_ghcr_login_refused_is_not_retried ;;
  pull-refused) test_pull_refused_is_not_retried ;;
  failed-reclaim) test_failed_deploy_reclaims_disk ;;
  disk-watermark) test_deploy_logs_disk_watermarks ;;
  operator) test_operator_rejects_stale_frontend ;;
  operator-sha-width) test_operator_uses_registry_sha_width ;;
  workflow) test_workflow_rejects_stale_frontend ;;
  workflow-build-failed) test_workflow_fails_on_a_failed_anchor_build ;;
  workflow-no-anchor) test_workflow_does_not_guess_without_the_anchor ;;
  session-base) test_deploy_warns_when_the_session_base_will_not_survive ;;
  healthy) test_healthy_current_pair_passes ;;
  rollout) test_rollout_switches_once ;;
  api-pool) test_rollout_with_an_api_pool ;;
  forge-router-recovery) test_rollout_recovers_after_forge_stops_backend ;;
  rollout-unknown-upstream) test_rollout_refuses_an_upstream_it_did_not_write ;;
  rollout-rollback) test_rollout_rollback_returns_to_the_previous_slot ;;
  smoke-refusal) test_rollout_smoke_failure_keeps_the_serving_slots ;;
  smoke-pass) test_rollout_smoke_passes_before_the_switch ;;
  smoke-rollback) test_rollback_is_not_held_by_a_smoke_test ;;
  smoke-skip) test_box_without_slots_skips_the_smoke_test ;;
  rollout-rollback-b) test_rollout_rollback_from_the_second_slot ;;
  frontend-rollout) test_frontend_rollout_switches_with_the_backend ;;
  rollout-leftovers) test_rollout_clears_an_interrupted_release ;;
  port-takeover-failure) test_failed_port_takeover_gives_the_ports_back ;;
  frontend-direct-port) test_frontend_direct_port_is_checked_before_anything ;;
  second-slot) test_second_slot_matches_the_first ;;
  frontend-rollout-unhealthy) test_frontend_rollout_rejects_unhealthy_next ;;
  rollout-unhealthy-next) test_rollout_leaves_the_running_backend_alone_when_next_never_comes_up ;;
  rollout-handover) test_rollout_moves_running_work_with_the_traffic ;;
  rollout-handover-old-backend) test_rollout_never_signals_a_backend_without_the_handler ;;
  rollout-model-streams) test_rollout_stops_the_old_backend_after_its_model_streams ;;
  rollout-model-stream-bound) test_rollout_stops_a_model_stream_at_its_bound ;;
  rollout-model-streams-after-health) test_rollout_waits_for_model_streams_only_after_the_health_check ;;
  model-stream-lost-probe) test_model_stream_wait_survives_a_lost_probe ;;
  rollout-model-streams-old-backend) test_rollout_does_not_wait_on_a_backend_without_the_count ;;
  app-router-deadline) test_app_router_outlasts_the_model_stream_wait ;;
  preview-owner-ordering) test_deploy_starts_preview_owner_before_routes_and_backends ;;
  preview-owner-unhealthy) test_deploy_fails_before_routing_when_preview_owner_unhealthy ;;
  preview-owner-stable) test_deploy_keeps_preview_owner_running ;;
  preview-owner-sites) test_deploy_splits_preview_content_hosts_to_the_owner ;;
  preview-kill-switch) test_kill_switch_routes_previews_back ;;
  preview-owner-files) test_deploy_fails_when_preview_owner_cannot_see_its_files ;;
  preview-kill-switch-owner) test_kill_switch_stops_the_running_owner ;;
  preview-owner-release) test_release_preview_connection_requires_interrupt ;;
  owners-behind-schema) test_deploy_releases_owners_behind_the_schema ;;
  api-front-reload) test_api_front_reloads_only_for_a_new_config ;;
  tls-renew-owner) test_certificate_renewal_keeps_the_preview_owner_route ;;
  unchanged-services) test_unchanged_services_keep_their_containers ;;
  collab-rollback) test_rollback_returns_collab_to_the_previous_image ;;
  owners-behind-schema-failed-migration) test_failed_migration_resumes_the_drained_owner ;;
  owners-spared) test_deploy_leaves_owners_the_migrations_spare ;;
  owners-behind-database) test_deploy_releases_owners_the_database_left_behind ;;
  owners-unreadable-migrations) test_deploy_releases_owners_when_migrations_unreadable ;;
  owner-release-same-image) test_owner_release_of_the_running_image_keeps_connections ;;
  all)
    test_rollout_with_an_api_pool
    test_missing_forge_fails_health_check
    test_forge_migration_release
    test_deploy_rejects_absent_frontend
    test_deploy_accepts_healthy_pair
    test_deploy_requires_released_collab
    test_deploy_keeps_connection_owner_running
    test_deploy_applies_journal_retention
    test_local_deploy_installs_owner_from_verified_backend_image
    test_owner_release_reuses_box_config_and_stops_when_busy
    test_cloud_control_has_an_independent_drained_release
    test_rollout_installs_connection_route_without_recreating_api_front
    test_api_front_reloads_only_for_a_new_config
    test_certificate_renewal_keeps_the_preview_owner_route
    test_deploy_warns_when_the_session_base_will_not_survive
    test_deploy_retains_ci_service_images
    test_deploy_delivers_private_executor
    test_private_executor_failure_does_not_fail_deploy
    test_local_app_images_skip_registry_pull
    test_local_app_images_must_exist
    test_rollback_restores_exact_previous_images
    test_unchanged_services_keep_their_containers
    test_rollback_returns_collab_to_the_previous_image
    test_unready_backend_without_slots_still_rolls_back
    test_ownership_handover_is_the_last_step_before_up
    test_rollback_hands_the_mounts_back
    test_rollback_leaves_an_already_migrated_box_alone
    test_pull_retries_transient_failure
    test_pull_on_a_full_disk_reclaims_before_the_retry
    test_exhausted_pull_retries_still_fail_the_deploy
    test_ghcr_login_retries_dropped_connections
    test_ghcr_login_refused_is_not_retried
    test_pull_refused_is_not_retried
    test_failed_deploy_reclaims_disk
    test_deploy_logs_disk_watermarks
    test_operator_rejects_stale_frontend
    test_operator_uses_registry_sha_width
    test_workflow_rejects_stale_frontend
    test_workflow_fails_on_a_failed_anchor_build
    test_workflow_does_not_guess_without_the_anchor
    test_healthy_current_pair_passes
    test_rollout_switches_once
    test_rollout_moves_running_work_with_the_traffic
    test_rollout_never_signals_a_backend_without_the_handler
    test_rollout_stops_the_old_backend_after_its_model_streams
    test_rollout_stops_a_model_stream_at_its_bound
    test_rollout_waits_for_model_streams_only_after_the_health_check
    test_model_stream_wait_survives_a_lost_probe
    test_rollout_does_not_wait_on_a_backend_without_the_count
    test_app_router_outlasts_the_model_stream_wait
    test_rollout_recovers_after_forge_stops_backend
    test_rollout_refuses_an_upstream_it_did_not_write
    test_rollout_rollback_returns_to_the_previous_slot
    test_rollout_rollback_from_the_second_slot
    test_rollout_smoke_failure_keeps_the_serving_slots
    test_rollout_smoke_passes_before_the_switch
    test_rollback_is_not_held_by_a_smoke_test
    test_box_without_slots_skips_the_smoke_test
    test_frontend_rollout_switches_with_the_backend
    test_rollout_clears_an_interrupted_release
    test_failed_port_takeover_gives_the_ports_back
    test_frontend_direct_port_is_checked_before_anything
    test_second_slot_matches_the_first
    test_frontend_rollout_rejects_unhealthy_next
    test_rollout_leaves_the_running_backend_alone_when_next_never_comes_up
    test_deploy_starts_preview_owner_before_routes_and_backends
    test_deploy_fails_before_routing_when_preview_owner_unhealthy
    test_deploy_keeps_preview_owner_running
    test_deploy_splits_preview_content_hosts_to_the_owner
    test_kill_switch_routes_previews_back
    test_deploy_fails_when_preview_owner_cannot_see_its_files
    test_kill_switch_stops_the_running_owner
    test_release_preview_connection_requires_interrupt
    test_deploy_releases_owners_behind_the_schema
    test_failed_migration_resumes_the_drained_owner
    test_deploy_leaves_owners_the_migrations_spare
    test_deploy_releases_owners_the_database_left_behind
    test_deploy_releases_owners_when_migrations_unreadable
    test_owner_release_of_the_running_image_keeps_connections
    ;;
  *) fail "unknown case: $CASE" ;;
esac
