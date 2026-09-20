#!/usr/bin/env bash
set -euo pipefail
task_root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$task_root/logs/cloud-warm"
log="$task_root/logs/cloud-warm/remote-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$log") 2>&1
printf '%s syncing isolated test checkouts\n' "$(date -u +%FT%TZ)"
ssh mac-mini 'mkdir -p ~/Projects/cheese-cloud-warm-test ~/Projects/micro-cloud-warm-test'
rsync -az --exclude=.git --exclude=.venv --exclude=node_modules --exclude=target --exclude=logs --exclude=tmp --exclude=.env "$task_root/" mac-mini:Projects/cheese-cloud-warm-test/
rsync -az --exclude=.git --exclude=target --exclude=node_modules --exclude=.env "$task_root/../micro-cloud-warm/" mac-mini:Projects/micro-cloud-warm-test/
ssh mac-mini 'bash ~/Projects/micro-cloud-warm-test/scripts/test-warm-pool.sh'
rsync -az mac-mini:Projects/micro-cloud-warm-test/backend/src/main/kotlin/app/microteams/microcloud/api/ "$task_root/../micro-cloud-warm/backend/src/main/kotlin/app/microteams/microcloud/api/"
rsync -az mac-mini:Projects/micro-cloud-warm-test/backend/src/main/kotlin/app/microteams/microcloud/model/ "$task_root/../micro-cloud-warm/backend/src/main/kotlin/app/microteams/microcloud/model/"
