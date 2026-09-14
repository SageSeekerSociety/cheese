#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${1:-all}"
FAKE_BIN="$ROOT/deploy/tests/fakes/app-tier"

# deploy-docker.sh CREATES the openviking memory dir (it must exist before the
# bind mount, or docker makes it root-owned and the backend cannot write it).
# Its default is a real path on the dev box, which a CI runner has neither
# reason nor permission to create — so every deploy in this file gets one
# inside the test tree. Exported once rather than per case: a future test that
# forgets it would not fail here, it would fail on someone's machine.
export VIKING_HOST_PATH="$ROOT/.tmp/viking-$$"
# The claude binary cache is the same shape: created by the deploy so the
# backend can write it, defaulting to a dev-box path CI cannot create.
export CLAUDE_CACHE_HOST_PATH="$ROOT/.tmp/claude-cache-$$"
# And the transcript archives, once more the same shape.
export TRANSCRIPTS_HOST_PATH="$ROOT/.tmp/transcripts-$$"
trap 'rm -rf "$ROOT/.tmp/viking-$$" "$ROOT/.tmp/claude-cache-$$" "$ROOT/.tmp/transcripts-$$"' EXIT

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

  if grep -F 'up -d --no-deps device-connection' "$docker_log" >/dev/null; then
    rm -rf "$run_dir"
    fail "business deploy recreated the running device connection owner"
  fi
  grep -F 'up -d backend frontend' "$docker_log" >/dev/null || {
    rm -rf "$run_dir"
    fail "business deploy did not update the app tier"
  }
  rm -rf "$run_dir"
  echo "PASS: business deploy leaves the device connection owner running"
}

test_local_deploy_installs_owner_from_verified_backend_image() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/connection-owner-local.XXXXXX")"
  docker_log="$run_dir/docker.log"
  PATH="$FAKE_BIN:$PATH" APP_TIER_DOCKER_LOG="$docker_log" \
    DEPLOY_APP_IMAGE_SOURCE=local BACKEND_IMAGE=repo/backend:local \
    FRONTEND_IMAGE=repo/frontend:local DEPLOY_HEALTH_ATTEMPTS=1 \
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

test_deploy_keeps_agent_runtime_images() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/runtime-images.XXXXXX")"
  docker_log="$run_dir/docker.log"
  PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    COMPOSE_OVERLAYS=docker-compose.subscription.yml \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null

  grep -Fqx \
    'pull ghcr.io/sageseekersociety/cheese/sandbox:testsha' "$docker_log" || \
    fail "deploy did not pull the sandbox image"
  grep -F 'create --name cheese-sandbox-image-retainer-next' "$docker_log" \
    >/dev/null || fail "deploy did not retain the sandbox image before pruning"

  promote_line="$(grep -nF \
    'rename cheese-sandbox-image-retainer-next cheese-sandbox-image-retainer' \
    "$docker_log" | cut -d: -f1)"
  prune_line="$(grep -nF 'image prune -af' "$docker_log" | cut -d: -f1)"
  [ -n "$promote_line" ] && [ -n "$prune_line" ] && \
    [ "$promote_line" -lt "$prune_line" ] || \
    fail "runtime image retainer was not promoted before image pruning"

  rm -rf "$run_dir"
  echo "PASS: deploy pulls, verifies, and retains agent runtime images"
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

