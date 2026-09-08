#!/usr/bin/env bash
set -euo pipefail
task_root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$task_root/logs/cloud-warm"
log="$task_root/logs/cloud-warm/backend-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$log") 2>&1
printf '%s starting isolated backend tests\n' "$(date -u +%FT%TZ)"
if ! docker container inspect cheese-cloud-warm-test-pg >/dev/null 2>&1; then
  docker run -d --name cheese-cloud-warm-test-pg --memory=384m --cpus=0.5 \
    -p 127.0.0.1:15447:5432 -e POSTGRES_PASSWORD=postgres \
    postgres:16@sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94
fi
docker start cheese-cloud-warm-test-pg >/dev/null
for attempt in {1..30}; do
  docker exec cheese-cloud-warm-test-pg pg_isready -U postgres && break
  sleep 1
done
export TEST_PG_BASE=postgresql+asyncpg://postgres:postgres@127.0.0.1:15447
cd "$task_root/backend"
uv sync --frozen --group dev
uv run alembic heads
uv run pytest -q -n 0 tests/integration/test_cloud_warm_pool.py \
  tests/integration/test_project_machines.py tests/unit/test_machine_service.py \
  tests/unit/test_machine_enrollment.py tests/unit/test_machine_reconcile.py \
  tests/unit/test_cloud_provider.py tests/unit/test_cloud_wakeup.py \
  tests/unit/test_domain_import_guard.py tests/unit/test_periodic_jobs.py \
  tests/unit/test_cloud_claude_transfer.py tests/unit/test_enrollment_installs_claude.py
printf '%s backend tests complete\n' "$(date -u +%FT%TZ)"
