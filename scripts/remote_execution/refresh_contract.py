"""The metering proxy's token refresh has to be the pinned Claude Code's own.

The proxy holds the platform's Claude login pair and renews it itself, with a
request laid out by `refresh_request` in deploy/metering-proxy/
cheese_billing_core.py. That request is meant to be byte for byte the one
Claude Code sends — the same URL, headers in the same order with the same
names and values, and the same JSON body — and nothing published pins how
Claude Code sends it. So this script makes the given build refresh a login
that is about to expire and compares what went on the wire with what the proxy
would send for the same login.

Nothing leaves the machine: mitmproxy answers every request itself, the token
endpoint included, and never connects upstream. `claude mcp list` is the command
that refreshes a login near expiry and waits for the result without calling the
model.

Point it at the pinned build to gate a merge, and at the newest published build
to learn that the next upgrade changes the refresh before the upgrade does.

Usage:
    python3 refresh_contract.py --claude <binary> --mitmdump <binary>
        [--output <receipts dir>]
"""

import argparse
import base64
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "cheese_billing_core", ROOT / "deploy/metering-proxy/cheese_billing_core.py"
)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

REFRESH_TOKEN = "sk-ant-ort01-contract-refresh-token"
SCOPES = [
    "user:inference",
    "user:profile",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
]

# Records every request and answers it here: the token endpoint with a rotated
# pair, everything else with an empty object. No flow ever gets a server.
ADDON = """
import base64, json, os
from mitmproxy import http

LOG = os.environ["CONTRACT_LOG"]


def request(flow):
    with open(LOG, "a") as fh:
        fh.write(json.dumps({
            "method": flow.request.method,
            "host": flow.request.pretty_host,
            "path": flow.request.path,
            "headers": [
                [n.decode(), v.decode()] for n, v in flow.request.headers.fields
            ],
            "body": base64.b64encode(flow.request.raw_content or b"").decode(),
        }) + "\\n")
    if flow.request.path.startswith("/v1/oauth/token"):
        body = {
            "access_token": "sk-ant-oat01-contract-rotated",
            "refresh_token": "sk-ant-ort01-contract-rotated",
            "expires_in": 28800,
            "scope": " ".join(SCOPES_HERE),
        }
        flow.response = http.Response.make(
            200, json.dumps(body).encode(), {"Content-Type": "application/json"}
        )
        return
    flow.response = http.Response.make(200, b"{}", {"Content-Type": "application/json"})
""".replace("SCOPES_HERE", repr(SCOPES))

