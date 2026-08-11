"""The backend and the sandbox agent must run as ONE uid over the shared jj store.

`ws.sandbox_vcs_mounts` bind-mounts a project's main-repo `.jj`/`.git` into every
sandbox container read-write, so the backend process and the in-container agent
write to the same store. jj creates its store objects — `.jj/repo/config-id`
above all — with a hardcoded 0600 (a tempfile that gets persisted, NOT
`0666 & ~umask`), so under two uids whichever side writes first locks the other
out of EVERY jj command: "Failed to determine the secure config for a repo …
Permission denied". Backend-side that reached users as a 422 on the file panel of
every topic in the project; sandbox-side as jj being unusable in the container.
Both directions actually happened in production.

No umask, shared group, or default ACL can widen a mode the writer sets
explicitly, so the fix is the identity itself: backend and sandbox share
`ws.AGENT_UID`. These tests pin the three places that has to hold — the two
images and the `docker run` — plus the behaviour that broke: files stay readable
after the agent uses jj, and edits pass back and forth between the two sides.
"""

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.workspace import service as ws

BACKEND_DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"
SANDBOX_DOCKERFILE = Path(__file__).resolve().parents[2] / "sandbox" / "Dockerfile"

needs_jj = pytest.mark.skipif(
    shutil.which("jj") is None, reason="jj is not installed in this environment"
)
# root ignores file modes, so the permission-denied half cannot be observed there.
not_root = pytest.mark.skipif(
    os.getuid() == 0, reason="running as root — file modes are not enforced"
)


