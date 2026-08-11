#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${1:-all}"
FAKE_BIN="$ROOT/deploy/tests/fakes/app-tier"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

test_deploy_rejects_absent_frontend() {
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/app-tier-deploy.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/app-tier-deploy.XXXXXX")"
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

test_deploy_keeps_agent_runtime_images() {
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/runtime-images.XXXXXX")"
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
    fail "deploy did not pull the SDK sandbox image"
  grep -Fqx \
    'pull ghcr.io/sageseekersociety/cheese/sandbox-tmux:testsha' "$docker_log" || \
    fail "deploy did not pull the tmux sandbox image"
  grep -F \
    'run --rm --entrypoint sh ghcr.io/sageseekersociety/cheese/sandbox-tmux:testsha' \
    "$docker_log" >/dev/null || fail "deploy did not smoke-test the tmux image"
  grep -F 'create --name cheese-tmux-image-retainer-next' "$docker_log" \
    >/dev/null || fail "deploy did not retain the tmux image before pruning"

  promote_line="$(grep -nF \
    'rename cheese-tmux-image-retainer-next cheese-tmux-image-retainer' \
    "$docker_log" | cut -d: -f1)"
  prune_line="$(grep -nF 'image prune -af' "$docker_log" | cut -d: -f1)"
  [ -n "$promote_line" ] && [ -n "$prune_line" ] && \
    [ "$promote_line" -lt "$prune_line" ] || \
    fail "runtime image retainer was not promoted before image pruning"

  rm -rf "$run_dir"
  echo "PASS: deploy pulls, verifies, and retains agent runtime images"
}

test_deploy_retains_ci_service_images() {
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/ci-service-images.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/app-only-images.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/local-app-images.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/local-app-missing.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/pull-retry.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/pull-exhausted.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/failed-reclaim.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/disk-watermark.XXXXXX")"
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
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/exact-rollback.XXXXXX")"
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
    HOME="$run_dir" \
    "$@"
}

# Line number of the first log line matching a pattern, or "" if absent. The
# `|| true` is load-bearing: this file runs under `set -e -o pipefail`, so a
# no-match grep would kill the run silently and a regression would read as a
# crash with no message instead of a named failure.
log_line() { grep -n -- "$2" "$1" 2>/dev/null | head -n 1 | cut -d: -f1 || true; }

new_ownership_run_dir() {
  mkdir -p "$ROOT/tmp"
  local dir
  dir="$(mktemp -d "$ROOT/tmp/ownership-order.XXXXXX")"
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

workflow_script() {
  (
    cd "$ROOT/backend"
    uv run python - "$ROOT/.github/workflows/deploy-drift.yml" <<'PY'
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
  )
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
  all)
    test_deploy_rejects_absent_frontend
    test_deploy_accepts_healthy_pair
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
    ;;
  *) fail "unknown case: $CASE" ;;
esac
