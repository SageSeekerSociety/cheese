"""一次没做成的提交，不再被当成「这一轮本来就没活」。

文件和提交之间只有一步：平台把话题工作区里的改动折成一个提交。后面每一个读的人
读的都是提交——房间里的改动摘要数的是新提交，验收卡的 diff 比的是分支，PR 推的
是分支，采纳合的也是分支。所以那一步失败时，磁盘上一个字节都没少，而上面这些地
方一起看不到这一轮——**和「这一轮确实什么都没改」长得一模一样**。

采纳、开 PR、push-fix 三条路以前都把这两件事收成同一个 `except ValidationError:
pass`，注释写着「还没有工作区」。工作区坏掉抛的是同一个异常，于是一条缺了整轮改
动的分支被一路带到合并，全程零报错。每轮末那次自动快照则是 `except Exception` +
一行服务器日志，房间里没有人会看到。

这里把工作区真的弄坏（`jj workspace forget`，和过期同一个抛法），再去问那几条路：
你还敢说没事吗。
"""

import asyncio
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.domain.agent import awaited_tasks, snapshot_notices
from app.domain.workspace import service as ws


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


def _topic_with_work(project_id: uuid.UUID) -> tuple[uuid.UUID, Path]:
    """A topic that has done a turn's worth of work, all of it committed."""
    topic_id = uuid.uuid4()
    wt = ws._ensure_worktree(project_id, topic_id)
    (wt / "app.py").write_text("def load(path):\n    return path\n")
    ws.snapshot_worktree(project_id, topic_id)
    return topic_id, wt


def _break_the_workspace(project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
    """Put the workspace into a state where no snapshot can be taken.

    Forgetting it is one real way for that to happen; a stale working copy is
    the common one. They are the same thing from here: the first command the
    snapshot runs fails, and it fails as a ValidationError."""
    ws._jj(
        ws.ensure_repo(project_id),
        "workspace",
        "forget",
        ws._tree_dirname(ws.tree_for_place(topic_id)),
    )


def test_a_workspace_that_cannot_commit_is_not_a_workspace_with_nothing_to_commit(
    project: uuid.UUID,
) -> None:
    topic_id, wt = _topic_with_work(project)
    (wt / "app.py").write_text("def load(path):\n    return path.strip()\n")
    _break_the_workspace(project, topic_id)

    with pytest.raises(ValidationError):
        ws.commit_pending_work(project, topic_id, "chore: fold")


def test_a_place_that_never_had_a_workspace_is_quietly_nothing_to_fold(
    project: uuid.UUID,
) -> None:
    """The case the old `except: pass` was actually written for — a topic that
    is only a conversation. It must stay silent, and it must not conjure a
    workspace on its way there."""
    topic_id = uuid.uuid4()

    ws.commit_pending_work(project, topic_id, "chore: fold")

    assert not ws.has_worktree(project, topic_id)


def test_accepting_refuses_rather_than_merging_a_branch_missing_this_turn(
    project: uuid.UUID,
) -> None:
    """The expensive one. The reviewer approved what they saw; if the last
    edits could not be committed, the branch is not that, and merging it
    anyway is a lie nobody is told about."""
    topic_id, wt = _topic_with_work(project)
    repo = ws.ensure_repo(project)
    base = _git(repo, "rev-parse", ws._base_branch(repo))
    (wt / "app.py").write_text("def load(path):\n    return path.strip()\n")
    _break_the_workspace(project, topic_id)

    with pytest.raises(ValidationError):
        ws.merge_topic(project, topic_id)

    assert _git(repo, "rev-parse", ws._base_branch(repo)) == base, (
        "主干动了——这正是那条缺了一整轮改动的合并"
    )


def test_a_discussion_topic_still_accepts_as_a_noop(project: uuid.UUID) -> None:
    """The other half of the same contract: refusing must not spread to the
    topics that genuinely have nothing to merge."""
    result = ws.merge_topic(project, uuid.uuid4())

    assert result["merged"] is False
    assert result["noop"] is True


def test_opening_a_pr_refuses_rather_than_publishing_a_head_without_this_turn(
    project: uuid.UUID,
) -> None:
    topic_id, wt = _topic_with_work(project)
    (wt / "app.py").write_text("def load(path):\n    return path.strip()\n")
    _break_the_workspace(project, topic_id)

    with pytest.raises(ValidationError):
        ws.push_topic_branch_for_github_pr(
            project,
            topic_id,
            owner="o",
            repo="r",
            remote_branch="cheese/x",
            token="t",
        )


def test_pushing_to_the_upstream_refuses_the_same_way(
    project: uuid.UUID, tmp_path: Path
) -> None:
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "--bare", str(upstream)], check=True)
    ws.set_upstream(project, str(upstream))
    topic_id, wt = _topic_with_work(project)
    (wt / "app.py").write_text("def load(path):\n    return path.strip()\n")
    _break_the_workspace(project, topic_id)

    with pytest.raises(ValidationError):
        ws.push_topic_branch(project, topic_id, token="t")