# Keeps Claude Code on its plaintext store on macOS, as it is on Linux: a
# lookup finds nothing (44) and a write fails, so it reads and writes
# $CLAUDE_CONFIG_DIR/.credentials.json. Only `-i` reads stdin; the client
# keeps stdin open on lookups, and reading it there would hang until its
# timeout, which it treats as a transient failure and skips the file.
FAKE_SECURITY = """#!/bin/sh
case "$1" in
  find-generic-password|delete-generic-password) exit 44 ;;
  -i) cat >/dev/null; exit 1 ;;
  *) exit 1 ;;
esac
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(config: Path, expires_in_s: float) -> None:
    config.mkdir(parents=True)
    oauth = {
        "accessToken": "sk-ant-oat01-contract-access-token",
        "refreshToken": REFRESH_TOKEN,
        "expiresAt": int((time.time() + expires_in_s) * 1000),
        "scopes": SCOPES,
        "subscriptionType": "max",
    }
    (config / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}))
    (config / ".credentials.json").chmod(0o600)
    (config / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True}))


def _proxys_request() -> tuple[list[tuple[str, str]], bytes]:
    """What the proxy sends for the same login, taken from the code that sends it."""
    with tempfile.TemporaryDirectory() as folder:
        store = Path(folder) / "credential"
        _seed(Path(folder) / "unused", 120)
        oauth = json.loads((Path(folder) / "unused/.credentials.json").read_text())
        store.write_text(json.dumps(oauth))
        sent = []

        def capture(url, body, timeout):
            sent.append((url, body))
            return 200, {"access_token": "unused", "expires_in": 60}

        core.PlatformCredential(store, post=capture).refresh_if_due()
    ((url, body),) = sent
    assert url == core.OAUTH_TOKEN_URL, url
    return core.refresh_request(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True)
    parser.add_argument("--mitmdump", required=True)
    parser.add_argument("--output", default="tmp/refresh-contract")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        (work / "home").mkdir()
        (work / "bin").mkdir()
        (work / "bin/security").write_text(FAKE_SECURITY)
        (work / "bin/security").chmod(0o755)
        (work / "addon.py").write_text(ADDON)
        log = work / "requests.jsonl"
        config = work / "config"
        _seed(config, 120)
        port = _free_port()
        mitm = subprocess.Popen(
            [
                args.mitmdump,
                "--listen-host",
                "127.0.0.1",
                "--listen-port",
                str(port),
                "--set",
                f"confdir={work / 'mitm'}",
                "--set",
                "connection_strategy=lazy",
                "--set",
                "termlog_verbosity=warn",
                "-s",
                str(work / "addon.py"),
            ],
            env={**os.environ, "CONTRACT_LOG": str(log)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            ca = work / "mitm/mitmproxy-ca-cert.pem"
            deadline = time.time() + 30
            while time.time() < deadline:
                if ca.exists():
                    try:
                        socket.create_connection(("127.0.0.1", port), 1).close()
                        break
                    except OSError:
                        pass
                time.sleep(0.2)
            else:
                print("mitmdump never came up", file=sys.stderr)
                return 1
            proxy = f"http://127.0.0.1:{port}"
            env = {
                "HOME": str(work / "home"),
                "PATH": f"{work / 'bin'}:/usr/bin:/bin",
                "CLAUDE_CONFIG_DIR": str(config),
                "HTTPS_PROXY": proxy,
                "HTTP_PROXY": proxy,
                "NODE_EXTRA_CA_CERTS": str(ca),
                "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            }
            run = subprocess.run(
                [args.claude, "mcp", "list"],
                env=env,
                cwd=work,
                capture_output=True,
                text=True,
                timeout=120,
            )
            version = subprocess.run(
                [args.claude, "--version"], env=env, capture_output=True, text=True
            ).stdout.strip()
        finally:
            mitm.terminate()
            mitm.wait(10)

        requests = (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )
        refreshes = [r for r in requests if r["path"].startswith("/v1/oauth/token")]
        stored = json.loads((config / ".credentials.json").read_text())
        wrote_back = (
            stored.get("claudeAiOauth", {}).get("refreshToken")
            == "sk-ant-ort01-contract-rotated"
        )

    ours_headers, ours_body = _proxys_request()
    failures = []
    if len(refreshes) != 1:
        failures.append(f"Claude Code sent {len(refreshes)} refresh requests, not 1")
    else:
        theirs = refreshes[0]
        theirs_headers = [tuple(h) for h in theirs["headers"]]
        theirs_body = base64.b64decode(theirs["body"])
        if (theirs["method"], theirs["host"]) != ("POST", "platform.claude.com"):
            failures.append(f"sent to {theirs['method']} {theirs['host']}")
        if theirs_headers != ours_headers:
            failures.append(
                "headers differ\n  claude: "
                + json.dumps(theirs_headers)
                + "\n  proxy:  "
                + json.dumps(ours_headers)
            )
        if theirs_body != ours_body:
            failures.append(
                "bodies differ\n  claude: "
                + theirs_body.decode(errors="replace")
                + "\n  proxy:  "
                + ours_body.decode(errors="replace")
            )
    if not wrote_back:
        failures.append("Claude Code did not store the rotated pair")
    if any(r["path"].startswith("/v1/messages") for r in requests):
        failures.append("the refresh run called the model")

    receipt = {
        "claude": version,
        "exit": run.returncode,
        "requests": [(r["method"], r["host"], r["path"]) for r in requests],
        "claude_refresh": refreshes,
        "proxy_refresh": {
            "headers": ours_headers,
            "body": ours_body.decode(),
        },
        "failures": failures,
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2))
    for failure in failures:
        print("FAIL:", failure)
    if not failures:
        print(f"refresh matches {version}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
