#!/usr/bin/env python3
"""Exercise the native fj binary against a merged-PR HTTP fixture in a terminal."""

import argparse
import errno
import json
import os
import pty
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-panic", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    requests = []
    repository = {
        "name": "project",
        "full_name": "fixture/project",
        "owner": {"login": "fixture"},
        "archived": False,
    }
    fixture = {
        "id": 1,
        "number": 1,
        "title": "Merged status regression",
        "state": "closed",
        "merged": True,
        "requested_reviewers": [],
        "requested_reviewers_teams": [],
        "merged_by": {"login": "fixture-reviewer"},
        "created_at": "1999-01-02T03:04:05Z",
        "merged_at": "2001-02-03T04:05:06Z",
        "user": {"login": "fixture-author"},
        "base": {"label": "main", "repo": repository},
        "head": {"label": "change", "repo": repository},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            responses = {
                "/api/v1/repos/fixture/project/pulls/1": fixture,
                "/api/v1/repos/fixture/project": repository,
                "/api/v1/repos/fixture/project/pulls/1/files": [],
                "/api/v1/repos/fixture/project/issues": [fixture],
            }
            path = self.path.split("?", 1)[0]
            if path in responses:
                content = json.dumps(responses[path]).encode()
                self.send_response(200)
            else:
                content = b'{"message":"unexpected fixture request"}'
                self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Total-Count", "1" if path.endswith("/issues") else "0")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="fj-status-") as directory:
            root = Path(directory)
            host = f"127.0.0.1:{server.server_port}"
            keys = {"hosts": {host: {"type": "Application", "token": "fixture-token"}}}
            for path in (
                root / "data/forgejo-cli/keys.json",
                root / "Library/Application Support/forgejo-cli.forgejo-cli/keys.json",
            ):
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(keys))
                path.chmod(0o600)
            command = [
                *options.command,
                "--host",
                f"http://{host}",
                "pr",
                "status",
                "fixture/project#1",
            ]
            for language, word in (
                ("en-US", "Merged"),
                ("zh-Hans", "已合并"),
                ("de-DE", "Zusammengeführt"),
            ):
                env = {
                    **os.environ,
                    "HOME": directory,
                    "XDG_DATA_HOME": str(root / "data"),
                    "LC_MESSAGES": language,
                }
                master, slave = pty.openpty()
                process = subprocess.Popen(
                    command, stdout=slave, stderr=slave, env=env, cwd=root
                )
                os.close(slave)
                timeout = threading.Timer(30, process.kill)
                timeout.start()
                output = bytearray()
                try:
                    while True:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError as error:
                            if error.errno != errno.EIO:
                                raise
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                    code = process.wait()
                finally:
                    timeout.cancel()
                    os.close(master)
                rendered = output.decode("utf-8", errors="replace")
                print(
                    json.dumps(
                        {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "command": command,
                            "language": language,
                            "fixture": fixture,
                            "exit_code": code,
                            "output": rendered,
                            "requests": requests,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if options.expect_panic:
                    assert code == 101 and "created_at" in rendered, rendered
                else:
                    assert code == 0, rendered
                    assert word in rendered and "fixture-reviewer" in rendered, rendered
                    assert "2001" in rendered and "1999" not in rendered, rendered
            for arguments in (
                ["pr", "view", "fixture/project#1"],
                ["pr", "search", "-r", "fixture/project", "-s", "all"],
            ):
                probe = [*options.command, "--host", f"http://{host}", *arguments]
                result = subprocess.run(
                    probe, env=env, cwd=root, capture_output=True, text=True, timeout=30
                )
                print(
                    json.dumps(
                        {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "command": probe,
                            "fixture": fixture,
                            "exit_code": result.returncode,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert result.returncode == 0, result.stderr
                assert fixture["title"] in result.stdout, result.stdout
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
