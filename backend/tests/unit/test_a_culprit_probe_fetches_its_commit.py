"""A culprit probe gets the commit it judges from the project's forge.

The probe program runs in a session's room, which is not a checkout, on a
machine whose copy of the repository was fetched when its tasks opened: a
squash merge that landed since is not in it. So the probe asks the platform's
CLI for a tree at that exact commit (`cheese revision-tree`), which fetches it
from the forge into the machine's repository cache with the session's own
credential, in a directory of its own. The test file it runs is the one at
that commit, not whatever the cache or the room holds.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from app.domain.agent import culprit_probe_script

CLI = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
SECRET = "forge-token-never-logged"
PROJECT = "7f3c1d2e-0000-4000-8000-000000000001"
GIT = ["-c", "user.name=T", "-c", "user.email=t@e", "-c", "commit.gpgsign=false"]


def _git(*args, cwd=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class _Platform(http.server.BaseHTTPRequestHandler):
    """The platform API the session's CLI talks to: only the forge binding."""

    forge = ""

    def do_GET(self):
        assert self.path == "/sandbox/forge-token", self.path
        body = json.dumps(
            {
                "data": {
                    "kind": "local",
                    "url": self.forge,
                    "repo": "acme/rocket",
                    "token": SECRET,
                    "username": "acme",
                    "default_branch": "main",
                }
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_a_squash_merge_newer_than_the_cache_is_fetched_and_judged(tmp_path):
    # The forge, with the commit the machine's tasks opened on.
    forge = tmp_path / "forge.git"
    _git("init", "-q", "--bare", "-b", "main", str(forge))
    seed = tmp_path / "seed"
    _git("clone", "-q", str(forge), str(seed))
    (seed / "pyproject.toml").write_text('[project]\nname = "rocket"\n')
    (seed / "uv.lock").write_text("locked\n")
    tests = seed / "tests"
    tests.mkdir()
    (tests / "test_launch.py").write_text("def test_launch():\n    assert True\n")
    _git("add", "-A", cwd=seed)
    _git(*GIT, "commit", "-qm", "base", cwd=seed)
    _git("push", "-q", "origin", "HEAD:main", cwd=seed)

    # The session machine: a repository cache fetched then, and a room that
    # is not a checkout.
    home = tmp_path / "home"
    room = home / "room"
    room.mkdir(parents=True)
    cache = home / ".cheese" / "repositories" / f"{PROJECT}.git"
    cache.parent.mkdir(parents=True)
    _git("init", "-q", "--bare", str(cache))
    _git("-C", str(cache), "remote", "add", "origin", forge.as_uri())
    _git(
        "-C",
        str(cache),
        "fetch",
        "-q",
        "origin",
        "refs/heads/main:refs/remotes/origin/main",
    )

    # Later, a squash merge lands on the forge and breaks the test.
    (tests / "test_launch.py").write_text("def test_launch():\n    assert False\n")
    _git(*GIT, "commit", "-qam", "squash: break launch", cwd=seed)
    _git("push", "-q", "origin", "HEAD:main", cwd=seed)
    fresh = _git("rev-parse", "HEAD", cwd=seed)

    # `uv` runs the probed tree's test as written at that commit.
    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "if sys.argv[1] == 'sync': sys.exit(0)\n"
        "report = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--junitxml='))\n"
        "broken = 'assert False' in pathlib.Path('tests/test_launch.py').read_text()\n"
        "case = '<testcase>' + ('<failure/>' if broken else '') + '</testcase>'\n"
        "pathlib.Path(report).write_text('<testsuite>' + case + '</testsuite>')\n"
        "sys.exit(1 if broken else 0)\n"
    )
    uv.chmod(0o755)
    cheese = binary / "cheese"
    cheese.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{CLI}" "$@"\n')
    cheese.chmod(0o755)

    _Platform.forge = forge.as_uri()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Platform)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        done = subprocess.run(
            [sys.executable, "-", fresh, json.dumps(["tests/test_launch.py"]), "n1"],
            input=Path(culprit_probe_script.__file__).read_bytes(),
            cwd=room,
            env={
                **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                "HOME": str(home),
                "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
                "CHEESE_TOKEN": "session-credential",
                "CHEESE_PROJECT": PROJECT,
                "NO_PROXY": "127.0.0.1",
                "no_proxy": "127.0.0.1",
            },
            capture_output=True,
            timeout=120,
        )
    finally:
        server.shutdown()

    record = home / ".cheese" / "culprit-probes" / "n1"
    verdict = (record / "verdict").read_text()
    kept = {path.name: path.read_text(errors="replace") for path in record.iterdir()}
    assert verdict == "fail", kept
    assert done.returncode == 1
    # The probe's own tree is gone; the room was never written.
    assert not (record / "tree").exists()
    assert list(room.iterdir()) == []
    # The forge credential stays out of what the probe keeps.
    for name, text in kept.items():
        assert SECRET not in text, name
