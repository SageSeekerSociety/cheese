"""Exercise the shipped fj launcher against the isolated Forgejo instance."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

forge_url = os.environ.get("FORGEJO_TEST_URL", "http://127.0.0.1:3000")
wrapper = os.environ.get("FORGEJO_TEST_WRAPPER", "/test/wrappers/fj")


class Credentials(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/sandbox/forge-token"
        assert self.headers["X-Cheese-Token"] == "isolated-smoke-test"
        token = Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "data": {
                        "kind": "forgejo",
                        "token": token,
                        "api_url": "http://internal-only.invalid/api/v1",
                        "url": f"{forge_url}/test/repo.git",
                        "repo": "test/repo",
                    }
                }
            ).encode()
        )

    def log_message(self, *_):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Credentials)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    env = dict(
        os.environ,
        CHEESE_API=f"http://127.0.0.1:{server.server_port}",
        CHEESE_TOKEN="isolated-smoke-test",
    )
    result = subprocess.run(
        [sys.executable, wrapper, "--host", forge_url, "whoami"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "cheese-platform" in result.stdout, result.stdout
    assert not list((Path.home() / ".cheese/forge-auth").rglob("keys.json"))
    print("Native fj authenticated against Forgejo; temporary credential removed.")
finally:
    server.shutdown()
    server.server_close()
    thread.join()
