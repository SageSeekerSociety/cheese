#!/usr/bin/env bash
# Runs ON a MicroCloud machine. Answers, with evidence, the one question the
# platform's model accounting rests on:
#
#   when the platform launches Claude Code here, whose endpoint does it call?
#
# It is not obvious, because two things compete. MicroCloud writes
# ~/.claude/settings.json with an `env` block (its newapi base URL, plus the
# ccproxy proxy and CA). Our launcher exports its own HOME and writes its own
# settings.json there. Claude Code applies settings `env` over the inherited
# environment — so if it resolves its config dir from the passwd entry rather
# than $HOME, our launcher's settings (and hooks) never load, and every turn
# silently bills MicroCloud's newapi account instead of our gateway.
#
# Three cases, each with a loopback sink standing in for our gateway:
#   A  our HOME, our settings.json carries the sink in its `env`
#      -> a hit proves $HOME is honoured and settings.json is the control point
#   B  our HOME, empty settings.json, sink injected as a process env var
#      -> a hit proves process env survives; a miss means something overrides it
#   C  as B, but the machine's own settings.json moved aside
#      -> distinguishes "the machine's file overrode us" from "env is ignored"
#
# Two traps this script exists to avoid repeating:
#   * an earlier version put the sink on the DEV BOX, which this machine cannot
#     route to — "nothing arrived" was then guaranteed under every hypothesis
#     and proved nothing at all;
#   * `claude -p` inherits stdin, so run from a heredoc it swallows the rest of
#     the script and answers THAT. Every call redirects from /dev/null.
set -u

SINK=http://127.0.0.1:18080
MC_SETTINGS="$HOME/.claude/settings.json"
REAL_HOME="$HOME"
PROBE_HOME="$HOME/.cheese/home/route-probe"
CLAUDE="$HOME/.local/bin/claude"

cat > /tmp/cheese-sink.py <<'PY'
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        print(f"HIT {self.path}", flush=True)
        self.send_response(500); self.end_headers()
        self.wfile.write(b'{"type":"error","error":{"type":"api_error","message":"sink"}}')
    do_GET = do_POST
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', 18080), H).serve_forever()
PY

cleanup() {
  pkill -f /tmp/cheese-sink.py 2>/dev/null
  [ -f "${MC_SETTINGS}.route-probe-bak" ] && mv "${MC_SETTINGS}.route-probe-bak" "$MC_SETTINGS"
  return 0
}
trap cleanup EXIT

mkdir -p "$PROBE_HOME/.claude"
printf '{"hasCompletedOnboarding":true,"autoUpdates":false,"bypassPermissionsModeAccepted":true}' \
  > "$PROBE_HOME/.claude.json"

run_case() {  # label, settings-json-content, extra-env...
  local label="$1" settings="$2"; shift 2
  printf '%s' "$settings" > "$PROBE_HOME/.claude/settings.json"
  rm -rf "$PROBE_HOME/.claude/projects" "$PROBE_HOME/.claude/sessions"
  rm -f /tmp/cheese-sink.log
  pkill -f /tmp/cheese-sink.py 2>/dev/null
  nohup python3 /tmp/cheese-sink.py >/tmp/cheese-sink.log 2>&1 &
  sleep 1
  # Self-check the instrument BEFORE trusting a negative result. A sink that
  # never came up reports "no hits" for every hypothesis, which is exactly how
  # an earlier version of this probe produced a confident wrong answer.
  if ! curl -fsS -m 5 -X POST "$SINK/__selfcheck" >/dev/null 2>&1; then :; fi
  if ! grep -q HIT /tmp/cheese-sink.log 2>/dev/null; then
    echo "--- $label"
    echo "    ABORT: the sink is not listening, so no result here means anything"
    echo "    sink log: $(head -3 /tmp/cheese-sink.log 2>/dev/null | tr '\n' ' ')"
    return 1
  fi
  # Count from a baseline instead of truncating: the sink holds the file open,
  # so truncating it leaves a sparse file that grep reads as binary.
  local base; base=$(grep -c HIT /tmp/cheese-sink.log 2>/dev/null | head -1)
  local out
  out=$(HOME="$PROBE_HOME" env -u HTTPS_PROXY -u HTTP_PROXY "$@" \
        timeout 60 "$CLAUDE" -p "say ok" </dev/null 2>&1)
  local total; total=$(grep -c HIT /tmp/cheese-sink.log 2>/dev/null | head -1)
  local hits=$(( total - base ))
  echo "--- $label"
  echo "    sink hits      : $hits"
  echo "    claude said    : $(echo "$out" | head -3 | tr '\n' ' ' | cut -c1-160)"
  # Where did claude keep its session? That is where it thinks HOME is.
  if [ -d "$PROBE_HOME/.claude/projects" ] || [ -d "$PROBE_HOME/.claude/sessions" ]; then
    echo "    config dir used: OUR HOME ($PROBE_HOME/.claude)"
  else
    echo "    config dir used: NOT our HOME (nothing written under $PROBE_HOME/.claude)"
  fi
}

