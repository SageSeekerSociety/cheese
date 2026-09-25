#!/usr/bin/env bash
# Log the platform in to Claude, or out, for every Cheese session at once.
#
# The metering proxy holds the platform's only Claude credential in
# <proxy home>/claude-credential/credential and puts it on each session's
# requests; it re-reads the file on every request, so each command here takes
# effect at the next request of every running session. Run it on the box, as
# the user that owns the metering proxy's directory.
#
#   claude-login.sh status        what the proxy holds now, and until when
#   claude-login.sh setup-token   store a one-year token from `claude setup-token`
#   claude-login.sh login         sign in with the browser; the proxy renews it
#   claude-login.sh logout        remove the credential
#   claude-login.sh egress set <http://[user:pass@]host:port>
#                                 send this credential's requests through a proxy
#   claude-login.sh egress clear  send them direct again
#   claude-login.sh egress test [n]  compare reaching Anthropic through it and direct
set -euo pipefail
umask 077

home="${METERING_PROXY_HOME:-$HOME/cheese-proxy-new/deploy/metering-proxy}"
dir="${CLAUDE_CREDENTIAL_DIR:-$home/claude-credential}"
file="$dir/credential"
egress="$dir/egress"

install_credential() {
  # Replace in one rename, so the proxy never reads half a file.
  local source="$1"
  mkdir -p "$dir"
  mv -f "$source" "$dir/.credential.new"
  mv -f "$dir/.credential.new" "$file"
}

case "${1:-}" in
  status)
    if [[ -s "$egress" ]]; then
      echo "egress: $(sed -E 's#//[^@/]*@#//***@#' "$egress")"
    else
      echo "egress: direct"
    fi
    if [[ ! -s "$file" ]]; then
      echo "logged out: sessions run on the API-key pool only"
      exit 0
    fi
    python3 - "$file" <<'PY'
import datetime, json, sys

text = open(sys.argv[1]).read().strip()
if not text.startswith("{"):
    print("setup-token: used as it is; replace it within a year of creating it")
    raise SystemExit
oauth = json.loads(text).get("claudeAiOauth") or {}


def when(ms):
    if not isinstance(ms, (int, float)):
        return "unknown"
    return datetime.datetime.fromtimestamp(ms / 1000).isoformat(timespec="minutes")


print("login: the proxy renews the access token itself")
print("  access token expires", when(oauth.get("expiresAt")))
print("  log in again by", when(oauth.get("refreshTokenExpiresAt")))
PY
    ;;
  setup-token)
    read -rsp "Paste the token from \`claude setup-token\`: " token
    echo
    [[ "$token" == sk-ant-oat01-* ]] || {
      echo "That is not a Claude setup-token (it starts with sk-ant-oat01-)." >&2
      exit 1
    }
    tmp="$(mktemp)"
    printf '%s\n' "$token" > "$tmp"
    install_credential "$tmp"
    echo "stored: every session uses it from its next request"
    ;;
  login)
    claude="${CLAUDE_BIN:-$(command -v claude || true)}"
    [[ -x "$claude" ]] || {
      echo "No claude binary found; set CLAUDE_BIN to one." >&2
      exit 1
    }
    # A throwaway config dir: the pair must end up with one holder, the proxy,
    # so the copy the login writes here is moved out and the dir removed.
    mkdir -p "$dir"
    tmp="$(mktemp -d "$dir/.login.XXXXXX")"
    trap 'rm -rf "$tmp"' EXIT
    env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY \
      -u ANTHROPIC_BASE_URL -u CLAUDE_SECURESTORAGE_CONFIG_DIR \
      CLAUDE_CONFIG_DIR="$tmp" "$claude" auth login
    [[ -s "$tmp/.credentials.json" ]] || {
      echo "The login left no credentials; nothing was changed." >&2
      exit 1
    }
    install_credential "$tmp/.credentials.json"
    echo "logged in: every session uses it from its next request"
    ;;
  logout)
    rm -f "$file"
    echo "logged out: subscription requests are refused from the next request"
    ;;
  egress)
    case "${2:-}" in
      set)
        url="${3:?usage: claude-login.sh egress set http://[user:pass@]host:port}"
        [[ "$url" =~ ^http://([^@/]+@)?[^/:@]+:[0-9]+/?$ ]] || {
          echo "An egress is an HTTP proxy: http://[user:pass@]host:port" >&2
          exit 1
        }
        mkdir -p "$dir"
        tmp="$(mktemp "$dir/.egress.XXXXXX")"
        printf '%s\n' "$url" > "$tmp"
        mv -f "$tmp" "$egress"
        echo "set: this credential's requests leave through it from the next request"
        ;;
      clear)
        rm -f "$egress"
        echo "cleared: this credential's requests go direct from the next request"
        ;;
      test)
        [[ -s "$egress" ]] || { echo "No egress is set." >&2; exit 1; }
        python3 - "$(cat "$egress")" "${3:-10}" <<'PY'
import base64, socket, ssl, statistics, sys, time, urllib.parse

HOST = "api.anthropic.com"
url, samples = sys.argv[1].strip(), int(sys.argv[2])
proxy = urllib.parse.urlsplit(url)
context = ssl.create_default_context()


def handshake(sock):
    with context.wrap_socket(sock, server_hostname=HOST):
        pass


def direct():
    handshake(socket.create_connection((HOST, 443), timeout=15))


def through():
    sock = socket.create_connection((proxy.hostname, proxy.port), timeout=15)
    lines = [f"CONNECT {HOST}:443 HTTP/1.1", f"Host: {HOST}:443"]
    if proxy.username is not None:
        pair = f"{urllib.parse.unquote(proxy.username)}:{urllib.parse.unquote(proxy.password or '')}"
        lines.append("Proxy-Authorization: Basic " + base64.b64encode(pair.encode()).decode())
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    reply = b""
    while b"\r\n\r\n" not in reply:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("the egress closed the connection")
        reply += chunk
    status = reply.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 200 " not in status + " ":
        raise OSError(f"the egress answered: {status}")
    handshake(sock)


for name, attempt in (("through the egress", through), ("direct", direct)):
    times, failures = [], []
    for _ in range(samples):
        start = time.perf_counter()
        try:
            attempt()
            times.append((time.perf_counter() - start) * 1000)
        except OSError as err:
            failures.append(str(err))
    line = f"{name}: {len(times)}/{samples} connected"
    if times:
        times.sort()
        slow = times[min(len(times) - 1, int(len(times) * 0.9))]
        line += f", median {statistics.median(times):.0f} ms, slowest 10% {slow:.0f} ms"
    print(line)
    if failures:
        print(f"  last failure: {failures[-1]}")
PY
        ;;
      *)
        echo "usage: claude-login.sh egress set <url> | clear | test [n]" >&2
        exit 2
        ;;
    esac
    ;;
  *)
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
