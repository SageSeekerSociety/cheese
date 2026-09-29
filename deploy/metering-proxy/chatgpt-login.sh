#!/usr/bin/env bash
# Log the platform's ChatGPT accounts in and out of the metering proxy.
#
# The proxy holds each account in <proxy home>/chatgpt-credential/<name>/credential,
# renews it itself, and re-reads it on every request, so each command here
# takes effect at the account's next request. Run it on the box, as the user
# that owns the metering proxy's directory.
#
#   chatgpt-login.sh ls                     every account, and whether it is usable
#   chatgpt-login.sh status [name]          what the proxy holds, and until when
#   chatgpt-login.sh login <name>           sign in with a device code
#   chatgpt-login.sh import <name> <file>   move in a pair held elsewhere (JSON)
#   chatgpt-login.sh logout <name>          remove the account's credential
#   chatgpt-login.sh egress set <name> <http://[user:pass@]host:port>
#                                           send this account's requests through a proxy
#   chatgpt-login.sh egress clear <name>    send them direct again
#   chatgpt-login.sh egress test <name> [n] compare reaching ChatGPT through it and direct
set -euo pipefail
umask 077

home="${METERING_PROXY_HOME:-$HOME/cheese-proxy-new/deploy/metering-proxy}"
root="${CHATGPT_CREDENTIAL_DIR:-$home/chatgpt-credential}"
# Where OpenAI's device login and token endpoints live; overridden only by tests.
auth_base="${OPENAI_AUTH_BASE:-https://auth.openai.com}"

account() {
  # One path segment on the proxy's route and one directory here, so nothing
  # that could climb out of either. The proxy applies the same pattern.
  local name="${1:?an account name is required}"
  [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || {
    echo "An account name is letters, digits, '.', '_' and '-', starting with a letter or digit." >&2
    exit 1
  }
  printf '%s/%s' "$root" "$name"
}

show() {
  local dir="$1"
  echo "${dir##*/}:"
  if [[ -s "$dir/egress" ]]; then
    echo "  egress: $(sed -E 's#//[^@/]*@#//***@#' "$dir/egress")"
  else
    echo "  egress: direct"
  fi
  if [[ ! -s "$dir/credential" ]]; then
    echo "  logged out: requests for this account are refused"
    return
  fi
  python3 - "$dir/credential" <<'PY'
import datetime, json, sys

doc = json.load(open(sys.argv[1]))
at = doc.get("expires_at")
when = (
    datetime.datetime.fromtimestamp(at).isoformat(timespec="minutes")
    if isinstance(at, (int, float))
    else "unknown"
)
print("  logged in: the proxy renews the access token itself")
print("  account id", doc.get("account_id") or "none")
print("  access token expires", when)
PY
}

# Store a pair for an account, the one way both `login` and `import` write it:
# validated, 0600, and replaced in one rename so the proxy never reads half a
# file. Reads the pair as JSON on stdin; the account directory is $1.
store() {
  # The pair arrives on stdin, which the heredoc below takes for the program,
  # so it is handed over on fd 3.
  python3 - "$1" 3<&0 <<'PY'
import base64, json, os, sys, tempfile, time


def claims(token):
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) < 2:
        return {}
    try:
        value = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def account_id(id_token):
    found = claims(id_token)
    value = found.get("chatgpt_account_id")
    if not isinstance(value, str) or not value:
        nested = found.get("https://api.openai.com/auth")
        value = nested.get("chatgpt_account_id") if isinstance(nested, dict) else ""
    return value if isinstance(value, str) else ""


directory = sys.argv[1]
try:
    pair = json.load(os.fdopen(3))
except ValueError:
    raise SystemExit("That is not JSON; nothing was changed.")
if not isinstance(pair, dict):
    raise SystemExit("That is not a JSON object; nothing was changed.")
for key in ("access_token", "refresh_token"):
    if not isinstance(pair.get(key), str) or not pair[key]:
        raise SystemExit(f"The credential has no {key}; nothing was changed.")
id_token = pair.get("id_token") if isinstance(pair.get("id_token"), str) else ""
expires_at = pair.get("expires_at")
if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
    expires_at = claims(pair["access_token"]).get("exp")
if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
    raise SystemExit(
        "The credential has no expires_at and its access token no exp; "
        "nothing was changed."
    )
given = pair.get("account_id")
credential = {
    "access_token": pair["access_token"],
    "refresh_token": pair["refresh_token"],
    "id_token": id_token,
    "expires_at": int(expires_at),
    "account_id": given if isinstance(given, str) and given else account_id(id_token),
}
os.makedirs(directory, exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=directory, prefix=".credential.")
with os.fdopen(fd, "w") as fh:
    json.dump(credential, fh, separators=(",", ":"))
os.replace(tmp, os.path.join(directory, "credential"))
left = (credential["expires_at"] - time.time()) / 3600
print(f"stored: the proxy uses it from the next request (access token good for {left:.1f} h)")
PY
}

