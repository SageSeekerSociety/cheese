#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/tmp"
WORK="$(mktemp -d "$ROOT/tmp/forgejo-bootstrap.XXXXXX")"
export FORGEJO_URL=http://127.0.0.1:33088/forge/
export FORGEJO_PORT=33088
export FORGEJO_WEBHOOK_HOSTS=host.docker.internal
export FORGEJO_LOG_DRIVER=json-file
project="forge-bootstrap-$(date +%s)"
cat > "$WORK/base.yml" <<'YAML'
services:
  backend:
    image: busybox:1.37.0
  frontend:
    image: busybox:1.37.0
YAML
dc() {
  docker compose -p "$project" -f "$WORK/base.yml" \
    -f "$ROOT/deploy/compose/docker-compose.forgejo.yml" "$@"
}
trap 'dc down -v >/dev/null' EXIT
dc up -d --wait forgejo
container="$(dc ps -q forgejo)"
printf 'EXISTING_SETTING=keep\n' > "$WORK/backend.env"
chmod 600 "$WORK/backend.env"
bootstrap() {
  python3 "$ROOT/deploy/bootstrap-forgejo.py" --container "$container" \
    --backend-env "$WORK/backend.env" --api-url http://127.0.0.1:33088/api/v1
}
repository() {
  python3 - "$WORK" "$1" <<'PY'
import json
from pathlib import Path
import sys
import urllib.request

work = Path(sys.argv[1])
token = (work / ".forgejo-admin-token").read_text().strip()
headers = {"Authorization": "token " + token, "Content-Type": "application/json"}
base = "http://127.0.0.1:33088/api/v1"
if sys.argv[2] == "create":
    request = urllib.request.Request(
        base + "/user/repos", headers=headers,
        data=json.dumps({"name": "persistence", "private": True,
                         "auto_init": True, "default_branch": "main"}).encode(),
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        assert json.load(response)["private"]
request = urllib.request.Request(
    base + "/repos/cheese-platform/persistence/branches/main", headers=headers
)
with urllib.request.urlopen(request, timeout=15) as response:
    head = json.load(response)["commit"]["id"]
saved = work / "repository-head"
if sys.argv[2] == "create":
    saved.write_text(head)
else:
    assert saved.read_text() == head, "Repository changed across container recreation"
PY
}
bootstrap
repository create
cp "$WORK/backend.env" "$WORK/expected.env"
dc rm --stop --force forgejo
dc up -d --wait forgejo
container="$(dc ps -q forgejo)"
bootstrap
repository verify
cmp "$WORK/expected.env" "$WORK/backend.env"
test "$(find "$WORK" -name '*.bak' | wc -l | tr -d ' ')" = 1
printf 'PASS: credentials and repository survive recreation; env unchanged; backup retained. Logs: %s\n' \
  "$WORK/forgejo-bootstrap.log"
