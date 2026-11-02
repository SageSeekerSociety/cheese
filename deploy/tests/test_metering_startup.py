"""The metering proxy survives TLS clients that arrive while it starts.

A release recreates the proxy with every session still dialling it, so the
first handshakes land the moment mitmproxy's listeners open. mitmproxy opens
them before its `running` event, and its TLS addon builds the certificate
store only on that event unless `confdir` is set as an option at startup
(tlsconfig.py, `running`: "configure ... is never called with confdir"). A
handshake that lands in between fails in `tls_start_client` with
`'NoneType' object has no attribute 'get_cert'`, and mitmproxy exits on any
error logged during startup. That is how the proxy release in deploy run
41392766820 died at 05:29:25 UTC and was rolled back.

This starts mitmdump with the command line compose.yml gives the container,
fires TLS handshakes at both listeners from before they open, and requires the
process to still be serving afterwards. Run it inside the proxy's base image,
as deploy-scripts-test.yml does:

  docker run --rm --network none --entrypoint python -v "$PWD:/work:ro" -w /work \
    mitmproxy/mitmproxy:12.1.2@sha256:... deploy/tests/test_metering_startup.py
"""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shlex
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

PROXY = Path(__file__).resolve().parents[1] / "metering-proxy"
SECRET = "startup-test-secret"
# The container's confdir: where compose mounts CERTS_DIR.
CONTAINER_CONFDIR = "/home/mitmproxy/.mitmproxy"
CLIENTS = 16
SERVE_AFTER_LISTEN_S = 3.0
ATTEMPTS = 5


def compose_command() -> list[str]:
    """The `command:` of the metering-proxy service, as the container gets it."""
    lines = (PROXY / "compose.yml").read_text().splitlines()
    start = lines.index("    command: >") + 1
    folded = []
    for line in lines[start:]:
        if not line.startswith("      "):
            break
        folded.append(line.strip())
    return shlex.split(" ".join(folded))


def scoped_token() -> str:
    """A session credential the CONNECT listener accepts (cheese_billing_core)."""

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = b64(json.dumps({"alg": "HS256", "typ": "agent+jwt"}).encode())
    payload = b64(
        json.dumps({"p": "project", "t": "topic", "exp": time.time() + 3600}).encode()
    )
    key = hmac.digest(SECRET.encode(), b"cheese:credential:agent", hashlib.sha256)
    signature = hmac.new(key, f"{header}.{payload}".encode(), hashlib.sha256)
    return f"{header}.{payload}.{b64(signature.digest())}"


def client_hello(sni: str) -> bytes:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = context.wrap_bio(incoming, outgoing, server_hostname=sni)
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


def hammer(port: int, opening: bytes, stop: threading.Event) -> None:
    """Connect and start a TLS handshake again and again, from before the
    listener exists until told to stop."""
    while not stop.is_set():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as conn:
                conn.sendall(opening)
                conn.recv(4096)
        except OSError:
            time.sleep(0.001)


class MeteringStartupTest(unittest.TestCase):
    def start_under_load(self, workdir: Path) -> tuple[int | None, str]:
        command = [
            arg.replace("/addons/billing_addon.py", str(PROXY / "billing_addon.py"))
            .replace(CONTAINER_CONFDIR, str(workdir / "confdir"))
            for arg in compose_command()
        ]
        env = dict(
            os.environ,
            HOME=str(workdir),
            CHEESE_SCOPED_SECRET=SECRET,
            CHEESE_USAGE_LOG=str(workdir / "logs/usage.jsonl"),
            CHEESE_CLAUDE_CREDENTIAL=str(workdir / "claude/credential"),
            CHEESE_CHATGPT_CREDENTIALS=str(workdir / "chatgpt"),
            CHEESE_CHATGPT_KEY="startup-test-key",
        )
        (workdir / "logs").mkdir()
        authorization = base64.b64encode(f"cheese:{scoped_token()}".encode()).decode()
        hello = client_hello("api.anthropic.com")
        openings = {
            # A session's CONNECT with its ClientHello right behind it.
            8444: (
                b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n"
                b"Host: api.anthropic.com:443\r\n"
                b"Proxy-Authorization: Basic " + authorization.encode() + b"\r\n\r\n"
            )
            + hello,
            # The ChatGPT listener takes TLS as well as plain HTTP.
            8445: hello,
        }
        stop = threading.Event()
        clients = [
            threading.Thread(target=hammer, args=(port, opening, stop), daemon=True)
            for port, opening in openings.items()
            for _ in range(CLIENTS // len(openings))
        ]
        for client in clients:
            client.start()
        output = workdir / "mitmdump.log"
        with output.open("wb") as log:
            proxy = subprocess.Popen(
                command, env=env, stdout=log, stderr=subprocess.STDOUT
            )
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and proxy.poll() is None:
                    if "listening at *:8445" in output.read_text(errors="replace"):
                        break
                    time.sleep(0.01)
                else:
                    self.assertIsNotNone(proxy.poll(), "mitmdump never listened")
                time.sleep(SERVE_AFTER_LISTEN_S)
                status = proxy.poll()
            finally:
                stop.set()
                if proxy.poll() is None:
                    proxy.terminate()
                    proxy.wait(timeout=10)
        for client in clients:
            client.join(timeout=5)
        return status, output.read_text(errors="replace")

    def test_tls_clients_during_startup_do_not_stop_the_proxy(self):
        # The race is a window of a few milliseconds, so one clean start
        # proves little; every attempt has to survive.
        for attempt in range(ATTEMPTS):
            with self.subTest(attempt=attempt), tempfile.TemporaryDirectory() as tmp:
                status, output = self.start_under_load(Path(tmp))
                self.assertIsNone(
                    status,
                    f"mitmdump exited ({status}) while clients connected during "
                    f"startup:\n{output[-4000:]}",
                )
                self.assertNotIn("Addon error", output)
                # The handshakes really reached the TLS layer.
                self.assertIn("client connect", output)


if __name__ == "__main__":
    unittest.main()
