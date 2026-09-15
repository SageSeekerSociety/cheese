#!/usr/bin/env bash
# Reclaim a cheese-ci machine's disk when it is running out, called before a job.
#
# Nothing else reclaims here: a pool machine only ever adds — images pulled for
# service containers, buildx layers, uv venvs, cargo targets. The weekly prune
# cannot help, because it keeps `--filter until=168h` and what fills the disk is
# what this week's jobs just pulled. Twice on 2026-09-15 a machine reached 100%
# and dropped out of the pool: the runner listener cannot write its own log file,
# so it crash-loops and GitHub shows the machine offline while its service claims
# to be active. CI then looks merely slow.
#
# Triggered on free space rather than on a clock, so an idle machine pays a single
# `df` and a busy one reclaims as often as it needs to.
#
# `prune -af` keeps every image a RUNNING container uses — but "not running yet"
# is not the same as "not wanted". `remote-execution.yml` BUILDS
# `cheese-private-executor` and runs it a few steps later; on 2026-09-15 a guard
# firing in this machine's other slot deleted it in between, and the job died on
# `docker run … exit status 125` with nothing to re-pull, because the image was
# never fetched from anywhere. So only images older than the grace period go: a
# job's own build is minutes old, and what actually fills a pool machine is older
# than that.
set -uo pipefail

FREE_FLOOR_GB="${CHEESE_CI_FREE_FLOOR_GB:-10}"
# Long enough to outlive any job on this pool (the longest timeout is 20 minutes),
# short enough that yesterday's layers are still reclaimable.
KEEP_NEWER_THAN="${CHEESE_CI_KEEP_NEWER_THAN:-2h}"

free_gb() {
  df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9'
}

before="$(free_gb)"
[ -n "$before" ] || exit 0
[ "$before" -lt "$FREE_FLOOR_GB" ] || exit 0

echo "disk guard: ${before}G free is below ${FREE_FLOOR_GB}G, reclaiming"
docker system prune -af --filter "until=$KEEP_NEWER_THAN" >/dev/null 2>&1 || true
docker volume prune -f >/dev/null 2>&1 || true
after="$(free_gb)"
echo "disk guard: ${before}G -> ${after:-?}G free"
exit 0
