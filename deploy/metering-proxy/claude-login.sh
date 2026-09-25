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
set -euo pipefail
umask 077

home="${METERING_PROXY_HOME:-$HOME/cheese-proxy-new/deploy/metering-proxy}"
dir="${CLAUDE_CREDENTIAL_DIR:-$home/claude-credential}"
file="$dir/credential"

install_credential() {
  # Replace in one rename, so the proxy never reads half a file.
  local source="$1"
  mkdir -p "$dir"
  mv -f "$source" "$dir/.credential.new"
  mv -f "$dir/.credential.new" "$file"
}

case "${1:-}" in
  status)
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
  *)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
