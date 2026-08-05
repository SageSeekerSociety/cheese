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

workflow_script() {
  (
    cd "$ROOT/backend"
    uv run python - "$ROOT/.github/workflows/deploy-drift.yml" <<'PY'
import sys
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text())
for step in workflow["jobs"]["drift"]["steps"]:
    if step.get("name") == "Compare main with what is running":
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
  operator) test_operator_rejects_stale_frontend ;;
  workflow) test_workflow_rejects_stale_frontend ;;
  healthy) test_healthy_current_pair_passes ;;
  all)
    test_deploy_rejects_absent_frontend
    test_deploy_accepts_healthy_pair
    test_operator_rejects_stale_frontend
    test_workflow_rejects_stale_frontend
    test_healthy_current_pair_passes
    ;;
  *) fail "unknown case: $CASE" ;;
esac
