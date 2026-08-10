#!/usr/bin/env bash
# Build deps a cheese CI runner needs beyond the template (docker ships already):
# - build-essential/pkg-config/libssl-dev + rustup: uv sync compiles the local
#   maturin/pyo3 crate (srp_rs) from source on every fresh venv.
# - a weekly docker prune cron: no deploys run here, so nothing else ever
#   reclaims layer space on the 40G disk.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential pkg-config libssl-dev git curl >/dev/null
if ! command -v cargo >/dev/null; then
  curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal >/dev/null 2>&1
fi
grep -q ".cargo/env" ~/.bashrc || echo '. "$HOME/.cargo/env"' >> ~/.bashrc
( crontab -l 2>/dev/null | grep -v "docker system prune" ; echo '0 5 * * 0 docker system prune -af --filter until=168h >/dev/null 2>&1' ) | crontab -
echo "deps ok: gcc=$(gcc --version | head -c 20) cargo=$(bash -lc 'cargo --version' 2>/dev/null | head -c 20)"
