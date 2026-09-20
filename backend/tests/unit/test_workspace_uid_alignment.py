"""The backend and the sandbox agent must run as ONE uid over the shared store.

`ws.sandbox_vcs_mounts` bind-mounts a project's main-repo `.git` into every
sandbox container read-write, and both sides WRITE it: the agent's own
`git commit` in its worktree is what moves a topic branch, while the backend
merges, diffs and pushes out of the same store. git creates object directories
0755 and loose objects 0444, owned by whoever wrote them, so under two uids the
second one can read everything and add nothing — its commit fails on an objects
directory it does not own. Backend-side, being locked out reaches users as a 422
on the file panel of every topic in the project.

`core.sharedRepository` could widen those modes, so this is negotiable in a way
the jj store it replaced never was — but nothing negotiates it today, so the fix
is still the identity itself: backend and sandbox share `ws.AGENT_UID`. These
tests pin the two images that has to hold across, plus the behaviour that broke:
the two sides' files stay usable by each other, and a store this process cannot
enter says so instead of reading like a missing file.
"""

import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.workspace import service as ws
from tests.machine_work import declare_task

BACKEND_DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"
SANDBOX_DOCKERFILE = Path(__file__).resolve().parents[2] / "sandbox" / "Dockerfile"

# root ignores file modes, so the permission-denied half cannot be observed there.
not_root = pytest.mark.skipif(
    os.getuid() == 0, reason="running as root — file modes are not enforced"
)


@pytest.fixture
def project(tmp_path, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    return uuid.uuid4()


def _agent_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """A git command the way the agent runs it — from inside the topic worktree,
    against the shared store."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
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


# --- the behaviour that broke ---------------------------------------------


def test_the_agents_commit_reaches_the_backend_through_the_shared_store(project):
    """One uid means the agent can write the store the backend owns — and that
    write is the whole delivery mechanism now: the commit it makes in its own
    worktree IS the topic branch moving, with nothing in between."""
    topic = uuid.uuid4()
    declare_task(project, topic)
    wt = ws.topic_worktree(project, topic)
    (wt / "note.md").write_text("hello\n", encoding="utf-8")

    _agent_git(wt, "add", "-A")
    _agent_git(
        wt,
        "-c",
        "user.name=芝士",
        "-c",
        "user.email=cheese@zhishi.local",
        "commit",
        "-m",
        "feat: work from the agent",
    )

    assert "note.md" in ws.topic_changed_files(project, topic)
    assert "note.md" in [f["path"] for f in ws.list_files(project, topic_id=topic)]
    assert ws.read_file(project, "note.md", topic_id=topic) == "hello\n"


@not_root
def test_a_store_it_cannot_enter_names_the_uid_split(project):
    """When it does go wrong, the file panel must say why.

    git does not report EACCES here: it validates the gitdir by reading what is
    inside, so a store owned by another uid comes back as `fatal: not a git
    repository`, which reads like the files are simply missing — and that
    reading is why a project-wide outage once went undiagnosed."""
    topic = uuid.uuid4()
    declare_task(project, topic)
    ws.topic_worktree(project, topic)

    store = ws._repo(project) / ".git"  # noqa: SLF001
    store.chmod(0o000)  # what another uid's directory looks like from here
    try:
        with pytest.raises(ws.WorkspacePermissionError) as excinfo:
            ws.topic_diff(project, topic)
    finally:
        store.chmod(0o755)

    message = str(excinfo.value)
    assert str(ws.AGENT_UID) in message
    assert str(os.getuid()) in message
    assert excinfo.value.code == 422


def test_human_can_save_a_file_the_agent_just_created(project):
    """人改文件即指令, first half: 芝士 creates a file with its native tools (0644,
    its own uid), the human saves over it from the file panel. Under a uid split
    that write was an uncaught EACCES — a 500 on save."""
    topic = uuid.uuid4()
    declare_task(project, topic)
    wt = ws.topic_worktree(project, topic)
    created = wt / "docs" / "agent.md"
    created.parent.mkdir(parents=True, exist_ok=True)
    created.write_text("from 芝士\n", encoding="utf-8")
    created.chmod(0o644)  # exactly what Write/Edit leaves behind under umask 022

    ws.write_file(project, "docs/agent.md", "edited by 人\n", topic_id=topic)

    assert created.read_text(encoding="utf-8") == "edited by 人\n"


def test_agent_can_modify_a_file_the_human_saved(project):
    """人改文件即指令, second half: the file the human saved must still be the
    agent's to edit on its next turn."""
    topic = uuid.uuid4()
    declare_task(project, topic)
    ws.write_file(project, "docs/human.md", "from 人\n", topic_id=topic)
    target = ws.topic_worktree(project, topic) / "docs" / "human.md"

    assert target.stat().st_uid == os.getuid(), (
        "the human's save left a file the sandbox uid cannot own"
    )
    with target.open("a", encoding="utf-8") as handle:  # 芝士's native Edit
        handle.write("appended by 芝士\n")

    assert "appended by 芝士" in ws.read_file(project, "docs/human.md", topic_id=topic)


@not_root
def test_unwritable_file_is_a_clean_422_not_a_500(project):
    """A save that genuinely cannot proceed must still be an error the panel can
    show, not an unhandled OSError."""
    topic = uuid.uuid4()
    declare_task(project, topic)
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


def test_ownership_audit_is_quiet_on_a_healthy_workspace(project):
    topic = uuid.uuid4()
    declare_task(project, topic)
    ws.topic_worktree(project, topic)
    assert ws.audit_workspace_ownership() == []


@not_root
def test_ownership_audit_names_a_store_this_process_cannot_use(project):
    topic = uuid.uuid4()
    declare_task(project, topic)
    ws.topic_worktree(project, topic)
    store = ws._repo(project) / ".git"  # noqa: SLF001
    store.chmod(0o000)
    try:
        problems = ws.audit_workspace_ownership()
    finally:
        store.chmod(0o755)
    assert len(problems) == 1
    assert str(store) in problems[0]
