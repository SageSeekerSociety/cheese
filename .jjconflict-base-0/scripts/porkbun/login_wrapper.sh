#!/usr/bin/env bash
# Reads the Porkbun password from STDIN (fed by mac-mini's `cred with` over
# ssh) into an env var — never argv, never printed, never written to disk.
set -euo pipefail
IFS= read -r PB_PASS || true  # printf sends no trailing newline; EOF-read exits 1
export PB_PASS
cd /Users/andyl/Projects/cheese-backend-py/tmp/cheesex/backend
exec uv run --with playwright python ../scripts/porkbun/login.py
