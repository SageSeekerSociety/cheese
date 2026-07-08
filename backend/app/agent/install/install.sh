#!/bin/sh
# install.sh — one-line installer for the `cheese` connector, served by the backend.
#
#   curl -fsSL <origin>/install.sh | sh
#
# Downloads the right prebuilt `cheese` binary — and a private, static tmux — from
# the same origin it was fetched from, so a fresh machine needs nothing pre-installed.
# It carries NO secrets and NO business logic: it only drops a binary and records the
# server URL. Enrollment still happens through the device flow (`cheese link
# auto-connect` → approve in the browser), so piping this to a shell never grants
# access by itself.
#
# No sudo needed here — the binary goes in your ~/.local/bin and config/tmux in your
# home. The next step, `cheese link auto-connect`, elevates itself just to install a
# boot service, which it runs as you (so it keeps using your home). `| sudo sh` also
# works: the binary then lands in /usr/local/bin and config stays in your home.
#
# The backend bakes the connector base URL into __CONNECTOR_BASE__ when it serves this
# file; override with CHEESE_CONNECTOR_BASE=... if needed.
set -eu

CONNECTOR_BASE="${CHEESE_CONNECTOR_BASE:-__CONNECTOR_BASE__}"

# --- pretty output -----------------------------------------------------------
if [ -t 1 ]; then B='\033[1m'; DIM='\033[2m'; M='\033[1;35m'; G='\033[1;32m'; Y='\033[1;33m'; R='\033[1;31m'; Z='\033[0m'; else B=''; DIM=''; M=''; G=''; Y=''; R=''; Z=''; fi
step() { printf "${M}▸${Z} %s\n" "$*"; }
ok()   { printf "  ${G}✓${Z} %s\n" "$*"; }
warn() { printf "  ${Y}!${Z} %s\n" "$*"; }
die()  { printf "${R}✗ %s${Z}\n" "$*" >&2; exit 1; }

printf "\n${B}🧀 cheese connector${Z} ${DIM}· installer${Z}\n\n"

# --- privilege + install locations ------------------------------------------
# Config + the private tmux always live in the *invoking* user's home and are owned by
# them, because `cheese link auto-connect` installs a system service that runs as that
# user (User=…) and must read them. The binary goes on PATH. Works `| sh` or `| sudo sh`.
if [ "$(id -u)" = 0 ]; then
  BIN_DIR="/usr/local/bin"                       # on every PATH, incl. sudo's secure_path
  owner="${SUDO_USER:-root}"
  home="$(getent passwd "$owner" 2>/dev/null | cut -d: -f6)"; [ -n "$home" ] || home="$HOME"
else
  BIN_DIR="$HOME/.local/bin"
  owner="$(id -un)"; home="$HOME"
fi
CFG_DIR="$home/.config/cheese"
CFG="$CFG_DIR/config.json"

# --- pick a downloader -------------------------------------------------------
if command -v curl >/dev/null 2>&1; then
  dl() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
  dl() { wget -qO "$2" "$1"; }
else
  die "need curl or wget to download the cheese binary"
fi

# --- detect os/arch ----------------------------------------------------------
os="$(uname -s)"; arch="$(uname -m)"
case "$os" in
  Linux)  os=linux ;;
  Darwin) os=darwin ;;
  *) die "unsupported OS: $os (cheese runs on Linux and macOS)" ;;
esac
case "$arch" in
  x86_64|amd64)  arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) die "unsupported arch: $arch (cheese ships amd64 and arm64)" ;;
esac
target="$os-$arch"
step "Platform"
ok "$target"

# --- 1. cheese binary --------------------------------------------------------
step "Installing cheese"
mkdir -p "$BIN_DIR"
tmp="$(mktemp)"
dl "$CONNECTOR_BASE/latest/$target/cheese" "$tmp" \
  || die "could not download $CONNECTOR_BASE/latest/$target/cheese"
install -m 0755 "$tmp" "$BIN_DIR/cheese"; rm -f "$tmp"
ok "$BIN_DIR/cheese"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH — add:  export PATH=\"\$PATH:$BIN_DIR\"" ;;
esac

# --- 2. private tmux ---------------------------------------------------------
# cheese runs its screens in a private tmux so it never fights the machine's own.
# Resolution order: (1) copy the machine's own tmux if it has one — the surest match
# for the OS; (2) else download the static build the origin publishes (no
# libevent/ncurses needed on the target); (3) else tell the user to install tmux
# themselves after setup. macOS gets its tmux here, since only Linux tmux is published.
step "Installing private tmux"
mkdir -p "$CFG_DIR/bin"
if command -v tmux >/dev/null 2>&1; then
  install -m 0755 "$(command -v tmux)" "$CFG_DIR/bin/tmux"
  ok "copied this machine's tmux ($(command -v tmux))"
else
  tmp="$(mktemp)"
  if dl "$CONNECTOR_BASE/latest/$target/tmux" "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
    install -m 0755 "$tmp" "$CFG_DIR/bin/tmux"
    ok "downloaded static tmux → $CFG_DIR/bin/tmux"
  else
    warn "no tmux on this machine and none could be downloaded for $target."
    warn "please install tmux after setup (e.g. apt/dnf/brew install tmux);"
    warn "sessions won't start until a tmux is on PATH or at $CFG_DIR/bin/tmux."
  fi
  rm -f "$tmp"
fi

# --- 3. remember the server --------------------------------------------------
# The cli base is the connector base minus its trailing /connector segment.
base="${CONNECTOR_BASE%/connector}"
mkdir -p "$CFG_DIR"
if [ -f "$CFG" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$CFG" "$base" <<'PY'
import json, sys
path, base = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {}
cfg["base"] = base
json.dump(cfg, open(path, "w"), indent=2)
PY
else
  printf '{\n  "base": "%s"\n}\n' "$base" > "$CFG"
  chmod 600 "$CFG"
fi
step "Server"
ok "$base"

# When installed via sudo, hand the config dir back to the user the service will run as.
if [ "$(id -u)" = 0 ] && [ "$owner" != "root" ]; then
  chown -R "$owner" "$CFG_DIR" 2>/dev/null || true
fi

# --- 4. what now? ------------------------------------------------------------
# One command connects: it logs this machine in (approve in the browser) and installs
# the boot service — elevating to root for a system-wide service if needed.
printf "\n${G}Installed.${Z} One more command to go online:\n\n"
printf "    ${B}cheese link auto-connect${Z}\n\n"
printf "${DIM}It prints an approve link — open it while logged in to bind this machine.\n"
printf "Manage later:  cheese link status · cheese link connect · cheese uninstall${Z}\n\n"
