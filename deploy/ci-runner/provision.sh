#!/usr/bin/env bash
# Provision a MicroCloud machine as a cheese GitHub Actions runner.
# Usage (on the machine): bash provision.sh <runner-name> <reg-token> [version] [slot]
set -euo pipefail

NAME="${1:?usage: provision-ci-runner.sh <name> <reg-token> [version] [slot]}"
TOKEN="${2:?registration token required}"
VER="${3:-2.336.0}"
# Which runner slot on this machine this installation is. A machine's slots share
# one Postgres, one Valkey and one set of host ports, so every job has to know
# which of them it may use — see backend/tests/isolation.py. Slot 0 lives in
# ~/actions-runner and each further slot in ~/actions-runner-<slot>, which also
# gives it its own RUNNER_TEMP and therefore its own uv venv and Cargo target.
SLOT="${4:-0}"
# Resolved before the cd below: the files this script installs sit beside it,
# and $0 is relative to wherever it was invoked from.
BUNDLE="$(cd "$(dirname "$0")" && pwd)"
REPO_URL="https://github.com/SageSeekerSociety/cheese"
ROOT="$HOME/actions-runner"
[ "$SLOT" = 0 ] || ROOT="$HOME/actions-runner-$SLOT"

mkdir -p "$ROOT" && cd "$ROOT"
if [ ! -f config.sh ]; then
  curl -sfL -o r.tar.gz "https://github.com/actions/runner/releases/download/v${VER}/actions-runner-linux-x64-${VER}.tar.gz"
  tar xzf r.tar.gz && rm r.tar.gz
fi
sudo ./bin/installdependencies.sh >/dev/null 2>&1 || true

# docker group so service containers work without sudo
sudo usermod -aG docker "$(whoami)" 2>/dev/null || true

./config.sh --unattended --url "$REPO_URL" --token "$TOKEN" \
  --name "$NAME" --labels cheese-ci --replace 2>&1 | tail -3

# Cache downloaded action archives across jobs. Without this the runner pulls
# every action's tarball from codeload.github.com on EVERY job; with three
# runners behind one exit IP a busy afternoon trips GitHub's anonymous rate
# limit and jobs die at checkout with 429 before running anything (#562,
# 2026-08-17 — the main source of that day's "randomly red" CI).
mkdir -p "$ROOT/action-archive-cache"
grep -q ACTIONS_RUNNER_ACTION_ARCHIVE_CACHE .env 2>/dev/null || \
  echo "ACTIONS_RUNNER_ACTION_ARCHIVE_CACHE=$ROOT/action-archive-cache" >> .env

# Clear what a killed job left, before the next job touches the workspace. A
# forwarded-project mount whose server was killed stays there answering ENOTCONN,
# and `actions/checkout` walks the workspace before cloning — one left behind on
# 2026-09-15 wedged every later job on that machine in a 15-minute timeout. The
# run that leaves one cannot clean up after itself (it was killed), so the clean-up
# belongs to whoever comes next.
install -m 0755 "$BUNDLE/job-started-hook.sh" "$ROOT/job-started-hook.sh"
install -m 0755 "$BUNDLE/disk-guard.sh" "$ROOT/disk-guard.sh"
grep -q ACTIONS_RUNNER_HOOK_JOB_STARTED .env 2>/dev/null || \
  echo "ACTIONS_RUNNER_HOOK_JOB_STARTED=$ROOT/job-started-hook.sh" >> .env

# Every job reads its slot from here; the .env of a runner installation becomes
# the environment of the jobs it runs.
grep -q CHEESE_CI_SLOT .env 2>/dev/null || echo "CHEESE_CI_SLOT=$SLOT" >> .env

# `pytest -n auto` sizes itself to the whole machine, which is right for one slot
# and twice the cores for two. Declared here because the job cannot see how many
# slots its machine has; a one-slot machine leaves it unset and keeps `auto`.
if [ "$SLOT" != 0 ] || [ -d "$HOME/actions-runner-1" ]; then
  cores="$(nproc 2>/dev/null || echo 8)"
  grep -q CHEESE_CI_TEST_WORKERS .env 2>/dev/null || \
    echo "CHEESE_CI_TEST_WORKERS=$(( cores / 2 > 0 ? cores / 2 : 1 ))" >> .env
fi

# The machine's own Postgres and Valkey, shared by its slots. Idempotent, so this
# is both the first-time setup and the repair after a prune took them away.
bash "$BUNDLE/resident-services.sh"

sudo ./svc.sh install "$(whoami)" 2>&1 | tail -1
sudo ./svc.sh start 2>&1 | tail -1

# Resilience: a crashed/OOM-killed runner must come back by itself — the dev-box
# runner once died silently for 25+ hours after an OOM kill (2026-08-07).
# By name, not the first one listed: a machine with a second slot has two runner
# units, and the first would be the other slot's.
UNIT="$(systemctl list-units --all 'actions.runner.*.service' --no-legend \
  | awk '{print $1}' | grep -F ".$NAME." | head -1 || true)"
if [ -z "$UNIT" ]; then
  # Nothing to harden and nothing to restart; saying so beats dying with `set -e`
  # after the runner is already registered and configured.
  echo "unit=none (no systemd unit for $NAME)"
else
  sudo mkdir -p "/etc/systemd/system/${UNIT}.d"
  printf '[Service]\nRestart=always\nRestartSec=10\nOOMPolicy=continue\n' | sudo tee "/etc/systemd/system/${UNIT}.d/override.conf" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl restart "$UNIT"
  echo "unit=${UNIT} active=$(systemctl is-active "$UNIT")"
fi
