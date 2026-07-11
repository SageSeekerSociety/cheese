#!/usr/bin/env bash
# Authenticated screenshot of the fusion demo frontend (:5200). Injects an alice
# session for BOTH auth layers (main axios + cheesex /api).
# Usage: scripts/dev/shot.sh <app-path> <out.png>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_PATH="${1:-/}"; OUT="${2:-/tmp/shot.png}"
read -r MAIN_TOKEN CX_TOKEN < <(cd "$ROOT/backend" && env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL PYTHONPATH=. .venv/bin/python - <<'PY' 2>/dev/null
import jwt, time
from app.core.config import settings
from app.core.tokens import mint_session_token
now=int(time.time())
main=jwt.encode({"sub":"1","type":"access","iat":now,"exp":now+86400}, settings.jwt_secret, algorithm="HS256")
cx=mint_session_token(handle="alice", user_id=None)
print(main, cx)
PY
)
cd "$ROOT/e2e"
MAIN_TOKEN="$MAIN_TOKEN" CX_TOKEN="$CX_TOKEN" node shot.mjs "$APP_PATH" "$OUT"
