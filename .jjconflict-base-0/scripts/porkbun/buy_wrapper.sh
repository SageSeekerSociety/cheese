#!/usr/bin/env bash
set -euo pipefail
IFS= read -r PB_PASS || true
export PB_PASS
cd /Users/andyl/Projects/cheese-backend-py/tmp/cheesex/backend
exec uv run --with playwright python ../scripts/porkbun/buy.py
