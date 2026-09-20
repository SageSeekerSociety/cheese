import asyncio
import base64
import subprocess
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["timeout", "offline", "starting"])
async def test_unavailable_live_files_offer_committed_version(monkeypatch, failure):
    from app.core.errors import GatewayUnavailableError
    from app.domain.agent.device_hub import DeviceNotReady, DeviceOffline
    from app.domain.workspace import forge_files

    task = SimpleNamespace(id=uuid.uuid4(), room_id=uuid.uuid4())
    room = SimpleNamespace(session_placement={"execution": {"kind": "device"}})
    session = SimpleNamespace(get=AsyncMock(return_value=room))
    files = forge_files.ProjectFiles(session, uuid.uuid4(), task.id)
    monkeypatch.setattr(files, "task", AsyncMock(return_value=task))
    error = {
        "timeout": TimeoutError(),
        "offline": DeviceOffline("test-device"),
        "starting": DeviceNotReady("starting"),
    }[failure]
    monkeypatch.setattr(forge_files.execution, "call", AsyncMock(side_effect=error))
    with pytest.raises(GatewayUnavailableError, match="重新读取.*已提交版本"):
        await files.live("write", path="report.md", content="edit", version="old")


@pytest.mark.anyio
async def test_live_file_deadline_cancels_the_device_request(monkeypatch):
    from app.core.errors import GatewayUnavailableError
    from app.domain.workspace import forge_files

    task = SimpleNamespace(id=uuid.uuid4(), room_id=uuid.uuid4())
    room = SimpleNamespace(session_placement={"execution": {"kind": "device"}})
    session = SimpleNamespace(get=AsyncMock(return_value=room))
    files = forge_files.ProjectFiles(session, uuid.uuid4(), task.id)
    monkeypatch.setattr(files, "task", AsyncMock(return_value=task))
    timeout = asyncio.timeout
    cancelled = asyncio.Event()

    def fast_deadline(seconds):
        assert seconds == 30
        return timeout(0.001)

    async def stalled(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(forge_files.asyncio, "timeout", fast_deadline)
    monkeypatch.setattr(forge_files.execution, "call", stalled)
    with pytest.raises(GatewayUnavailableError):
        await files.live("tree")
    assert cancelled.is_set()


@pytest.fixture
def live(tmp_path):
    task_id = str(uuid.uuid4())
    root = tmp_path / ".cheese" / "tasks" / task_id
    root.mkdir(parents=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    executor = object.__new__(Executor)
    executor.env = {"HOME": str(tmp_path)}

    def call(operation, **params):
        return executor.task_fs({"task_id": task_id, "operation": operation, **params})

    return root, call


def test_live_read_and_conditional_save_preserve_concurrent_agent_edit(live):
    root, call = live
    (root / "report.md").write_text("draft")
    read = call("read", path="report.md")
    assert base64.b64decode(read["data"]) == b"draft"
    (root / "report.md").write_text("agent edit")
    assert call(
        "write", path="report.md", version=read["version"], content="human edit"
    ) == {"error": "conflict"}
    assert (root / "report.md").read_text() == "agent edit"
    current = call("read", path="report.md")
    saved = call(
        "write", path="report.md", version=current["version"], content="reviewed edit"
    )
    assert saved["version"] != current["version"]
    assert (root / "report.md").read_text() == "reviewed edit"


def test_tree_includes_untracked_files_but_cannot_follow_escape_links(live, tmp_path):
    root, call = live
    (root / "draft.bin").write_bytes(b"\xff\x00")
    secret = tmp_path / "outside-secret"
    secret.write_text("private")
    (root / "escape").symlink_to(secret)
    assert call("tree")["files"] == [{"path": "draft.bin", "bytes": 2}]
    for path in ("escape", "../outside-secret", ".git/config", str(secret)):
        with pytest.raises(ValueError):
            call("read", path=path)


def test_binary_and_unversioned_writes_are_refused(live):
    root, call = live
    (root / "image.bin").write_bytes(b"\x00\xff")
    read = call("read", path="image.bin")
    assert call("write", path="image.bin", content="broken") == {"error": "conflict"}
    with pytest.raises(ValueError, match="Binary"):
        call("write", path="image.bin", content="broken", version=read["version"])
    assert (root / "image.bin").read_bytes() == b"\x00\xff"


def test_office_revision_save_checks_version_and_preserves_file_mode(live):
    root, call = live
    path = root / "report.docx"
    path.write_bytes(b"PK\x00original document")
    path.chmod(0o640)
    read = call("read", path=path.name)
    replacement = b"PK\x00reviewed document"
    encoded = base64.b64encode(replacement).decode()
    saved = call("write_bytes", path=path.name, version=read["version"], data=encoded)
    assert path.read_bytes() == replacement
    assert path.stat().st_mode & 0o777 == 0o640
    assert saved["version"] != read["version"]
    assert call(
        "write_bytes", path=path.name, version=read["version"], data=encoded
    ) == {"error": "conflict"}
    assert not list(root.glob("*.cheese-save-*"))


def test_chunked_read_detects_replacement_between_chunks(live):
    root, call = live
    path = root / "large.bin"
    path.write_bytes(b"first part;second part")
    first = call("read", path=path.name, size=11)
    assert base64.b64decode(first["data"]) == b"first part;"
    path.write_bytes(b"first part;new content")
    assert call("read", path=path.name, offset=11, version=first["version"]) == {
        "error": "conflict"
    }


def test_live_diff_includes_committed_staged_unstaged_and_untracked_work(live):
    root, call = live

    def git(*args):
        return subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Agent",
                "-c",
                "user.email=agent@users.invalid",
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    (root / "report.txt").write_text("Original report\n")
    git("add", ".")
    git("commit", "-m", "Initial")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    (root / "report.txt").write_text("Committed report\n")
    git("commit", "-am", "Update report")
    (root / "staged.txt").write_text("Staged addition\n")
    git("add", "staged.txt")
    (root / "report.txt").write_text("Live report\n")
    (root / "untracked.txt").write_text("Untracked addition\n")
    before = git("status", "--porcelain")
    diff = call("diff", base_branch="main")["diff"]
    assert "-Original report" in diff and "+Live report" in diff
    assert "+Staged addition" in diff and "+Untracked addition" in diff
    assert git("status", "--porcelain") == before
