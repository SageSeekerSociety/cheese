"""A sandbox must be able to READ the jj metadata the backend writes.

jj (0.42) creates its metadata with a hardcoded 0600 — it ignores umask, so no
host-side umask or default ACL can prevent it. The sandbox container runs as a
different uid sharing no group with the backend, so every such file is opaque
to it and `jj log` inside a sandbox dies with "Permission denied" on the newest
operation.

The worktree is made accessible once, when it is created; these tests cover
what LATER jj calls write, which is where the failure was actually observed.

Mode bits stand in for the cross-uid check: tests do not run as another user,
and "other" is exactly the class the sandbox's uid falls into here.
"""

import os
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.workspace import service as ws


@pytest.fixture
def project(tmp_path, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    return uuid.uuid4()


def _unreadable_by_others(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        if not d.stat().st_mode & 0o005:  # needs r+x to be traversable
            out.append(d)
        out.extend(
            p for name in filenames if not (p := d / name).stat().st_mode & 0o004
        )
    return out


def _both_jj_trees(wt: Path) -> list[Path]:
    """A workspace's own `.jj` plus the shared store its pointer resolves to —
    a workspace's operations land in the MAIN repo's op_store, not its own."""
    pointer = (wt / ".jj" / "repo").read_text().strip()
    return [wt / ".jj", Path(os.path.normpath(wt / ".jj" / pointer))]


def test_metadata_written_after_creation_stays_readable(project):
    """The reported bug: a worktree is readable when created, then the next jj
    call writes a 0600 operation and the sandbox can no longer read history."""
    branch = ws.branch_for_topic(uuid.uuid4())
    wt = ws._ensure_worktree(project, branch)  # noqa: SLF001

    (wt / "hello.txt").write_text("hi\n", encoding="utf-8")
    ws._jj(wt, "commit", "-m", "later work")  # noqa: SLF001

    for tree in _both_jj_trees(wt):
        assert _unreadable_by_others(tree) == []


def test_the_newest_operation_is_readable(project):
    """Narrowest form of the failure: jj reads the op log head on every command,
    so ONE unreadable operation file breaks every jj call in the sandbox."""
    branch = ws.branch_for_topic(uuid.uuid4())
    wt = ws._ensure_worktree(project, branch)  # noqa: SLF001
    (wt / "a.txt").write_text("a\n", encoding="utf-8")
    ws._jj(wt, "commit", "-m", "work")  # noqa: SLF001

    store = _both_jj_trees(wt)[1]
    ops = sorted(
        (store / "op_store" / "operations").iterdir(), key=lambda p: p.stat().st_mtime
    )
    assert ops, "expected jj to have written operations"
    assert ops[-1].stat().st_mode & 0o004


def test_a_failing_jj_call_still_repairs_modes(project):
    """A failed call writes operations too, and leaves the sandbox just as
    broken — so the repair must not sit behind the returncode check."""
    branch = ws.branch_for_topic(uuid.uuid4())
    wt = ws._ensure_worktree(project, branch)  # noqa: SLF001
    victim = wt / ".jj" / "working_copy" / "checkout"
    victim.chmod(0o600)

    with pytest.raises(ValidationError):
        ws._jj(wt, "log", "-r", "no-such-bookmark-xyz")  # noqa: SLF001

    assert victim.stat().st_mode & 0o004


def test_the_store_root_is_writable_for_jjs_secure_config(project):
    """jj writes a temp file into the store root to resolve its "secure config"
    before running ANY command — read-only there fails with "Failed to
    determine the secure config for a repo" even for `jj log`."""
    branch = ws.branch_for_topic(uuid.uuid4())
    wt = ws._ensure_worktree(project, branch)  # noqa: SLF001
    ws._jj(wt, "status")  # noqa: SLF001

    assert _both_jj_trees(wt)[1].stat().st_mode & 0o002


def test_mounting_for_a_sandbox_repairs_metadata_written_earlier(project):
    """A repo whose metadata predates this behaviour — every repo already on
    disk — is never covered by the per-call repair, which only fixes what THAT
    call wrote. Handing the store to a container is the moment it must be
    right."""
    branch = ws.branch_for_topic(uuid.uuid4())
    wt = ws._ensure_worktree(project, branch)  # noqa: SLF001
    victim = wt / ".jj" / "working_copy" / "checkout"
    victim.chmod(0o600)  # stands in for pre-existing 0600 metadata

    ws.sandbox_vcs_mounts(project, branch, container_workdir="/work")

    assert victim.stat().st_mode & 0o004


def test_op_log_writes_stay_denied(project):
    """Read access must not become write access: a sandbox reads history with
    --ignore-working-copy. If one topic's agent could write the shared op log,
    it could corrupt every other topic's history."""
    branch = ws.branch_for_topic(uuid.uuid4())
    wt = ws._ensure_worktree(project, branch)  # noqa: SLF001
    ws._jj(wt, "status")  # noqa: SLF001

    ops = _both_jj_trees(wt)[1] / "op_store" / "operations"
    assert not ops.stat().st_mode & 0o002
