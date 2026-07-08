"""Unit tests for cross-machine agent cloning (the「复制自」transfer).

No real device: a fake exec hub interprets the exact shell snippets ``clone.py`` emits,
backed by an in-memory per-device filesystem. Covers the pure fork/slug helpers and an
end-to-end ``clone_session`` including the chunked base64 transfer (``_CHUNK`` shrunk so
a small payload spans multiple chunks).
"""

import base64
import re
from typing import Any

import pytest

from app.agent import clone
from app.core.errors import PreconditionFailedError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_slug_for_matches_claude_convention() -> None:
    assert clone.slug_for("/home/u/repo/App") == "-home-u-repo-App"
    # dots also become dashes (Claude's slug rule)
    assert clone.slug_for("/home/u/a.b/c") == "-home-u-a-b-c"


def test_fork_transcript_rewrites_every_session_id() -> None:
    data = b'{"sessionId":"OLD","x":1}\n{"parent":"OLD"}\n'
    out = clone.fork_transcript(data, "OLD", "NEW")
    assert out == b'{"sessionId":"NEW","x":1}\n{"parent":"NEW"}\n'
    assert b"OLD" not in out


class FakeExecHub:
    """Interprets the specific bash snippets clone.py emits against an in-memory
    filesystem keyed by literal path strings, per device."""

    def __init__(self) -> None:
        self.fs: dict[str, dict[str, bytes]] = {}  # device_id -> {path: content}
        self.dirs: dict[str, set[str]] = {}  # device_id -> existing dirs

    def seed(self, device_id: str, path: str, content: bytes) -> None:
        self.fs.setdefault(device_id, {})[path] = content

    def add_dir(self, device_id: str, path: str) -> None:
        self.dirs.setdefault(device_id, set()).add(path)

    async def exec(
        self,
        device_id: str,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60,
        stdin: str | None = None,
    ) -> dict[str, Any]:
        fs = self.fs.setdefault(device_id, {})
        script = argv[-1]

        def ok(stdout: str = "") -> dict[str, Any]:
            return {"stdout": stdout, "stderr": "", "exit": 0}

        def fail() -> dict[str, Any]:
            return {"stdout": "", "stderr": "no such file", "exit": 1}

        if script.startswith("test -d "):
            path = script[len("test -d ") :].strip().strip("'")
            return ok() if path in self.dirs.get(device_id, set()) else fail()
        if script.startswith("if echo Zm9v"):
            return ok("-d")  # GNU-style decode flag
        if script.startswith("stat -c %s"):
            m = re.match(r"stat -c %s (.+?) 2>/dev/null", script)
            path = m.group(1) if m else ""
            return ok(str(len(fs.get(path, b"")))) if path in fs else ok("0")
        if script.startswith("dd if="):
            m = re.match(r"dd if=(.+?) bs=(\d+) skip=(\d+) count=1", script)
            assert m
            path, bs, skip = m.group(1), int(m.group(2)), int(m.group(3))
            chunk = fs.get(path, b"")[skip * bs : skip * bs + bs]
            return ok(base64.b64encode(chunk).decode())
        if script.startswith("mkdir -p"):
            m = re.search(r": > (.+)$", script)
            if m:
                fs[m.group(1)] = b""
            return ok()
        if script.startswith("base64 -d >> "):
            path = script[len("base64 -d >> ") :].strip()
            fs[path] = fs.get(path, b"") + base64.b64decode(stdin or "")
            return ok()
        if script.startswith("mv "):
            m = re.match(r"mv (.+?) (.+)$", script)
            assert m
            fs[m.group(2)] = fs.pop(m.group(1), b"")
            return ok()
        raise AssertionError(f"unexpected script: {script}")


async def test_clone_session_forks_transcript_across_devices(monkeypatch) -> None:
    monkeypatch.setattr(clone, "_CHUNK", 4)  # force multi-chunk transfer of a small payload
    hub = FakeExecHub()
    source_cwd = "/home/u/repo/App"
    target_cwd = "/home/u/repo/App"
    old_sid, new_sid = "old-session", "new-session"
    src_path = clone.transcript_path(source_cwd, old_sid)
    content = b'{"sessionId":"old-session","msg":"hello world, this spans chunks"}\n'
    hub.seed("srcdev", src_path, content)
    hub.add_dir("tgtdev", target_cwd)

    await clone.clone_session(
        hub,  # type: ignore[arg-type]
        source_device_id="srcdev",
        source_cwd=source_cwd,
        source_session_id=old_sid,
        target_device_id="tgtdev",
        target_cwd=target_cwd,
        new_session_id=new_sid,
    )

    dst_path = clone.transcript_path(target_cwd, new_sid)
    written = hub.fs["tgtdev"][dst_path]
    assert b"old-session" not in written  # session id was forked
    assert b'"sessionId":"new-session"' in written
    assert b"hello world, this spans chunks" in written  # payload intact across chunks


async def test_clone_session_creates_missing_target_dir() -> None:
    # 目标 cwd 在目标机上不存在时，clone 不该失败：它会 `mkdir -p` 建出目录并照常写入
    # transcript（空目录也能 `claude --resume`，仓库内容缺失是用户后续的事）。
    hub = FakeExecHub()
    hub.seed("srcdev", clone.transcript_path("/a", "old-sid"), b'{"sessionId":"old-sid"}\n')
    # 注意：这里刻意不 add_dir("tgtdev", "/missing")，即目标目录不存在。
    await clone.clone_session(
        hub,  # type: ignore[arg-type]
        source_device_id="srcdev",
        source_cwd="/a",
        source_session_id="old-sid",
        target_device_id="tgtdev",
        target_cwd="/missing",
        new_session_id="new-sid",
    )
    dst_path = clone.transcript_path("/missing", "new-sid")
    written = hub.fs["tgtdev"][dst_path]
    assert b'"sessionId":"new-sid"' in written  # 目录已被创建且 transcript 正常写入


async def test_clone_session_rejects_empty_source() -> None:
    hub = FakeExecHub()
    hub.add_dir("tgtdev", "/a")
    with pytest.raises(PreconditionFailedError, match="empty or missing"):
        await clone.clone_session(
            hub,  # type: ignore[arg-type]
            source_device_id="srcdev",
            source_cwd="/a",
            source_session_id="s",
            target_device_id="tgtdev",
            target_cwd="/a",
            new_session_id="n",
        )