# ---- 每轮末那一次：失败不再只是一行服务器日志 --------------------------------


def _capture_the_room(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    told: list[tuple] = []

    async def _fake(topic_id: uuid.UUID, detail: str) -> None:
        told.append((topic_id, detail))

    monkeypatch.setattr(snapshot_notices, "warn_snapshot_failed", _fake)
    return told


def test_the_room_hears_when_a_turn_s_work_could_not_be_committed(
    project: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A turn ends on the event loop, so the notice is scheduled onto it."""
    told = _capture_the_room(monkeypatch)
    topic_id, wt = _topic_with_work(project)
    (wt / "app.py").write_text("def load(path):\n    return path.strip()\n")
    _break_the_workspace(project, topic_id)

    async def a_turn_ends() -> str:
        outcome = awaited_tasks.checkpoint_worktree(project, topic_id)
        await asyncio.sleep(0.05)  # let the scheduled notice run
        return outcome

    outcome = asyncio.run(a_turn_ends())

    assert outcome == "failed"
    assert [t for t, _ in told] == [topic_id]
    assert "topic_" in told[0][1], "房间里那条没带上工具自己的原话"


def test_the_room_hears_it_after_a_background_command_too(
    project: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catch-up snapshot runs the commit in a worker thread, where nothing
    can be scheduled onto the loop. Telling the room must survive that."""
    told = _capture_the_room(monkeypatch)
    topic_id, wt = _topic_with_work(project)
    (wt / "app.py").write_text("def load(path):\n    return path.strip()\n")
    _break_the_workspace(project, topic_id)

    outcome = asyncio.run(
        awaited_tasks.checkpoint_worktree_off_loop(project, topic_id, "chore: after")
    )

    assert outcome == "failed"
    assert [t for t, _ in told] == [topic_id]


def test_a_turn_that_committed_fine_says_nothing(
    project: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    told = _capture_the_room(monkeypatch)
    topic_id, wt = _topic_with_work(project)
    (wt / "app.py").write_text("def load(path):\n    return path.strip()\n")

    async def a_turn_ends() -> str:
        outcome = awaited_tasks.checkpoint_worktree(project, topic_id)
        await asyncio.sleep(0.05)
        return outcome

    assert asyncio.run(a_turn_ends()) == "snapshotted"
    assert told == []


def test_the_notice_keeps_the_tools_own_words() -> None:
    """信息不能丢，只能收起来：the recovery hint for a stale working copy is in
    jj's own sentence, so the notice forwards it instead of paraphrasing."""
    content, meta = snapshot_notices.snapshot_failed_notice(
        "jj diff failed: Error: The working copy is stale. "
        "Hint: Run `jj workspace update-stale` to update it."
    )

    assert len(content) <= 40
    assert "jj workspace update-stale" in meta["detail"]
    assert meta["event_type"] == "snapshot_failed"
    assert meta["severity"] == "error"
