"""工作区目录名与 git 分支名是两件事，改一个不许动到另一个。

**为什么值得钉**：以前它们是一条链——`branch_for_topic(topic_id)` 给出
`topic/<hex8>`，`_worktree_path` 把 `/` 折成 `_` 得到目录名，容器工作目录再由
目录名拼出来，而设备端的 tmux 会话名是**按工作目录算的 cksum**
（`agent/device_launch.py`：“retires a stale session whenever the resolved work
dir changes”）。于是给分支改个名，磁盘上的工作区要搬家、容器工作目录跟着变、
正在跑的 claude 会话被退休——而设备端启动时没带 `--resume`，等于冷启动、对话
上下文全丢。没有任何地方写着这件事。

而且这条链是**偶然**的：`_worktree_path` 拿到的分支名永远是
`branch_for_topic(topic_id)`，`topic/<hex8>` → `topic_<hex8>` 本来就是话题 id
的纯函数。所以目录名改成直接由 topic_id 派生之后，磁盘上的结果一字不差，零迁移。

下面两组测试就是那条链被剪断后的替代品：
- 第一组：路径的字面值。期望值是**旧算法写死的产物**，不是重新调用一遍新算法
  ——否则算法怎么改它都绿。磁盘上已经存在的 `topic_<hex8>` 目录靠这组不被搬走。
- 第二组：把 `branch_for_topic` 换掉，路径必须纹丝不动。这一条才是这次改动的
  全部意义所在。
"""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.workspace import service as ws

# 固定话题 id → 旧算法（branch_for_topic(topic).replace("/", "_")）当时产出的
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
    """这次改动的全部意义：`branch_for_topic` 返回别的值，路径也不许变。

    改动之前这条断言必然失败——目录名就是分支名折出来的。
    """
    project, topic = uuid.uuid4(), uuid.uuid4()
    before_path = ws._worktree_path(project, topic)  # noqa: SLF001
    before_workdir = ws.sandbox_topic_workdir(topic)

    monkeypatch.setattr(
        ws, "branch_for_topic", lambda tid: f"fix/some-new-scheme-{tid.hex[:8]}"
    )

    assert ws._worktree_path(project, topic) == before_path  # noqa: SLF001
    assert ws.sandbox_topic_workdir(topic) == before_workdir
    assert before_path.name.startswith("topic_")


def test_renaming_the_branch_does_not_move_a_real_workspace(
    workspace_root: Path, monkeypatch
):
    """同一件事，但走到真的 jj workspace 和真的 docker 挂载参数上。

    `sandbox_vcs_mounts` 依赖的 `.jj/repo` 指针是**按主仓和工作区之间的目录层级**
    算出来的相对路径（`../../../../<project_id>/.jj/repo`）——路径的名字或深度一变，
    容器里的 jj 就走丢。所以这里比的不只是字符串，是那套挂载还指得到同一个地方。
    """
    if shutil.which("jj") is None:  # pragma: no cover - 环境缺件，不是代码缺陷
        pytest.skip("jj 未安装")
    project, topic = uuid.uuid4(), uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001
    workdir = ws.sandbox_topic_workdir(topic)
    before = ws.sandbox_project_mounts(project, topic)

    monkeypatch.setattr(
        ws, "branch_for_topic", lambda tid: f"feat/renamed-{tid.hex[:8]}"
    )

    # 目录还在原地（没被搬走、没被重建），挂载参数逐字相同。
    assert ws._ensure_worktree(project, topic) == wt  # noqa: SLF001
    assert ws.sandbox_project_mounts(project, topic) == before
    assert ws.sandbox_topic_workdir(topic) == workdir

    # 并且这套挂载确实还落在指针解析出来的位置上——层级没被悄悄改深或改浅。
    pointer = (wt / ".jj" / "repo").read_text()
    store = Path(os.path.normpath(os.path.join(workdir, ".jj", pointer)))
    assert f"{ws._repo(project) / '.jj'}:{store.parents[1] / '.jj'}" in before  # noqa: SLF001


def test_the_git_branch_is_still_named_after_the_branch_function(
    workspace_root: Path, monkeypatch
):
    """反过来的一半：解耦不等于工作区不再导出分支。

    `_ensure_worktree` 建的 bookmark 仍然必须是 `branch_for_topic` 说的那个名字
    ——采纳/diff 全走 git 分支，那条线不能跟着目录名一起被剪断。
    """
    if shutil.which("jj") is None:  # pragma: no cover - 环境缺件，不是代码缺陷
        pytest.skip("jj 未安装")
    project, topic = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        ws, "branch_for_topic", lambda tid: f"feat/renamed-{tid.hex[:8]}"
    )

    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    listed = subprocess.run(
        ["jj", "--no-pager", "bookmark", "list"],
        cwd=wt,
        capture_output=True,
        text=True,
    ).stdout
    assert f"feat/renamed-{topic.hex[:8]}" in listed
    assert wt.name == f"topic_{topic.hex[:8]}"  # 目录名没跟着走