echo "claude  : $("$CLAUDE" --version 2>&1 | head -1)"
echo "machine settings.json env keys: $(python3 -c '
import json,sys
try: print(",".join(json.load(open(sys.argv[1])).get("env",{}).keys()) or "(none)")
except Exception as e: print("unreadable:", e)
' "$MC_SETTINGS" 2>/dev/null)"

run_case "A  our settings.json points at the sink" \
  "{\"env\":{\"ANTHROPIC_BASE_URL\":\"$SINK\",\"ANTHROPIC_AUTH_TOKEN\":\"probe-token\"}}"

run_case "B  empty settings.json, sink injected as process env" \
  '{}' ANTHROPIC_BASE_URL="$SINK" ANTHROPIC_AUTH_TOKEN=probe-token

mv "$MC_SETTINGS" "${MC_SETTINGS}.route-probe-bak"
run_case "C  same as B, machine settings.json moved aside" \
  '{}' ANTHROPIC_BASE_URL="$SINK" ANTHROPIC_AUTH_TOKEN=probe-token
mv "${MC_SETTINGS}.route-probe-bak" "$MC_SETTINGS"

# A and B losing to a file we do not own is only interesting if something CAN
# win against it without touching that file. These are the two candidates.
# CLAUDE_CONFIG_DIR relocates the whole config ROOT, so `.claude.json` has to
# live INSIDE it — not beside it as it does under a plain HOME. Getting that
# wrong makes claude refuse to start, which reads exactly like "the override did
# not work" while proving nothing.
cp "$PROBE_HOME/.claude.json" "$PROBE_HOME/.claude/.claude.json"
run_case "D  machine settings.json in place, CLAUDE_CONFIG_DIR=ours" \
  "{\"env\":{\"ANTHROPIC_BASE_URL\":\"$SINK\",\"ANTHROPIC_AUTH_TOKEN\":\"probe-token\"}}" \
  CLAUDE_CONFIG_DIR="$PROBE_HOME/.claude"

printf '{"env":{"ANTHROPIC_BASE_URL":"%s","ANTHROPIC_AUTH_TOKEN":"probe-token"}}' "$SINK" \
  > "$PROBE_HOME/cli-settings.json"
run_case_cli() {
  local base; base=$(grep -c HIT /tmp/cheese-sink.log 2>/dev/null | head -1)
  local out
  out=$(HOME="$PROBE_HOME" env -u HTTPS_PROXY -u HTTP_PROXY \
        timeout 60 "$CLAUDE" --settings "$PROBE_HOME/cli-settings.json" \
        -p "say ok" </dev/null 2>&1)
  local total; total=$(grep -c HIT /tmp/cheese-sink.log 2>/dev/null | head -1)
  echo "--- E  machine settings.json in place, claude --settings <ours>"
  echo "    sink hits      : $(( total - base ))"
  echo "    claude said    : $(echo "$out" | head -3 | tr '\n' ' ' | cut -c1-160)"
}
printf '{}' > "$PROBE_HOME/.claude/settings.json"
run_case_cli
echo "machine settings.json restored: $([ -f "$MC_SETTINGS" ] && echo yes || echo NO)"