test_app_only_deploy_does_not_require_agent_images() {
  mkdir -p "$ROOT/.tmp"
  run_dir="$(mktemp -d "$ROOT/.tmp/app-only-images.XXXXXX")"
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

  if grep -F 'pull ghcr.io/sageseekersociety/cheese/sandbox' "$docker_log" \
    >/dev/null; then
    fail "app-only deploy unexpectedly required an agent runtime image"
  fi
  rm -rf "$run_dir"
  echo "PASS: app-only deployment stays compatible with historical releases"
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
    FRONTEND_IMAGE=repo/frontend:testsha \
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
    FRONTEND_IMAGE=repo/frontend:testsha \
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
  if ! PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=healthy \
    APP_TIER_MAIN_SHA=testsha \
    APP_TIER_DOCKER_LOG="$docker_log" \
    APP_TIER_PULL_FAIL_COUNT=2 \
    APP_TIER_PULL_COUNTER="$run_dir/pull.count" \
    DEPLOY_PULL_BACKOFF_SECONDS="0 0" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1; then
    cat "$run_dir/out" >&2
    rm -rf "$run_dir"
    fail "deploy gave up on a pull that would have succeeded on retry"
  fi

  attempts="$(grep -c 'pull backend frontend$' "$docker_log" || true)"
  [ "$attempts" = 3 ] || fail "expected 3 pull attempts, saw $attempts"
  grep -q 'image pull: succeeded on attempt 3/3' "$run_dir/out" || \
    fail "the retry that finally worked was not reported"

  # The reclaim between attempts must actually precede the retry, not trail it.
  first_prune="$(grep -nF 'image prune -af' "$docker_log" | head -n 1 | cut -d: -f1)"
  last_pull="$(grep -n 'pull backend frontend$' "$docker_log" | tail -n 1 | cut -d: -f1)"
  [ -n "$first_prune" ] && [ "$first_prune" -lt "$last_pull" ] || \
    fail "no disk reclaim happened between the failed pull and the retry"
  rm -rf "$run_dir"
  echo "PASS: a transient pull failure is retried with a reclaim in between"
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
    DEPLOY_PULL_BACKOFF_SECONDS="0 0" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" > "$run_dir/out" 2>&1; then
    rm -rf "$run_dir"
    fail "deploy reported success after every pull attempt failed"
  fi

  grep -q 'image pull failed after 3 attempts' "$run_dir/out" || \
    fail "the failure did not name how many attempts were spent"
  grep -q 'attempt 1 exited 1; attempt 2 exited 1; attempt 3 exited 1' \
    "$run_dir/out" || fail "the failure did not report each attempt's outcome"
  if grep -F 'run --rm backend' "$docker_log" >/dev/null; then
    fail "deploy proceeded to migration after the pull was given up on"
  fi
  grep -F 'image prune -af' "$docker_log" >/dev/null || \
    fail "the abandoned deploy left its pulled layers on disk"
  rm -rf "$run_dir"
  echo "PASS: exhausted pull retries still abort the deploy, and still reclaim"
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
    BACKEND_IMAGE=repo/backend:testsha \
    FRONTEND_IMAGE=repo/frontend:testsha \
    DEPLOY_APP_IMAGE_SOURCE=local \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    rm -rf "$run_dir"
    fail "rollback scenario unexpectedly passed health checks"
  fi
  grep -Fqx \
    'compose-up-env BACKEND_IMAGE=repo/backend:oldsha FRONTEND_IMAGE=repo/frontend:oldsha IMAGE_TAG=oldsha' \
    "$docker_log" || fail "rollback did not restore the exact previous images"
  rm -rf "$run_dir"
  echo "PASS: rollback restores exact previous image references"
}

# A box with an api-front switch (ACTIVE_BACKEND_DIR) deploys the backend by
# rollout: the next container comes up and answers /healthz, api-front is
# pointed at it, the compose backend is recreated behind it, api-front is
# pointed back, the next container is removed. Every deploy used to cut the
# backend for the ~13 s a fresh container takes to boot; these two tests pin
# the order that removes that gap, and the one failure that must leave the
# running backend alone.
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
    DEPLOY_DRAIN_SECONDS=0 \
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
  : > "$dir/docker.log"
  printf '%s' "$dir"
}

# Nth log line matching a pattern (1-based), or "" if there is no Nth match.
nth_log_line() { grep -n -- "$2" "$1" 2>/dev/null | sed -n "${3}p" | cut -d: -f1 || true; }
last_log_line() { grep -n -- "$2" "$1" 2>/dev/null | tail -n 1 | cut -d: -f1 || true; }

