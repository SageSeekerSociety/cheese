#!/usr/bin/env bash
# Authenticated screenshot of the fusion demo frontend (:5200).
# Injects an alice session as ONE unified token (sub=int id + handle claim) —
# the merged backend authenticates both API layers off it (fusion unify P3).
# Usage: scripts/dev/shot.sh <app-path> <out.png>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_PATH="${1:-/}"; OUT="${2:-/tmp/shot.png}"
TOKEN=$(cd "$ROOT/backend" && env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL PYTHONPATH=. .venv/bin/python - <<'PY' 2>/dev/null
import jwt, time
from app.core.config import settings
now=int(time.time())
print(jwt.encode({"sub":"1","handle":"alice","type":"access","iat":now,"exp":now+86400},
                 settings.jwt_secret, algorithm="HS256"))
PY
)
cd "$ROOT/e2e"
TOKEN="$TOKEN" node shot.mjs "$APP_PATH" "$OUT"
