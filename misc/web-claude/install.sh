#!/usr/bin/env bash
# install.sh — install the `cheese` CLI on this machine and point it at a server.
#
#   ./install.sh http://server:9100          # or: CHEESE_SERVER=... ./install.sh
#
# What it does, in order:
#   1. finds a cheese binary (./cheese next to this script, $CHEESE_BIN, the
#      repo's cli/ build, or builds one with go),
#   2. copies it to ~/.local/bin/cheese,
#   3. copies a private tmux to ~/.config/cheese/bin/tmux so cheese never
#      depends on (or fights with) the machine's own tmux — a ./tmux next to
#      this script wins, else the system tmux is copied,
#   4. writes the server URL into ~/.config/cheese/config.json (keeping any
#      existing login), so later commands never need the URL again,
#   5. tells you exactly what to do next.
set -euo pipefail

say()  { printf '\033[1;35m[cheese]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[cheese]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[cheese]\033[0m %s\n' "$*" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="${1:-${CHEESE_SERVER:-}}"

BIN_DIR="$HOME/.local/bin"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/cheese"
CFG="$CFG_DIR/config.json"

# --- 1. find a cheese binary -------------------------------------------------
CHEESE_SRC=""
for cand in "${CHEESE_BIN:-}" "$HERE/cheese" "$HERE/../../cli/cheese"; do
  [ -n "$cand" ] && [ -f "$cand" ] && [ -x "$cand" ] && CHEESE_SRC="$cand" && break
done
if [ -z "$CHEESE_SRC" ] && command -v go >/dev/null 2>&1 && [ -d "$HERE/../../cli" ]; then
  say "no prebuilt binary found — building from source…"
  (cd "$HERE/../../cli" && go build -o /tmp/cheese-built .) || die "build failed"
  CHEESE_SRC=/tmp/cheese-built
fi
[ -n "$CHEESE_SRC" ] || die "no cheese binary found (put one next to this script, set CHEESE_BIN, or install go)"

# --- 2. install it -----------------------------------------------------------
mkdir -p "$BIN_DIR"
install -m 0755 "$CHEESE_SRC" "$BIN_DIR/cheese"
say "installed $BIN_DIR/cheese"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH — add:  export PATH=\"\$PATH:$BIN_DIR\"" ;;
esac

# --- 3. private tmux ---------------------------------------------------------
mkdir -p "$CFG_DIR/bin"
if [ -f "$HERE/tmux" ] && [ -x "$HERE/tmux" ]; then
  install -m 0755 "$HERE/tmux" "$CFG_DIR/bin/tmux"
  say "installed private tmux (bundled) at $CFG_DIR/bin/tmux"
elif command -v tmux >/dev/null 2>&1; then
  install -m 0755 "$(command -v tmux)" "$CFG_DIR/bin/tmux"
  say "installed private tmux (copied from system) at $CFG_DIR/bin/tmux"
else
  warn "no tmux found on this machine and none bundled next to this script —"
  warn "screens will not start until a tmux exists at $CFG_DIR/bin/tmux (or on PATH)."
fi

# --- 4. remember the server --------------------------------------------------
if [ -n "$SERVER" ]; then
  mkdir -p "$CFG_DIR"
  if [ -f "$CFG" ] && command -v python3 >/dev/null 2>&1; then
    python3 - "$CFG" "$SERVER" <<'PY'
import json, sys
path, base = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {}
cfg["base"] = base
json.dump(cfg, open(path, "w"), indent=2)
PY
  elif [ -f "$CFG" ]; then
    warn "config exists but python3 is missing — not rewriting it; run: cheese auth login $SERVER"
  else
    printf '{\n  "base": "%s"\n}\n' "$SERVER" > "$CFG"
    chmod 600 "$CFG"
  fi
  say "server set to $SERVER"
else
  warn "no server URL given — you'll need to pass it once: cheese auth login <server-url>"
fi

# --- 5. what now? ------------------------------------------------------------
echo
if "$BIN_DIR/cheese" link status 2>/dev/null | grep -q "logged in : yes"; then
  say "this machine is already logged in."
  say "next:  cheese link connect          # connect now"
else
  say "next:  cheese link connect          # logs you in, then connects"
  say "  or:  cheese link auto-connect     # same, and reconnect on every boot"
fi
say "later: cheese link status | cheese link disconnect | cheese uninstall"