test_rollout_keeps_a_backend_serving() {
  local run_dir docker_log next_up flip_to_next blue_up flip_back next_gone frontend_up
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  rollout_run "$run_dir" env >/dev/null 2>&1 || fail "rollout deploy did not succeed"
  next_up="$(log_line "$docker_log" 'run -d --no-deps --name cheese-backend-next -p 0.0.0.0:18082:8081 backend')"
  flip_to_next="$(nth_log_line "$docker_log" 'exec cheese-api-front nginx -s reload' 1)"
  blue_up="$(log_line "$docker_log" 'up -d --no-deps backend')"
  flip_back="$(nth_log_line "$docker_log" 'exec cheese-api-front nginx -s reload' 2)"
  next_gone="$(last_log_line "$docker_log" 'rm -f cheese-backend-next')"
  frontend_up="$(log_line "$docker_log" 'up -d --no-deps frontend')"
  [ -n "$next_up" ] || fail "rollout never started cheese-backend-next"
  [ -n "$flip_to_next" ] && [ -n "$flip_back" ] || fail "rollout did not reload api-front twice"
  [ -n "$blue_up" ] || fail "rollout never recreated the compose backend"
  [ -n "$frontend_up" ] || fail "rollout never brought the frontend up"
  [ "$next_up" -lt "$flip_to_next" ] || fail "api-front was reloaded before the next backend existed"
  [ "$flip_to_next" -lt "$blue_up" ] || fail "the compose backend was recreated before traffic had moved off it"
  [ "$blue_up" -lt "$flip_back" ] || fail "api-front was pointed back before the compose backend was recreated"
  [ "$flip_back" -lt "$next_gone" ] || fail "cheese-backend-next was removed while api-front still pointed at it"
  [ "$next_gone" -lt "$frontend_up" ] || fail "the frontend came up before the backend rollout finished"
  grep -Fqx 'upstream backend_active { server 127.0.0.1:18081; }' "$run_dir/active/backend.conf" \
    || fail "api-front was left pointing away from the compose backend: $(cat "$run_dir/active/backend.conf")"
  [ "$(ls "$run_dir/active" | wc -l | tr -d ' ')" = 1 ] \
    || fail "the switch directory holds leftovers: $(ls "$run_dir/active")"
  rm -rf "$run_dir"
  echo "PASS: rollout keeps a healthy backend behind api-front throughout"
}

test_frontend_rollout_keeps_serving() {
  local run_dir docker_log next_up flip recreate flip_back gone
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 8080
  grep -Fq 'location = /api/connector/agent' "$run_dir/active/sites-frontend.conf" \
    || fail "stable frontend ingress still sends public device sockets through the rolling frontend"
  grep -Fq 'location ~ ^/api/topics/[^/]+/execution/[^/]+$' "$run_dir/active/sites-frontend.conf" \
    || fail "stable frontend ingress still sends public execution through the rolling frontend"
  rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" >"$run_dir/deploy.log" 2>&1 || { cat "$run_dir/deploy.log"; fail "frontend rollout failed"; }
  next_up="$(log_line "$docker_log" 'run -d --no-deps --name cheese-frontend-next')"
  flip="$(nth_log_line "$docker_log" 'exec cheese-api-front nginx -s reload' 3)"
  recreate="$(log_line "$docker_log" 'up -d --no-deps frontend')"
  flip_back="$(nth_log_line "$docker_log" 'exec cheese-api-front nginx -s reload' 4)"
  gone="$(last_log_line "$docker_log" 'rm -f cheese-frontend-next')"
  [ -n "$next_up" ] && [ -n "$flip" ] && [ -n "$flip_back" ] || fail "missing frontend switches"
  [ "$next_up" -lt "$flip" ] && [ "$flip" -lt "$recreate" ] && [ "$recreate" -lt "$flip_back" ] && [ "$flip_back" -lt "$gone" ] || fail "frontend replaced before traffic moved"
  grep -Fq 'server 127.0.0.1:8080;' "$run_dir/active/sites-frontend.conf" || fail "frontend proxy did not return to compose"
  rm -rf "$run_dir"
  echo "PASS: frontend stays behind a healthy proxy target across recreate"
}

