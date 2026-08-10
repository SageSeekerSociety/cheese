#!/usr/bin/env bash
# Runs ON a MicroCloud machine. One question only:
#
#   can the platform point Claude Code at OUR gateway without touching the
#   settings.json MicroCloud owns?
#
# `machine_route_probe.sh` established that it cannot be done with $HOME or a
# process env var: the machine's ~/.claude/settings.json wins both. This tries
# CLAUDE_CONFIG_DIR, which relocates the config ROOT — note that `.claude.json`
# then has to live INSIDE that directory, not beside it.
#
# Self-checks the sink first: a listener that never bound reports "no hits" for
# every hypothesis, which is how this probe previously produced a confident
# wrong answer.
set -u

SINK=http://127.0.0.1:18080
MC_SETTINGS="$HOME/.claude/settings.json"
CFG="$HOME/.cheese/home/override-probe/.claude"
CLAUDE="$HOME/.local/bin/claude"

echo "machine settings.json: $([ -f "$MC_SETTINGS" ] && echo present || echo MISSING)"
[ -f "$MC_SETTINGS" ] || { echo "ABORT: machine is in a dirty state, restore it first"; exit 1; }
echo "  its env keys: $(python3 -c '
import json,sys; print(",".join(json.load(open(sys.argv[1])).get("env",{}).keys()))' "$MC_SETTINGS")"

cat > /tmp/ov-sink.py <<'PY'
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
pkill -f '[o]v-sink.py' 2>/dev/null
rm -f /tmp/ov-sink.log
nohup python3 /tmp/ov-sink.py >/tmp/ov-sink.log 2>&1 &
sleep 1
curl -fsS -m 5 -X POST "$SINK/__selfcheck" >/dev/null 2>&1
if ! grep -q HIT /tmp/ov-sink.log 2>/dev/null; then
  echo "ABORT: sink never bound — $(head -3 /tmp/ov-sink.log | tr '\n' ' ')"
  exit 1
fi
echo "sink: listening (self-check passed)"

rm -rf "$CFG"; mkdir -p "$CFG"
printf '{"hasCompletedOnboarding":true,"autoUpdates":false,"bypassPermissionsModeAccepted":true}' \
  > "$CFG/.claude.json"
printf '{"env":{"ANTHROPIC_BASE_URL":"%s","ANTHROPIC_AUTH_TOKEN":"probe-token"}}' "$SINK" \
  > "$CFG/settings.json"

base=$(grep -c HIT /tmp/ov-sink.log | head -1)
out=$(CLAUDE_CONFIG_DIR="$CFG" env -u HTTPS_PROXY -u HTTP_PROXY \
      timeout 60 "$CLAUDE" -p "say ok" </dev/null 2>&1)
total=$(grep -c HIT /tmp/ov-sink.log | head -1)
hits=$(( total - base ))

echo "CLAUDE_CONFIG_DIR override -> sink hits: $hits"
echo "  claude said: $(echo "$out" | head -3 | tr '\n' ' ' | cut -c1-200)"
if [ "$hits" -gt 0 ]; then
  echo "VERDICT: CLAUDE_CONFIG_DIR wins over the machine's settings.json"
else
  echo "VERDICT: it does NOT win — the machine's settings.json still decides"
fi
pkill -f '[o]v-sink.py' 2>/dev/null
echo "machine settings.json still present: $([ -f "$MC_SETTINGS" ] && echo yes || echo NO)"
