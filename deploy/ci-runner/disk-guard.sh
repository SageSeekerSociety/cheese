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
# `prune -af` keeps every image a RUNNING container uses, so a job in this
# machine's other slot keeps its service containers. What it can cost that job is
# an image pulled but not yet started, which is a re-pull, not a failure.
set -uo pipefail

FREE_FLOOR_GB="${CHEESE_CI_FREE_FLOOR_GB:-10}"

free_gb() {
  df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9'
}

before="$(free_gb)"
[ -n "$before" ] || exit 0
[ "$before" -lt "$FREE_FLOOR_GB" ] || exit 0

echo "disk guard: ${before}G free is below ${FREE_FLOOR_GB}G, reclaiming"
docker system prune -af >/dev/null 2>&1 || true
docker volume prune -f >/dev/null 2>&1 || true
after="$(free_gb)"
echo "disk guard: ${before}G -> ${after:-?}G free"
exit 0
