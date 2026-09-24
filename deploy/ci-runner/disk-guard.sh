#!/usr/bin/env bash
# Reclaim a cheese-ci machine's disk when it is running out, called before a job.
#
# Pool machines accumulate images, buildx layers and uv venvs.
# Twice on 2026-09-15 a machine reached 100%
# and dropped out of the pool: the runner listener cannot write its own log file,
# so it crash-loops and GitHub shows the machine offline while its service claims
# to be active. CI then looks merely slow.
#
# Triggered on free space rather than on a clock, so an idle machine pays a single
# `df` and a busy one reclaims as often as it needs to.
#
# Never prune the shared Docker daemon here: another slot may be pulling an
# image before it has a container. On 2026-09-23 a concurrent Docker cleanup
# overlapped a pull whose layer, snapshot and lease then disappeared. The logs
# establish the overlap, not the operation that deleted the content.
# Docker reclamation requires maintenance with both runner slots drained.
#
# The tiers below reclaim host caches, cheapest first, and stop as soon as the
# machine is over the floor — every tier deleted is one the next job pays to
# rebuild, and a machine that is already clear has nothing to buy with that.
#
# What is deliberately NOT here, in descending order of how much it would free:
#   ~/.cache/uv          the wheel cache every `uv sync` on this box is built
#                        out of. Deleting it costs 356 MiB off the network and
#                        two minutes on the NEXT job, not this one — it is the
#                        single most expensive thing on the machine to rebuild.
#   ~/.cache/ms-playwright   browser binaries; `pnpm exec playwright install`
#                        refetches them, but nothing in e2e asks it to.
#   ~/setup-pnpm         pnpm itself, installed by pnpm/action-setup.
# A machine that stays under the floor after every tier needs a bigger disk, not a
# deeper tier: MicroCloud cannot resize, so that means asking Lg for capacity.
set -uo pipefail

FREE_FLOOR_GB="${CHEESE_CI_FREE_FLOOR_GB:-10}"
# Runner logs are the one thing here nothing else writes, so a few days of them
# are worth keeping: they are how a machine that dropped out of the pool gets
# diagnosed after the fact.
KEEP_DIAG_DAYS="${CHEESE_CI_KEEP_DIAG_DAYS:-3}"

free_gb() {
  df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9'
}

before="$(free_gb)"
[ -n "$before" ] || exit 0
[ "$before" -lt "$FREE_FLOOR_GB" ] || exit 0

echo "disk guard: ${before}G free is below ${FREE_FLOOR_GB}G, reclaiming"

# Each tier runs only if the ones before it did not get the machine over the
# floor. `done_here` is checked between tiers rather than inside them: a tier is
# one decision, and a half-applied one leaves a state nobody can reason about.
done_here() {
  now="$(free_gb)"
  [ -n "$now" ] && [ "$now" -ge "$FREE_FLOOR_GB" ]
}

tier() {
  name="$1"
  shift
  if done_here; then
    return 0
  fi
  "$@" >/dev/null 2>&1
  echo "disk guard: $name -> $(free_gb)G free"
}

# The runner never rotates these. Seen on all three machines 2026-09-17: 3903
# files spanning a month, ~780 MB per runner instance, two instances per box.
reclaim_diag() {
  find "$HOME"/actions-runner*/_diag -maxdepth 1 -type f -name '*.log' \
    -mtime +"$KEEP_DIAG_DAYS" -delete
}

# Another runner slot can be installing packages. Use ensure-apt.sh's host
# lock and skip this tier while it is held, preserving that install's indexes.
reclaim_apt() {
  flock -n "${CHEESE_APT_LOCK:-/tmp/cheese-ci-apt.lock}" \
    sudo -n sh -c 'apt-get clean && rm -rf /var/lib/apt/lists/*'
}

# npm's content-addressed cache, ~1.5 GB per box and growing: `npm install -g
# @anthropic-ai/claude-code` in cli.yml and claude-canary.yml never prunes it.
# pnpm's store is left alone — it is what frontend and e2e install out of.
reclaim_npm() {
  rm -rf "$HOME/.npm/_cacache"
}

# `_work/uv-venv` is the path #1104 stopped writing to, 1.3 GB a copy. A PR
# branched before #1104 still runs the old workflow and will rebuild its venv,
# which is the ordinary price of this guard firing.
reclaim_stale_work() {
  rm -rf "$HOME"/actions-runner*/_work/uv-venv
}

tier "runner logs" reclaim_diag
tier "apt" reclaim_apt
tier "npm cache" reclaim_npm
tier "stale work dirs" reclaim_stale_work

after="$(free_gb)"
echo "disk guard: ${before}G -> ${after:-?}G free"
if [ -n "$after" ] && [ "$after" -lt "$FREE_FLOOR_GB" ]; then
  echo "disk guard: still under ${FREE_FLOOR_GB}G after every tier — this machine needs a bigger disk"
fi
exit 0