@pytest.fixture
def project(tmp_path, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    return uuid.uuid4()


def _agent_jj(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """A jj command the way the agent runs it — from inside the topic worktree,
    against the shared store."""
    return subprocess.run(
        ["jj", "--no-pager", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


# --- the invariant itself -------------------------------------------------


def test_backend_image_runs_as_the_agent_uid():
    """The backend image's user is not a free choice — it has to be the uid that
    also owns the sandbox side of the shared store."""
    text = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    adduser = re.search(r"adduser\s+[^\n]*?--uid\s+(\d+)\s+--gid\s+(\d+)", text)
    addgroup = re.search(r"addgroup\s+[^\n]*?--gid\s+(\d+)", text)
    assert adduser, "backend/Dockerfile stopped creating its user with an explicit uid"
    assert addgroup, "backend/Dockerfile stopped pinning its group's gid"
    assert int(adduser.group(1)) == ws.AGENT_UID
    assert int(adduser.group(2)) == ws.AGENT_GID
    assert int(addgroup.group(1)) == ws.AGENT_GID


def test_sandbox_image_pins_node_to_the_agent_uid():
    """`node` comes from the base image rather than from a line we write, so the
    sandbox Dockerfile asserts it at build time instead of assuming it — a base
    image bump that moves the uid must fail the build, not the file panel."""
    text = SANDBOX_DOCKERFILE.read_text(encoding="utf-8")
    assert f'test "$(id -u node)" = {ws.AGENT_UID}' in text
    assert f'test "$(id -g node)" = {ws.AGENT_GID}' in text
    assert text.rstrip().endswith("USER node")


@needs_jj
@pytest.mark.anyio
async def test_sandbox_container_is_started_as_that_user(project, monkeypatch):
    """The run-time half: whatever the image says, the container is launched as
    the aligned user."""
    from app.domain.agent import tmux_provider

    captured: list[tuple[str, ...]] = []

    async def _fake_docker(*args: str, stdin: bytes | None = None):
        captured.append(args)
        return 0, "", ""

    monkeypatch.setattr(tmux_provider, "_docker", _fake_docker)
    provider = tmux_provider.TmuxHooksProvider(image="cheesex-agent-sandbox:test")
    topic = uuid.uuid4()
    # The argv it builds IS the behaviour under test.
    await provider._create_container(  # noqa: SLF001
        "cheesex-tmux-test",
        {
            "CHEESE_PROJECT": str(project),
            "CHEESE_TOPIC": str(topic),
            "SBX_WORKTREE": str(ws.topic_worktree(project, topic)),
            "SBX_SESSION": str(ws.session_dir(project, topic)),
        },
    )
    args = captured[-1]
    assert "--user" in args
    assert args[args.index("--user") + 1] == "node"


# --- the behaviour that broke ---------------------------------------------


@needs_jj
def test_files_stay_readable_after_the_agent_runs_jj(project):
    """The regression: the agent using jj in its worktree writes `config-id` into
    the SHARED store, and every later backend read goes through jj
    (`_tree` → `_catch_up_with_branch` → `jj diff`). One uid → still readable."""
    topic = uuid.uuid4()
    wt = ws.topic_worktree(project, topic)
    (wt / "note.md").write_text("hello\n", encoding="utf-8")

    _agent_jj(wt, "config", "set", "--repo", "user.name", "芝士")
    _agent_jj(wt, "status")

    config_id = ws._repo(project) / ".jj" / "repo" / "config-id"  # noqa: SLF001
    assert config_id.is_file(), "jj no longer writes config-id — re-check the store"
    assert config_id.stat().st_uid == os.getuid(), (
        "the shared store is owned by another uid — every jj call will now fail"
    )

    assert "note.md" in [f["path"] for f in ws.list_files(project, topic_id=topic)]
    assert ws.read_file(project, "note.md", topic_id=topic) == "hello\n"
    assert ws.read_file_bytes(project, "note.md", topic_id=topic) == b"hello\n"


@needs_jj
@not_root
def test_unreadable_store_names_the_uid_split_instead_of_dumping_jj_output(project):
    """When it does go wrong, the file panel must say why. This used to surface
    as `jj diff failed: Internal error…`, which reads like "file not found" and
    is why a project-wide outage went undiagnosed.

    `config-id` is deliberately NOT the probe here: `_jj` now deletes that one
    before every call (its own remedy, added upstream), so it can no longer
    reach a user. Any OTHER unreadable file in the shared store still can, and
    that is the shape a uid split takes once config-id is handled."""
    topic = uuid.uuid4()
    wt = ws.topic_worktree(project, topic)
    (wt / "note.md").write_text("hello\n", encoding="utf-8")

    op_store = ws._jj_store(ws._repo(project)) / "op_store"  # noqa: SLF001
    op_store.chmod(0o000)  # what another uid's 0600 looks like from this process
    try:
        with pytest.raises(ws.WorkspacePermissionError) as excinfo:
            ws.list_files(project, topic_id=topic)
    finally:
        op_store.chmod(0o755)

    message = str(excinfo.value)
    assert str(ws.AGENT_UID) in message
    assert str(os.getuid()) in message
    assert excinfo.value.code == 422


@needs_jj
def test_human_can_save_a_file_the_agent_just_created(project):
    """人改文件即指令, first half: 芝士 creates a file with its native tools (0644,
    its own uid), the human saves over it from the file panel. Under a uid split
    that write was an uncaught EACCES — a 500 on save."""
    topic = uuid.uuid4()
    wt = ws.topic_worktree(project, topic)
    created = wt / "docs" / "agent.md"
    created.parent.mkdir(parents=True, exist_ok=True)
    created.write_text("from 芝士\n", encoding="utf-8")
    created.chmod(0o644)  # exactly what Write/Edit leaves behind under umask 022

    ws.write_file(project, "docs/agent.md", "edited by 人\n", topic_id=topic)

    assert created.read_text(encoding="utf-8") == "edited by 人\n"


@needs_jj
def test_agent_can_modify_a_file_the_human_saved(project):
    """人改文件即指令, second half: the file the human saved must still be the
    agent's to edit on its next turn."""
    topic = uuid.uuid4()
    ws.write_file(project, "docs/human.md", "from 人\n", topic_id=topic)
    target = ws.topic_worktree(project, topic) / "docs" / "human.md"

    assert target.stat().st_uid == os.getuid(), (
        "the human's save left a file the sandbox uid cannot own"
    )
    with target.open("a", encoding="utf-8") as handle:  # 芝士's native Edit
        handle.write("appended by 芝士\n")

    assert "appended by 芝士" in ws.read_file(project, "docs/human.md", topic_id=topic)


@needs_jj
@not_root
def test_unwritable_file_is_a_clean_422_not_a_500(project):
    """A save that genuinely cannot proceed must still be an error the panel can
    show, not an unhandled OSError."""
    topic = uuid.uuid4()
    ws.write_file(project, "locked.md", "v1\n", topic_id=topic)
    target = ws.topic_worktree(project, topic) / "locked.md"
    target.chmod(0o444)
    try:
        with pytest.raises(ws.WorkspacePermissionError) as excinfo:
            ws.write_file(project, "locked.md", "v2\n", topic_id=topic)
        with pytest.raises(ws.WorkspacePermissionError):
            ws.write_file_bytes(project, "locked.md", b"v2\n", topic_id=topic)
    finally:
        target.chmod(0o644)
    assert excinfo.value.code == 422
    assert "locked.md" in str(excinfo.value)


# --- the boot-time audit ---------------------------------------------------


@needs_jj
def test_ownership_audit_is_quiet_on_a_healthy_workspace(project):
    topic = uuid.uuid4()
    ws.topic_worktree(project, topic)
    assert ws.audit_workspace_ownership() == []


@needs_jj
@not_root
def test_ownership_audit_names_a_store_this_process_cannot_read(project):
    topic = uuid.uuid4()
    wt = ws.topic_worktree(project, topic)
    _agent_jj(wt, "config", "set", "--repo", "user.name", "芝士")
    config_id = ws._repo(project) / ".jj" / "repo" / "config-id"  # noqa: SLF001
    config_id.chmod(0o000)
    try:
        problems = ws.audit_workspace_ownership()
    finally:
        config_id.chmod(0o600)
    assert len(problems) == 1
    assert "config-id" in problems[0]
