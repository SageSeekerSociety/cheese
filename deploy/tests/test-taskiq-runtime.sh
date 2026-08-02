#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${1:-all}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

test_import() {
  (
    cd "$ROOT/backend"
    uv run python - <<'PY'
import worker
from app.core.taskiq_broker import broker

tasks = broker.get_all_tasks()
required = {
    "notification_aggregation_finalize",
    "task_deadline_check",
    "process_email_queue",
    "taskiq_runtime_heartbeat",
}
assert required <= tasks.keys(), (required, tasks.keys())
PY
  )
  echo "PASS: production worker entry point imports and registers scheduled jobs"
}

test_compose() {
  rendered="$({
    BACKEND_ENV_FILE=/dev/null \
      docker compose -f "$ROOT/deploy/compose/docker-compose.base.yml" \
      config --format json
  })"
  RENDERED_COMPOSE="$rendered" python - <<'PY'
import json
import os

services = json.loads(os.environ["RENDERED_COMPOSE"])["services"]
expected = {
    "taskiq-worker": ["taskiq", "worker", "worker:broker"],
    "taskiq-scheduler": ["taskiq", "scheduler", "worker:scheduler"],
}
for name, command in expected.items():
    assert name in services, f"missing Compose service {name}"
    assert services[name].get("command") == command, (
        name,
        services[name].get("command"),
    )
    assert "healthcheck" in services[name], f"{name} has no effect healthcheck"
PY
  echo "PASS: Compose renders worker and scheduler with heartbeat healthchecks"
}

test_deploy() {
  mkdir -p "$ROOT/tmp"
  run_dir="$(mktemp -d "$ROOT/tmp/taskiq-deploy.XXXXXX")"
  trap 'rm -rf "$run_dir"' RETURN
  : > "$run_dir/docker.log"
  PATH="$ROOT/deploy/tests/fakes/taskiq:$PATH" \
    FAKE_DOCKER_LOG="$run_dir/docker.log" \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null

  grep -Eq 'pull .*taskiq-worker .*taskiq-scheduler' "$run_dir/docker.log" ||
    fail "deploy did not pull worker and scheduler"
  grep -Eq 'up -d .*taskiq-worker .*taskiq-scheduler' "$run_dir/docker.log" ||
    fail "deploy did not start worker and scheduler"
  grep -Eq 'exec -T taskiq-worker .*taskiq_health' "$run_dir/docker.log" ||
    fail "deploy did not require a fresh effect heartbeat"

  if PATH="$ROOT/deploy/tests/fakes/taskiq:$PATH" \
    FAKE_DOCKER_LOG="$run_dir/docker.log" \
    FAKE_TASKIQ_HEALTH=fail \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    fail "deploy reported success without a worker heartbeat"
  fi

  if PATH="$ROOT/deploy/tests/fakes/taskiq:$PATH" \
    FAKE_DOCKER_LOG="$run_dir/docker.log" \
    FAKE_TASKIQ_SCHEDULER=missing \
    DEPLOY_HEALTH_ATTEMPTS=1 \
    DEPLOY_HEALTH_INTERVAL_SECONDS=0 \
    HOME="$run_dir" \
    "$ROOT/deploy/deploy-docker.sh" testsha \
      "$ROOT/deploy/compose/docker-compose.base.yml" >/dev/null 2>&1; then
    fail "deploy reported success without a running scheduler"
  fi
  echo "PASS: deploy starts Taskiq runtime and gates success on its heartbeat"
}

case "$CASE" in
  import) test_import ;;
  compose) test_compose ;;
  deploy) test_deploy ;;
  all)
    test_import
    test_compose
    test_deploy
    ;;
  *) fail "unknown case: $CASE" ;;
esac
