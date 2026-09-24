#!/usr/bin/env bash
# Build deps a cheese CI runner needs beyond the template (docker ships already):
# - build-essential: `go test -race` in cli.yml needs gcc.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential git curl lsof qemu-guest-agent >/dev/null
# The guest agent lets the Proxmox host read the VM's disk usage and run fstrim
# without anyone logging in (the runners' disks are thin-provisioned with
# discard=on since 2026-09-03, and nobody with sudo can otherwise reach them).
# It only talks to the host once the VM config carries `agent: 1`.
sudo systemctl enable --now qemu-guest-agent >/dev/null 2>&1 || true
bash "$(dirname "$0")/retire-docker-prune.sh" \
  "$HOME/ci-maintenance-backups/prune-retirement-$(date -u +%Y%m%dT%H%M%S)-$$"
echo "deps ok: gcc=$(gcc --version | head -c 20)"
