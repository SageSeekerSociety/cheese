"""Test logging the platform's ChatGPT accounts in and out, as the metering proxy
sees them: against a stand-in for OpenAI's device login, directly and through
an account's egress."""

import base64
import json
import os
import select
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/metering-proxy/chatgpt-login.sh"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def jwt(claims: dict) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


ID_TOKEN = jwt(
    {
        "sub": "user-1",
        "https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"},
    }
)


class FakeOpenAI(ThreadingHTTPServer):
    """OpenAI's device login: a code, one pending poll, then the exchange."""

    def __init__(self, id_token=ID_TOKEN):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests: list[tuple[str, str, bytes]] = []
        self.polls = 0
        self.id_token = id_token


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        server = self.server
        server.requests.append((self.path, self.headers.get("Content-Type", ""), body))
        if self.path == "/api/accounts/deviceauth/usercode":
            self.answer(
                200,
                {
                    "device_auth_id": "dev-1",
                    "user_code": "ABCD-1234",
                    "interval": 1,
                    "expires_in": 60,
                },
            )
        elif self.path == "/api/accounts/deviceauth/token":
            server.polls += 1
            if server.polls == 1:
                self.answer(403, {"error": "authorization_pending"})
            else:
                self.answer(
                    200, {"authorization_code": "code-1", "code_verifier": "ver-1"}
                )
        elif self.path == "/oauth/token":
            self.answer(
                200,
                {
                    "access_token": "at-1",
                    "refresh_token": "rt-1",
                    "id_token": server.id_token,
                    "expires_in": 3600,
                },
            )
        else:
            self.answer(404, {})

    def answer(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FakeEgress:
    """An HTTP proxy that records each CONNECT and relays the tunnel."""

    def __init__(self):
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.connects: list[bytes] = []
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.relay, args=(client,), daemon=True).start()

    def relay(self, client):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = client.recv(4096)
            if not chunk:
                return
            head += chunk
        self.connects.append(head)
        target = head.split(b" ")[1].decode()
        host, port = target.rsplit(":", 1)
        upstream = socket.create_connection((host, int(port)))
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        sockets = [client, upstream]
        while True:
            readable, _, _ = select.select(sockets, [], [], 10)
            if not readable:
                break
            for sock in readable:
                data = sock.recv(65536)
                if not data:
                    client.close()
                    upstream.close()
                    return
                (upstream if sock is client else client).sendall(data)

    def close(self):
        self.listener.close()


class ChatGPTLoginTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.home = Path(self.folder.name)
        self.openai = FakeOpenAI()
        threading.Thread(target=self.openai.serve_forever, daemon=True).start()
        self.env = dict(
            os.environ,
            METERING_PROXY_HOME=str(self.home),
            OPENAI_AUTH_BASE=f"http://127.0.0.1:{self.openai.server_address[1]}",
        )

    def tearDown(self):
        self.openai.shutdown()
        self.openai.server_close()
        self.folder.cleanup()

    def credential(self, name="work") -> Path:
        return self.home / "chatgpt-credential" / name / "credential"

    def run_script(self, *args, stdin=""):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_a_device_login_leaves_the_pair_with_the_proxy(self):
        before = time.time()
        result = self.run_script("login", "work")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ABCD-1234", result.stderr)
        stored = json.loads(self.credential().read_text())
        self.assertEqual(
            {k: stored[k] for k in ("access_token", "refresh_token", "account_id")},
            {"access_token": "at-1", "refresh_token": "rt-1", "account_id": "acct-1"},
        )
        self.assertEqual(stored["id_token"], ID_TOKEN)
        self.assertGreaterEqual(stored["expires_at"], int(before) + 3600)
        self.assertLessEqual(stored["expires_at"], time.time() + 3600)
        self.assertEqual(self.credential().stat().st_mode & 0o777, 0o600)
        leftovers = [p.name for p in self.credential().parent.iterdir()]
        self.assertEqual(leftovers, ["credential"])

        paths = [path for path, _type, _body in self.openai.requests]
        self.assertEqual(
            paths,
            [
                "/api/accounts/deviceauth/usercode",
                "/api/accounts/deviceauth/token",
                "/api/accounts/deviceauth/token",
                "/oauth/token",
            ],
        )
        self.assertEqual(
            json.loads(self.openai.requests[0][2]), {"client_id": CLIENT_ID}
        )
        self.assertEqual(
            json.loads(self.openai.requests[1][2]),
            {"device_auth_id": "dev-1", "user_code": "ABCD-1234"},
        )
        _path, content_type, body = self.openai.requests[-1]
        self.assertEqual(content_type, "application/x-www-form-urlencoded")
        self.assertEqual(
            dict(urllib.parse.parse_qsl(body.decode())),
            {
                "grant_type": "authorization_code",
                "code": "code-1",
                "redirect_uri": "https://auth.openai.com/deviceauth/callback",
                "client_id": CLIENT_ID,
                "code_verifier": "ver-1",
            },
        )

    def test_status_shows_the_account_without_its_tokens(self):
        self.run_script("login", "work")

        status = self.run_script("status", "work").stdout

        self.assertIn("logged in", status)
        self.assertIn("acct-1", status)
        self.assertNotIn("at-1", status)
        self.assertNotIn("rt-1", status)

    def test_a_login_that_names_no_account_changes_nothing(self):
        self.openai.id_token = jwt({"email": "nobody@example.com"})

        result = self.run_script("login", "work")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.credential().exists())

    def test_a_login_goes_through_the_accounts_egress(self):
        egress = FakeEgress()
        self.addCleanup(egress.close)
        url = f"http://me:secret@127.0.0.1:{egress.port}"
        self.assertEqual(self.run_script("egress", "set", "work", url).returncode, 0)

        result = self.run_script("login", "work")

        self.assertEqual(result.returncode, 0, result.stderr)
        # One tunnel per request, each authenticated as the egress asks.
        self.assertEqual(len(egress.connects), len(self.openai.requests))
        target = f"CONNECT 127.0.0.1:{self.openai.server_address[1]} "
        for head in egress.connects:
            self.assertTrue(head.decode().startswith(target), head)
            self.assertIn(
                b"Proxy-Authorization: Basic " + base64.b64encode(b"me:secret"), head
            )
        self.assertEqual(
            json.loads(self.credential().read_text())["access_token"], "at-1"
        )

    def test_accounts_are_kept_apart_and_listed(self):
        self.run_script("login", "work")
        self.run_script("egress", "set", "spare", "http://10.0.0.5:3128")

        listed = self.run_script("ls").stdout

        self.assertIn("work: logged in, direct", listed)
        self.assertIn("spare: logged out, through an egress", listed)
        self.assertFalse(self.credential("spare").exists())

    def test_logging_out_removes_only_that_accounts_credential(self):
        self.run_script("login", "work")
        self.run_script("egress", "set", "work", "http://10.0.0.5:3128")

        result = self.run_script("logout", "work")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.credential().exists())
        self.assertTrue((self.credential().parent / "egress").exists())
        self.assertIn("logged out", self.run_script("status", "work").stdout)

    def test_an_egress_is_set_shown_without_its_password_and_cleared(self):
        result = self.run_script(
            "egress", "set", "work", "http://me:secret@10.0.0.5:3128"
        )
        egress = self.credential().parent / "egress"

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(egress.read_text().strip(), "http://me:secret@10.0.0.5:3128")
        self.assertEqual(egress.stat().st_mode & 0o777, 0o600)
        status = self.run_script("status", "work").stdout
        self.assertIn("egress: http://***@10.0.0.5:3128", status)
        self.assertNotIn("secret", status)

        self.run_script("egress", "clear", "work")
        self.assertFalse(egress.exists())
        self.assertIn("egress: direct", self.run_script("status", "work").stdout)

    def test_something_that_is_not_an_http_proxy_is_refused(self):
        result = self.run_script("egress", "set", "work", "socks5://10.0.0.5:1080")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.credential().parent / "egress").exists())

    def test_a_name_that_could_leave_the_directory_is_refused(self):
        for name in ("../work", ".hidden", "a/b", ""):
            result = self.run_script("egress", "set", name, "http://10.0.0.5:3128")
            self.assertNotEqual(result.returncode, 0, name)
        self.assertFalse((self.home / "egress").exists())
        self.assertEqual(list(self.home.rglob("egress")), [])

    def test_an_imported_pair_becomes_the_proxys(self):
        source = self.home / "held-elsewhere.json"
        source.write_text(
            json.dumps(
                {
                    "access_token": "at-9",
                    "refresh_token": "rt-9",
                    "id_token": ID_TOKEN,
                    "expires_at": 1900000000,
                    "account_id": "acct-9",
                }
            )
        )

        result = self.run_script("import", "work", str(source))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(self.credential().read_text()),
            {
                "access_token": "at-9",
                "refresh_token": "rt-9",
                "id_token": ID_TOKEN,
                "expires_at": 1900000000,
                "account_id": "acct-9",
            },
        )
        self.assertEqual(self.credential().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.openai.requests, [])

    def test_an_import_without_expiry_takes_it_from_the_access_token(self):
        source = self.home / "held-elsewhere.json"
        source.write_text(
            json.dumps(
                {
                    "access_token": jwt({"exp": 1900001234}),
                    "refresh_token": "rt-9",
                    "id_token": ID_TOKEN,
                }
            )
        )

        result = self.run_script("import", "work", str(source))

        self.assertEqual(result.returncode, 0, result.stderr)
        stored = json.loads(self.credential().read_text())
        self.assertEqual(stored["expires_at"], 1900001234)
        self.assertEqual(stored["account_id"], "acct-1")

    def test_an_import_that_is_not_a_usable_pair_changes_nothing(self):
        self.run_script("login", "work")
        before = self.credential().read_text()
        for bad in (
            "not json",
            json.dumps({"access_token": "at-9", "expires_at": 1900000000}),
            json.dumps({"access_token": "opaque", "refresh_token": "rt-9"}),
        ):
            source = self.home / "bad.json"
            source.write_text(bad)
            result = self.run_script("import", "work", str(source))
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertEqual(self.credential().read_text(), before)

    def test_the_client_version_is_set_shown_and_cleared(self):
        version = self.home / "chatgpt-credential" / "client-version"
        self.assertIn("none set", self.run_script("client-version", "show").stdout)

        result = self.run_script("client-version", "set", "0.160.1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(version.read_text().strip(), "0.160.1")
        self.assertEqual(version.stat().st_mode & 0o777, 0o600)
        self.assertIn("0.160.1", self.run_script("client-version", "show").stdout)

        self.run_script("client-version", "set", "0.161.0")
        self.assertEqual(version.read_text().strip(), "0.161.0")

        result = self.run_script("client-version", "clear")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(version.exists())
        self.assertIn("none set", self.run_script("client-version", "show").stdout)

    def test_a_client_version_no_header_should_carry_is_refused(self):
        self.run_script("client-version", "set", "0.160.1")
        for bad in ("", "0.1 0", "1.0\nx-injected: yes", "../1"):
            result = self.run_script("client-version", "set", bad)
            self.assertNotEqual(result.returncode, 0, bad)
        version = self.home / "chatgpt-credential" / "client-version"
        self.assertEqual(version.read_text().strip(), "0.160.1")

    def test_the_client_version_is_not_listed_or_usable_as_an_account(self):
        self.run_script("login", "work")
        self.run_script("client-version", "set", "0.160.1")

        listed = self.run_script("ls").stdout
        status = self.run_script("status").stdout

        self.assertNotIn("client-version", listed + status)
        self.assertIn("work: logged in", listed)
        result = self.run_script("egress", "set", "client-version", "http://10.0.0.5:3128")
        self.assertNotEqual(result.returncode, 0)
        version = self.home / "chatgpt-credential" / "client-version"
        self.assertEqual(version.read_text().strip(), "0.160.1")


if __name__ == "__main__":
    unittest.main()