test_frontend_rollout_rejects_unhealthy_next() {
  local run_dir
  run_dir="$(new_rollout_run_dir)"
  bash "$ROOT/deploy/llm-tunnel/configure-frontend.sh" "$run_dir/active" 8080
  if rollout_run "$run_dir" env ACTIVE_FRONTEND_DIR="$run_dir/active" APP_TIER_CURL_FAIL_MATCH=:18084/ >/dev/null 2>&1; then
    fail "unhealthy frontend was accepted"
  fi
  ! grep -q 'up -d --no-deps frontend' "$run_dir/docker.log" || fail "old frontend was replaced without a healthy successor"
  grep -Fq 'server 127.0.0.1:8080;' "$run_dir/active/sites-frontend.conf" || fail "frontend proxy moved to unhealthy successor"
  rm -rf "$run_dir"
  echo "PASS: failed frontend startup leaves the old frontend serving"
}

test_rollout_leaves_the_running_backend_alone_when_next_never_comes_up() {
  local run_dir docker_log
  run_dir="$(new_rollout_run_dir)"
  docker_log="$run_dir/docker.log"
  if rollout_run "$run_dir" env APP_TIER_CURL_FAIL_MATCH=:18082/ >/dev/null 2>&1; then
    fail "rollout succeeded although the next backend never answered /healthz"
  fi
  grep -q 'run -d --no-deps --name cheese-backend-next' "$docker_log" \
    || fail "the next backend was never started"
  ! grep -q 'up -d --no-deps backend' "$docker_log" \
    || fail "the running backend was recreated although nothing healthy could replace it"
  ! grep -q 'exec cheese-api-front nginx -s reload' "$docker_log" \
    || fail "api-front was reloaded although the next backend was unhealthy"
  grep -Fqx 'upstream backend_active { server 127.0.0.1:18081; }' "$run_dir/active/backend.conf" \
    || fail "api-front was moved off the running backend"
  [ "$(last_log_line "$docker_log" 'rm -f cheese-backend-next')" -gt "$(log_line "$docker_log" 'run -d --no-deps')" ] \
    || fail "the failed next backend was not cleaned up"
  rm -rf "$run_dir"
  echo "PASS: an unhealthy next backend leaves the running one untouched"
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
    FRONTEND_IMAGE=repo/frontend:testsha \
    DEPLOY_APP_IMAGE_SOURCE=local \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    WORKSPACES_HOST_PATH="$run_dir/workspaces" \
    UPLOADS_HOST_PATH="$run_dir/uploads" \
    APPHOME_HOST_PATH="$run_dir/apphome" \
    VIKING_HOST_PATH="$run_dir/viking" \
    CLAUDE_CACHE_HOST_PATH="$run_dir/claude-cache" \
    TRANSCRIPTS_HOST_PATH="$run_dir/transcripts" \
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
  # viking is deliberately NOT created: the deploy script makes it, and these
  # tests are the only place that would notice if it stopped.
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
  mkdir -p "$run_dir/viking"; : > "$run_dir/viking/.cheese-uid-1000"
  mkdir -p "$run_dir/claude-cache"; : > "$run_dir/claude-cache/.cheese-uid-1000"
  mkdir -p "$run_dir/transcripts"; : > "$run_dir/transcripts/.cheese-uid-1000"

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
    APP_TIER_REQUIRE_SHORT7=true \
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

test_workflow_rejects_stale_frontend() {
  script="$(workflow_script)"
  if PATH="$FAKE_BIN:$PATH" \
    APP_TIER_SCENARIO=frontend_stale \
    APP_TIER_MAIN_SHA=abc1234 \
    bash -c "$script"; then
    fail "workflow reported success with a stale frontend"
  fi
  echo "PASS: workflow drift check rejects a stale frontend"
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

case "$CASE" in
  deploy) test_deploy_rejects_absent_frontend ;;
  deploy-healthy) test_deploy_accepts_healthy_pair ;;
  connection-owner) test_deploy_keeps_connection_owner_running ;;
  connection-owner-local) test_local_deploy_installs_owner_from_verified_backend_image ;;
  connection-route) test_rollout_installs_connection_route_without_recreating_api_front ;;
  cloud-control-release) test_cloud_control_has_an_independent_drained_release ;;
  runtime-images) test_deploy_keeps_agent_runtime_images ;;
  ci-service-images) test_deploy_retains_ci_service_images ;;
  app-only) test_app_only_deploy_does_not_require_agent_images ;;
  local-images) test_local_app_images_skip_registry_pull ;;
  local-images-missing) test_local_app_images_must_exist ;;
  rollback-images) test_rollback_restores_exact_previous_images ;;
  ownership-order) test_ownership_handover_is_the_last_step_before_up ;;
  ownership-rollback) test_rollback_hands_the_mounts_back ;;
  ownership-rollback-noop) test_rollback_leaves_an_already_migrated_box_alone ;;
  pull-retry) test_pull_retries_transient_failure ;;
  pull-exhausted) test_exhausted_pull_retries_still_fail_the_deploy ;;
  failed-reclaim) test_failed_deploy_reclaims_disk ;;
  disk-watermark) test_deploy_logs_disk_watermarks ;;
  operator) test_operator_rejects_stale_frontend ;;
  operator-sha-width) test_operator_uses_registry_sha_width ;;
  workflow) test_workflow_rejects_stale_frontend ;;
  healthy) test_healthy_current_pair_passes ;;
  rollout) test_rollout_keeps_a_backend_serving ;;
  frontend-rollout) test_frontend_rollout_keeps_serving ;;
  frontend-rollout-unhealthy) test_frontend_rollout_rejects_unhealthy_next ;;
  rollout-unhealthy-next) test_rollout_leaves_the_running_backend_alone_when_next_never_comes_up ;;
  all)
    test_deploy_rejects_absent_frontend
    test_deploy_accepts_healthy_pair
    test_deploy_keeps_connection_owner_running
    test_local_deploy_installs_owner_from_verified_backend_image
    test_owner_release_reuses_box_config_and_stops_when_busy
    test_cloud_control_has_an_independent_drained_release
    test_rollout_installs_connection_route_without_recreating_api_front
    test_deploy_keeps_agent_runtime_images
    test_deploy_retains_ci_service_images
    test_app_only_deploy_does_not_require_agent_images
    test_local_app_images_skip_registry_pull
    test_local_app_images_must_exist
    test_rollback_restores_exact_previous_images
    test_ownership_handover_is_the_last_step_before_up
    test_rollback_hands_the_mounts_back
    test_rollback_leaves_an_already_migrated_box_alone
    test_pull_retries_transient_failure
    test_exhausted_pull_retries_still_fail_the_deploy
    test_failed_deploy_reclaims_disk
    test_deploy_logs_disk_watermarks
    test_operator_rejects_stale_frontend
    test_operator_uses_registry_sha_width
    test_workflow_rejects_stale_frontend
    test_healthy_current_pair_passes
    test_rollout_keeps_a_backend_serving
    test_frontend_rollout_keeps_serving
    test_frontend_rollout_rejects_unhealthy_next
    test_rollout_leaves_the_running_backend_alone_when_next_never_comes_up
    ;;
  *) fail "unknown case: $CASE" ;;
esac