case "${1:-}" in
  ls)
    [[ -d "$root" ]] || { echo "no accounts"; exit 0; }
    found=0
    for dir in "$root"/*/; do
      [[ -d "$dir" ]] || continue
      found=1
      name="$(basename "$dir")"
      state="logged out"
      [[ -s "$dir/credential" ]] && state="logged in"
      route="direct"
      [[ -s "$dir/egress" ]] && route="through an egress"
      echo "$name: $state, $route"
    done
    [[ "$found" = 1 ]] || echo "no accounts"
    ;;
  status)
    if [[ -n "${2:-}" ]]; then
      dir="$(account "$2")"
      [[ -d "$dir" ]] || { echo "No account named $2." >&2; exit 1; }
      show "$dir"
    else
      found=0
      for dir in "$root"/*/; do
        [[ -d "$dir" ]] || continue
        found=1
        show "${dir%/}"
      done
      [[ "$found" = 1 ]] || echo "no accounts"
    fi
    ;;
  login)
    dir="$(account "${2:-}")"
    egress_url=""
    [[ -s "$dir/egress" ]] && egress_url="$(cat "$dir/egress")"
    # The device flow, exchange and form fields are the backend's
    # (backend/app/domain/subscription/openai_codex.py). It goes through the
    # account's egress when it has one, like every refresh after it.
    pair="$(python3 - "$auth_base" "$egress_url" <<'PY'
import base64, http.client, json, sys, time, urllib.parse

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
base, egress_url = sys.argv[1].rstrip("/"), sys.argv[2].strip()
egress = urllib.parse.urlsplit(egress_url) if egress_url else None


