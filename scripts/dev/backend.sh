#!/usr/bin/env bash
# Restart the merged fusion backend on :8799 with a CLEAN environment.
#
# WHY the env-scrubbing matters (hard-won, non-obvious):
# Claude Code's own shell exports ANTHROPIC_API_KEY/ANTHROPIC_MODEL/etc. The
# backend spawns the `claude` CLI as a subprocess for agent turns; that
# subprocess INHERITS the shell env, so a leaked empty ANTHROPIC_API_KEY or a
# stray ANTHROPIC_MODEL silently hijacks/breaks model routing (symptom: the
# agent 404s with "model may not exist"). Always launch the backend with these
# unset so ONLY the .env-driven provider config reaches the CLI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)/backend"
cd "$ROOT"
lsof -ti:8799 | xargs kill 2>/dev/null || true
sleep 1
env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL -u ANTHROPIC_DEFAULT_HAIKU_MODEL \
    -u ANTHROPIC_VERTEX_PROJECT_ID -u AI_AGENT -u ANTHROPIC_AUTH_TOKEN \
    -u ANTHROPIC_BASE_URL -u OPENAI_API_KEY \
    nohup uv run uvicorn app.main:app --host 127.0.0.1 --port 8799 --log-level warning \
    > /tmp/fusion-be-8799.log 2>&1 &
for i in $(seq 1 15); do
  sleep 1
  curl -sf http://127.0.0.1:8799/health >/dev/null 2>&1 && { echo "backend :8799 up"; exit 0; }
done
echo "backend failed to come up; tail /tmp/fusion-be-8799.log:"; tail -8 /tmp/fusion-be-8799.log; exit 1
