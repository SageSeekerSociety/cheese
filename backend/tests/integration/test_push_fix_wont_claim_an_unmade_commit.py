"""`cheese push-fix` 说「没推」，而不是把一次没做成的提交报成推上去了。

push-fix 的意思是「把此刻磁盘上的东西送上 PR」，所以它第一步就是把工作树折成一
个提交。那一步失败时，返回 `pushed: true` 是**最贵的一种错**：芝士读到的是「我
的修复已经在 PR 上了」，于是回去等 CI——等的却是一个从来没动过的 head。

原先那一步是 `except ValidationError: pass`，注释写着「还没有工作区」，而工作区
坏掉抛的是同一个异常。这条用例把工作区真弄坏（`jj workspace forget`，和过期同一
个抛法），再看这个接口怎么答。
"""

import asyncio
import subprocess
import uuid as _uuid

from app.domain.agent import awaited_tasks, snapshot_notices
from app.domain.workspace import service as ws
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept_pr import (
    _cards_for_topic,
    _make_card,
    _make_project,
    _make_topic,
    _pr_ready,
    _real_git_head,
    _reset_client,
)


def _break_the_workspace(project_id: _uuid.UUID, topic_id: _uuid.UUID) -> None:
    """Leave the workspace unable to take a snapshot. Forgetting it is one real
    way to get there; a stale working copy is the common one, and from the
    snapshot's point of view they are the same — the first jj command it runs
    fails, as a ValidationError."""
    subprocess.run(
        [
            "jj",
            "--repository",
            str(ws.ensure_repo(project_id)),
            "workspace",
            "forget",
            ws._tree_dirname(ws.tree_for_place(topic_id)),
        ],
        capture_output=True,
        check=True,
    )


def test_push_fix_reports_the_failure_instead_of_claiming_a_push(client, monkeypatch):
    fake = _pr_ready(client, monkeypatch, patch_local_head=False)
    push_calls: list[str] = []
    holder: dict = {"pr_number": None}

    def real_head_push(project_id, topic_id, *, owner, repo, remote_branch, token):
        head_sha = _real_git_head(project_id, topic_id)
        push_calls.append(head_sha)
        if holder["pr_number"] is not None:
            fake.prs[holder["pr_number"]]["head_sha"] = head_sha
        return {"head_sha": head_sha, "remote_branch": remote_branch}

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", real_head_push)

    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

        wt = ws.topic_worktree(puid, tuid)
        (wt / "work.txt").write_text("first pass\n")
        ws.snapshot_worktree(puid, tuid)
        first_head = _real_git_head(puid, tuid)

        cid = _make_card(client, tid)
        accepted = client.post(
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).json()["data"]
        assert accepted["status"] == "pr_open"
        holder["pr_number"] = accepted["pr_number"]
        fake.prs[holder["pr_number"]]["head_sha"] = first_head
        assert push_calls == [first_head]

        # 芝士 fixes the red check — and the workspace can no longer commit.
        (wt / "work.txt").write_text("fixed\n")
        _break_the_workspace(puid, tuid)

        answer = client.post(
            f"/topics/{tid}/push-fix", headers=session_auth_headers("alice")
        ).json()["data"]

        assert answer["pushed"] is False, "提交没做成，却报成推上去了"
        assert "没能提交" in answer["reason"], answer["reason"]
        assert push_calls == [first_head], "什么都没提交，却又推了一次"
        assert _cards_for_topic(client, tid)[0]["pr_head_sha"] == first_head
    finally:
        _reset_client()


def test_the_room_is_told_when_a_turn_s_work_never_reached_the_branch(
    client, monkeypatch
):
    """采纳、开 PR、push-fix 都可以拒绝执行；每轮末那一次不行——它不能因为提交
    失败就把一轮判死。所以它改成说出来：话题时间线里落一条真事件，而不是服务器
    日志里一行没人会看的 warning。"""
    monkeypatch.setattr(snapshot_notices, "async_session_factory", client.test_factory)
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

    wt = ws.topic_worktree(puid, tuid)
    (wt / "fix.py").write_text("first pass\n")
    ws.snapshot_worktree(puid, tuid)

    (wt / "fix.py").write_text("the actual fix\n")
    _break_the_workspace(puid, tuid)

    async def a_turn_ends() -> str:
        outcome = awaited_tasks.checkpoint_worktree(puid, tuid)
        await asyncio.sleep(0.2)  # the notice is scheduled onto this loop
        return outcome

    assert asyncio.run(a_turn_ends()) == "failed"

    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    said = [
        b
        for b in blocks
        if (b.get("meta") or {}).get("event_type") == "snapshot_failed"
    ]
    assert said, "房间里一个字都没说"
    detail = said[0]["meta"]["detail"] or ""
    # 工具自己那句话要原样带上——工作区过期时，恢复命令就在里面。
    assert "jj" in detail, detail
    assert ws._tree_dirname(ws.tree_for_place(tuid)) in detail, detail
    assert (wt / "fix.py").read_text() == "the actual fix\n", "文件不该被动过"