def post(path, body, content_type):
    target = urllib.parse.urlsplit(base + path)
    secure = target.scheme == "https"
    port = target.port or (443 if secure else 80)
    kind = http.client.HTTPSConnection if secure else http.client.HTTPConnection
    if egress is None:
        conn = kind(target.hostname, port, timeout=30)
    else:
        conn = kind(egress.hostname, egress.port, timeout=30)
        headers = {}
        if egress.username is not None:
            pair = f"{urllib.parse.unquote(egress.username)}:{urllib.parse.unquote(egress.password or '')}"
            headers["Proxy-Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        conn.set_tunnel(target.hostname, port, headers=headers)
    try:
        conn.request("POST", target.path, body, {"Content-Type": content_type, "Accept": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
    finally:
        conn.close()
    try:
        parsed = json.loads(data or b"{}")
    except ValueError:
        parsed = {}
    return resp.status, parsed if isinstance(parsed, dict) else {}


def post_json(path, payload):
    return post(path, json.dumps(payload).encode(), "application/json")


status, start = post_json("/api/accounts/deviceauth/usercode", {"client_id": CLIENT_ID})
if status != 200 or not isinstance(start.get("device_auth_id"), str) or not isinstance(start.get("user_code"), str):
    raise SystemExit(f"OpenAI did not start a device login (HTTP {status}); nothing was changed.")
interval = start.get("interval") if isinstance(start.get("interval"), int) and start["interval"] > 0 else 5
expires_in = start.get("expires_in") if isinstance(start.get("expires_in"), int) else 900
print(f"Open https://auth.openai.com/codex/device and enter {start['user_code']}", file=sys.stderr)
deadline = time.time() + expires_in
while True:
    if time.time() > deadline:
        raise SystemExit("The code expired before it was entered; nothing was changed.")
    time.sleep(interval)
    status, done = post_json(
        "/api/accounts/deviceauth/token",
        {"device_auth_id": start["device_auth_id"], "user_code": start["user_code"]},
    )
    if status in (403, 404):
        continue
    if status == 410:
        raise SystemExit("OpenAI ended the login; nothing was changed.")
    if status != 200:
        raise SystemExit(f"Polling the login failed (HTTP {status}); nothing was changed.")
    break
if not isinstance(done.get("authorization_code"), str) or not isinstance(done.get("code_verifier"), str):
    raise SystemExit("OpenAI's answer lacked the authorization code; nothing was changed.")
status, tokens = post(
    "/oauth/token",
    urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": done["authorization_code"],
            "redirect_uri": "https://auth.openai.com/deviceauth/callback",
            "client_id": CLIENT_ID,
            "code_verifier": done["code_verifier"],
        }
    ).encode(),
    "application/x-www-form-urlencoded",
)
if status != 200 or not isinstance(tokens.get("access_token"), str):
    raise SystemExit(f"OpenAI refused the code exchange (HTTP {status}); nothing was changed.")
id_token = tokens.get("id_token") if isinstance(tokens.get("id_token"), str) else ""
parts = id_token.split(".")
try:
    identity = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))) if len(parts) > 1 else {}
except ValueError:
    identity = {}
# The backend's rule: a login that names no stable identity does not count.
if not isinstance(identity, dict) or not identity.get("sub"):
    raise SystemExit("OpenAI's id_token names no account; nothing was changed.")
pair = {k: tokens.get(k) for k in ("access_token", "refresh_token", "id_token")}
if isinstance(tokens.get("expires_in"), int):
    pair["expires_at"] = int(time.time()) + tokens["expires_in"]
json.dump(pair, sys.stdout)
PY
)"
    store "$dir" <<< "$pair"
    ;;
  import)
    dir="$(account "${2:-}")"
    file="${3:?usage: chatgpt-login.sh import <name> <file>}"
    [[ -r "$file" ]] || { echo "Cannot read $file." >&2; exit 1; }
    store "$dir" < "$file"
    echo "The proxy now renews this pair; stop using and refreshing the copy it came from."
    ;;
  logout)
    dir="$(account "${2:-}")"
    rm -f "$dir/credential"
    echo "logged out: requests for ${2} are refused from the next request"
    ;;
  egress)
    dir="$(account "${3:-}")"
    case "${2:-}" in
      set)
        url="${4:?usage: chatgpt-login.sh egress set <name> http://[user:pass@]host:port}"
        [[ "$url" =~ ^http://([^@/]+@)?[^/:@]+:[0-9]+/?$ ]] || {
          echo "An egress is an HTTP proxy: http://[user:pass@]host:port" >&2
          exit 1
        }
        mkdir -p "$dir"
        tmp="$(mktemp "$dir/.egress.XXXXXX")"
        printf '%s\n' "$url" > "$tmp"
        mv -f "$tmp" "$dir/egress"
        echo "set: ${3}'s requests leave through it from the next request"
        ;;
      clear)
        rm -f "$dir/egress"
        echo "cleared: ${3}'s requests go direct from the next request"
        ;;
      test)
        [[ -s "$dir/egress" ]] || { echo "No egress is set for ${3}." >&2; exit 1; }
        python3 - "$(cat "$dir/egress")" "${4:-10}" <<'PY'
import base64, socket, ssl, statistics, sys, time, urllib.parse

HOST = "chatgpt.com"
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
        echo "usage: chatgpt-login.sh egress set <name> <url> | clear <name> | test <name> [n]" >&2
        exit 2
        ;;
    esac
    ;;
  *)
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
