"""The shell that ships a home's transcripts, run for real.

The platform never sees this script's logic — only the last line it prints and
its exit code — so the branches are exercised here under `sh` against a real
home on disk, with a `curl` on PATH that records the request instead of
sending it. What is asserted is what the platform would have received and what
the home looks like afterwards.
"""

import os
import subprocess
import tarfile
import uuid
from pathlib import Path

import pytest

from app.domain.agent.device_provider import (
    TRANSCRIPT_MARK,
    transcript_upload_script,
)

FAKE_CURL = """#!/bin/sh
# The device's curl, minus the network: record the arguments, swallow the
# body, answer as the platform would.
printf '%s\\n' "$@" > "$FAKE_CURL_ARGS"
cat > "$FAKE_CURL_BODY"
[ "${FAKE_CURL_EXIT:-0}" -eq 0 ] || exit "$FAKE_CURL_EXIT"
printf '{"file":"x.tar.gz","size":1,"sha256":"y"}'
"""


class Device:
    def __init__(self, root: Path) -> None:
        self.home_root = root / "home"
        self.bin = root / "bin"
        self.bin.mkdir()
        curl = self.bin / "curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        self.args = root / "curl.args"
        self.body = root / "curl.body"
        self.project_id = uuid.uuid4()
        self.place_id = uuid.uuid4()
        self.home = (
            self.home_root / ".cheese/home" / str(self.project_id) / str(self.place_id)
        )

    def write(self, relative: str, content: str = "{}") -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def run(self, *, curl_exit: int = 0) -> subprocess.CompletedProcess[str]:
        self.args.unlink(missing_ok=True)
        self.body.unlink(missing_ok=True)
        return subprocess.run(
            ["sh", "-c", transcript_upload_script(self.project_id, self.place_id)],
            env={
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "HOME": str(self.home_root),
                "CHEESE_API": "https://cheese.example/api/connector",
                "CHEESE_TOKEN": "tok-device",
                "FAKE_CURL_ARGS": str(self.args),
                "FAKE_CURL_BODY": str(self.body),
                "FAKE_CURL_EXIT": str(curl_exit),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def uploaded_members(self) -> list[str]:
        with tarfile.open(self.body, "r:gz") as tar:
            return sorted(m.name for m in tar if m.isfile())


@pytest.fixture
def device(tmp_path: Path) -> Device:
    return Device(tmp_path)


def _outcome(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout.strip().splitlines()[-1]


def test_a_home_with_sessions_is_shipped_whole(device: Device):
    device.write(".claude/projects/-work/session.jsonl", '{"type":"user"}')
    device.write(".claude/todos/agent.json", "[]")
    device.write(".claude/settings.json")  # not transcripts, not shipped
    device.write("cache/big.bin", "x" * 1000)

    result = device.run()

    assert result.returncode == 0, result.stderr
    assert _outcome(result) == "uploaded"
    assert device.uploaded_members() == [
        ".claude/projects/-work/session.jsonl",
        ".claude/todos/agent.json",
    ]
    args = device.args.read_text().splitlines()
    assert "-T" in args and args[args.index("-T") + 1] == "-"
    assert "Authorization: Bearer tok-device" in args
    assert args[-1] == (
        "https://cheese.example/api/connector/transcripts/"
        f"{device.project_id}/{device.place_id}"
    )
    assert (device.home / TRANSCRIPT_MARK).exists()


def test_a_home_without_sessions_is_not_shipped(device: Device):
    device.write(".claude/settings.json")

    result = device.run()

    assert result.returncode == 0 and _outcome(result) == "none"
    assert not device.args.exists()


def test_todos_are_optional(device: Device):
    device.write(".claude/projects/-work/session.jsonl")

    result = device.run()

    assert _outcome(result) == "uploaded"
    assert device.uploaded_members() == [".claude/projects/-work/session.jsonl"]


def test_an_unchanged_home_is_not_shipped_twice_and_a_changed_one_is(device: Device):
    session = device.write(".claude/projects/-work/session.jsonl")
    assert _outcome(device.run()) == "uploaded"

    assert _outcome(device.run()) == "unchanged"
    assert not device.args.exists()

    # A session written after the upload started is newer than the mark.
    later = os.stat(device.home / TRANSCRIPT_MARK).st_mtime + 5
    os.utime(session, (later, later))
    assert _outcome(device.run()) == "uploaded"
    assert device.args.exists()


def test_a_refused_upload_fails_loudly_and_leaves_no_mark(device: Device):
    device.write(".claude/projects/-work/session.jsonl")

    result = device.run(curl_exit=22)

    assert result.returncode != 0
    assert "uploaded" not in result.stdout
    assert not (device.home / TRANSCRIPT_MARK).exists()
    # So the next run ships it again rather than calling it unchanged.
    assert _outcome(device.run()) == "uploaded"
