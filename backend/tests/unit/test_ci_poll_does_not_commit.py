"""轮询不再替你提交: reading a topic's branch head must not move it.

The CI poller used to commit the workspace on every tick before reading the
branch head. Any write at all — a scratch file, a line in the living doc —
therefore became a commit, the commit moved the branch, the moved branch was
re-pushed, and `cancel-in-progress` killed the CI run already in flight. One PR
was measured running the backend suite 17 times, 14 of those cancelled.

Functional, against a real repo AND a real topic workspace: the workspace is
what makes this test able to fail. Without one there would be nothing on disk
for a commit to sweep up, and the old behaviour would pass too.
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from app.domain.review.services import AcceptService
from app.domain.workspace import service as ws
from tests.machine_work import machine_commits


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path))
    project_id = uuid.uuid4()
    ws.ensure_repo(project_id)
    return project_id


def test_reading_the_branch_head_leaves_uncommitted_work_uncommitted(
    project: uuid.UUID,
) -> None:
    topic_id = uuid.uuid4()
    worktree = ws._ensure_worktree(project, topic_id)
    repo = ws.ensure_repo(project)
    branch = ws.branch_for_tree(topic_id)
    before = _git(repo, "rev-parse", branch)

    # Somebody is working: a file changed, but nobody said "this is a fix".
    (worktree / "scratch.md").write_text("half a thought\n")

    head = AcceptService(None)._local_topic_branch_head(project, topic_id)  # type: ignore[arg-type]

    assert head == before, "读一次分支头不应该产生提交"
    assert _git(repo, "rev-parse", branch) == before, "分支头不应该被读操作推动"
    assert (worktree / "scratch.md").exists(), "文件还在，只是没被提交"


def test_the_machine_s_own_push_is_what_moves_the_branch(project: uuid.UUID) -> None:
    """The other half of the contract: the head still moves — by the agent
    committing and pushing. That is what `cheese push-fix` then puts on the PR."""
    topic_id = uuid.uuid4()
    ws._ensure_worktree(project, topic_id)
    repo = ws.ensure_repo(project)
    branch = ws.branch_for_tree(topic_id)
    before = _git(repo, "rev-parse", branch)

    machine_commits(project, topic_id, {"fix.md": "the actual fix\n"})

    assert _git(repo, "rev-parse", branch) != before, (
        "分身自己提交推送之后，分支头必须动"
    )


def test_reading_the_branch_head_is_none_when_the_branch_does_not_exist(
    project: uuid.UUID,
) -> None:
    head = AcceptService(None)._local_topic_branch_head(project, uuid.uuid4())  # type: ignore[arg-type]
    assert head is None
