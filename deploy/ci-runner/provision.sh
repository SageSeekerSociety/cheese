#!/usr/bin/env bash
# Provision a MicroCloud machine as a cheese GitHub Actions runner.
# Usage (on the machine): bash provision-ci-runner.sh <runner-name> <reg-token> [runner-version]
set -euo pipefail

NAME="${1:?usage: provision-ci-runner.sh <name> <reg-token> [version]}"
TOKEN="${2:?registration token required}"
VER="${3:-2.336.0}"
REPO_URL="https://github.com/SageSeekerSociety/cheese"

mkdir -p ~/actions-runner && cd ~/actions-runner
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
mkdir -p "$HOME/actions-runner/action-archive-cache"
grep -q ACTIONS_RUNNER_ACTION_ARCHIVE_CACHE .env 2>/dev/null || \
  echo "ACTIONS_RUNNER_ACTION_ARCHIVE_CACHE=$HOME/actions-runner/action-archive-cache" >> .env

# Clear what a killed job left, before the next job touches the workspace. A
# forwarded-project mount whose server was killed stays there answering ENOTCONN,
# and `actions/checkout` walks the workspace before cloning — one left behind on
# 2026-09-15 wedged every later job on that machine in a 15-minute timeout. The
# run that leaves one cannot clean up after itself (it was killed), so the clean-up
# belongs to whoever comes next.
install -m 0755 "$(dirname "$0")/job-started-hook.sh" "$HOME/actions-runner/job-started-hook.sh"
install -m 0755 "$(dirname "$0")/disk-guard.sh" "$HOME/actions-runner/disk-guard.sh"
grep -q ACTIONS_RUNNER_HOOK_JOB_STARTED .env 2>/dev/null || \
  echo "ACTIONS_RUNNER_HOOK_JOB_STARTED=$HOME/actions-runner/job-started-hook.sh" >> .env

sudo ./svc.sh install "$(whoami)" 2>&1 | tail -1
sudo ./svc.sh start 2>&1 | tail -1

# Resilience: a crashed/OOM-killed runner must come back by itself — the dev-box
# runner once died silently for 25+ hours after an OOM kill (2026-08-07).
UNIT="$(systemctl list-units --all 'actions.runner.*.service' --no-legend | awk '{print $1}' | head -1)"
sudo mkdir -p "/etc/systemd/system/${UNIT}.d"
printf '[Service]\nRestart=always\nRestartSec=10\nOOMPolicy=continue\n' | sudo tee "/etc/systemd/system/${UNIT}.d/override.conf" >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart "$UNIT"
echo "unit=${UNIT} active=$(systemctl is-active "$UNIT")"
