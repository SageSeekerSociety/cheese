"""Persisted workspace names survive branch renaming and historical migration."""

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.workspace import service as ws

# 固定话题 id → 旧算法（branch_for_task(topic).replace("/", "_")）当时产出的
# 目录名，写死。任何让这一列变动的改动都会搬走线上已经存在的工作区。
FROZEN = [
    ("733747a3-0000-4000-8000-000000000001", "topic_733747a3"),
    ("bdf6626e-d3be-400a-b352-ac598b91959b", "topic_bdf6626e"),
    ("00000000-0000-4000-8000-000000000000", "topic_00000000"),
    ("ffffffff-ffff-4fff-bfff-ffffffffffff", "topic_ffffffff"),
]


@pytest.fixture
def workspace_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    monkeypatch.setattr(settings, "workspace_root", str(root))
    return root


@pytest.mark.parametrize(("topic", "dirname"), FROZEN)
def test_the_worktree_path_is_byte_for_byte_what_it_always_was(
    topic: str, dirname: str, workspace_root: Path
):
    project = uuid.uuid4()

    ws.bind_task(
        uuid.UUID(topic),
        branch=f"topic/{uuid.UUID(topic).hex[:8]}",
        directory=dirname,
        base="main",
    )
    got = ws._worktree_path(project, uuid.UUID(topic))  # noqa: SLF001

    assert got == (workspace_root / ".worktrees" / str(project) / dirname).resolve()


@pytest.mark.parametrize(("topic", "dirname"), FROZEN)
def test_the_container_workdir_is_byte_for_byte_what_it_always_was(
    topic: str, dirname: str
):
    assert ws.sandbox_topic_workdir(uuid.UUID(topic)) == (
        f"{ws.SANDBOX_TOPICS_ROOT}/{dirname}"
    )


def test_renaming_the_branch_does_not_move_anything_on_disk(
    workspace_root: Path, monkeypatch
):
    """这次改动的全部意义：`branch_for_task` 返回别的值，路径也不许变。

    改动之前这条断言必然失败——目录名就是分支名折出来的。
    """
    project, topic = uuid.uuid4(), uuid.uuid4()
    ws.bind_task(
        topic,
        branch=f"task/{topic.hex[:8]}",
        directory=f"topic_{topic.hex[:8]}",
        base="main",
    )
    before_path = ws._worktree_path(project, topic)  # noqa: SLF001
    before_workdir = ws.sandbox_topic_workdir(topic)

    monkeypatch.setattr(
        ws, "branch_for_task", lambda tid: f"fix/some-new-scheme-{tid.hex[:8]}"
    )

    assert ws._worktree_path(project, topic) == before_path  # noqa: SLF001
    assert ws.sandbox_topic_workdir(topic) == before_workdir
    assert before_path.name.startswith("topic_")


def test_renaming_the_branch_does_not_move_a_real_workspace(
    workspace_root: Path, monkeypatch
):
    """同一件事，但走到真的工作区和真的 docker 挂载参数上。

    `sandbox_vcs_mounts` 依赖的 `.git` 指针是**按主仓和工作区之间的目录层级**
    算出来的相对路径（`../../../<project_id>/.git/worktrees/<目录名>`）——路径的
    名字或深度一变，容器里的 git 就走丢。所以这里比的不只是字符串，是那套挂载
    还指得到同一个地方。
    """
    project, topic = uuid.uuid4(), uuid.uuid4()
    ws.bind_task(
        topic,
        branch=f"task/{topic.hex[:8]}",
        directory=f"topic_{topic.hex[:8]}",
        base="main",
    )
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001
    workdir = ws.sandbox_topic_workdir(topic)
    before = ws.sandbox_project_mounts(project, topic)

    monkeypatch.setattr(
        ws, "branch_for_task", lambda tid: f"feat/renamed-{tid.hex[:8]}"
    )

    # 目录还在原地（没被搬走、没被重建），挂载参数逐字相同。
    assert ws._ensure_worktree(project, topic) == wt  # noqa: SLF001
    assert ws.sandbox_project_mounts(project, topic) == before
    assert ws.sandbox_topic_workdir(topic) == workdir

    # 并且这套挂载确实还落在指针解析出来的位置上——层级没被悄悄改深或改浅。
    pointer = (wt / ".git").read_text().split(":", 1)[1].strip()
    admin = Path(os.path.normpath(os.path.join(workdir, pointer)))
    assert f"{ws._repo(project) / '.git'}:{admin.parents[1]}" in before  # noqa: SLF001


def test_the_git_branch_is_still_named_after_the_branch_function(
    workspace_root: Path, monkeypatch
):
    """反过来的一半：解耦不等于工作区不再检出分支。

    工作区检出的仍然必须是 `branch_for_task` 说的那个分支——采纳/diff 全走 git
    分支，那条线不能跟着目录名一起被剪断。
    """
    project, topic = uuid.uuid4(), uuid.uuid4()
    ws.bind_task(
        topic,
        branch=f"task/{topic.hex[:8]}",
        directory=f"topic_{topic.hex[:8]}",
        base="main",
    )
    monkeypatch.setattr(
        ws, "branch_for_task", lambda tid: f"feat/renamed-{tid.hex[:8]}"
    )

    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    on = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=wt,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert on == f"feat/renamed-{topic.hex[:8]}"
    assert wt.name == f"topic_{topic.hex[:8]}"  # 目录名没跟着走
