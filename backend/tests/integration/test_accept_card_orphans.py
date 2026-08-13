"""孤儿卡 / note 串台 / 递卡互斥 —— 三条运行时缺陷的功能测试 (2026-08-10).

全部走 HTTP，断言的是**外部可观察的行为**（卡的状态、note、话题里的消息、
通知、轮询器动没动 GitHub），不看源码结构。

- **B 孤儿卡**：话题归档后，`pr_open` 的卡必须停止被轮询推进——修复前
  `poll_open_prs` 只按卡的 status 选行，归档话题上的卡每 60 秒还在用批准人的
  GitHub token 推分支 / 合 PR。这里的判据就是"轮询器有没有再打 GitHub"。
- **A note 串台**：`⚠️ 轮询暂停` 不能再让真正的 CI 失败通知误命中去重；
  而 `⚠️ 平台自动重推失败` 的优先级是**有意**的，必须保留。
- **C 递卡互斥**：交付途中（`pr_open`）或卡在冲突时，不能再递第二张卡。
"""

import asyncio
import uuid

from tests.conftest import wait_turns_idle
from tests.integration.conftest import session_auth_headers

# Reuse the 两阶段采纳 harness instead of rebuilding it — see
# .claude/rules/backend-tests.md.
from tests.integration.test_accept_pr import (
    _pr_ready,
    _reset_client,
)


def _make_project(client) -> str:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]


def _make_topic(client, project_id: str, title: str = "做一个东西") -> str:
    return client.post(
        "/api/topics", json={"project_id": project_id, "title": title}
    ).json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice"):
    return client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": reviewer, "routing_reason": "最懂"},
    )


def _cards(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/accept-card").json()["data"]["data"]


def _topic(client, topic_id: str) -> dict:
    return client.get(f"/api/topics/{topic_id}").json()["data"]


def _poll(client) -> dict:
    r = client.post("/api/admin/scheduler/poll-open-prs")
    assert r.status_code == 200
    return r.json()["data"]


def _archive(client, topic_id: str, by: str = "bob") -> dict:
    r = client.post(f"/api/topics/{topic_id}/archive", json={"by": by})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _accept(client, card_id: str, handle: str = "alice") -> dict:
    r = client.post(
        f"/api/accept-cards/{card_id}/accept",
        json={"decided_by": handle},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _open_pr_card(client, monkeypatch, *, title: str = "做一个东西"):
    """A topic whose card is `pr_open` — a human accepted, the PR is up."""
    fake = _pr_ready(client, monkeypatch)
    pid = _make_project(client)
    tid = _make_topic(client, pid, title)
    cid = _make_card(client, tid).json()["data"]["id"]
    accepted = _accept(client, cid)
    assert accepted["status"] == "pr_open"
    return fake, pid, tid, cid, accepted


# --------------------------------------------------------------------------
# B: 孤儿卡不是"停着"，是"还在动"
# --------------------------------------------------------------------------


def test_archiving_a_topic_stops_the_poller_from_driving_its_pr(client, monkeypatch):
    """核心回归：归档后，轮询器绝不能再拿批准人的 token 去动这张卡的 PR。

    判据是行为，不是状态字段：把 PR 的 CI 设成绿（修复前这会让下一轮轮询直接
    调用 merge_pull_request 把 PR 合进 main），然后归档，再轮询——GitHub 侧
    必须一次调用都没有。
    """
    fake, _pid, tid, _cid, accepted = _open_pr_card(client, monkeypatch)
    try:
        number = accepted["pr_number"]
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全绿")

        _archive(client, tid)

        result = _poll(client)
        assert result["cards_checked"] == 0
        assert result["errors"] == []
        # 修复前这里会有一次真实的合并调用 —— 把已归档话题的 PR 合进 main。
        assert fake.merge_calls == []
    finally:
        _reset_client()


def test_archiving_a_stage_one_card_revokes_it_and_leaves_the_pr_open(
    client, monkeypatch
):
    """第一阶段（PR 未合并）+ 人工归档 = 撤销授权、停止推进，但不替人关 PR。

    产品判断（见 review/archive.py 的 docstring）：归档的人未必是当初授权开 PR
    的人，用别人的 token 去关别人名下的 PR 是把"借来的钥匙"问题又用了一次。
    代价是 PR 会留在 GitHub 上，所以留痕和通知是这条选择的必要配套——这里一并断言。
    """
    fake, pid, tid, _cid, accepted = _open_pr_card(client, monkeypatch)
    try:
        number = accepted["pr_number"]
        _archive(client, tid, by="bob")

        card = _cards(client, tid)[0]
        assert card["status"] == "revoked"
        assert f"#{number}" in card["note"]
        assert "未合并" in card["note"]
        # 授权来源不能被归档动作抹掉。
        assert card["decided_by"] == "alice"

        # 平台没有去动 GitHub 上那个 PR（没关、没合）。
        assert fake.merge_calls == []
        assert fake.prs[number]["head_sha"]  # PR 还在，状态未被平台改写

        # 留痕：话题里有一条系统消息说清 PR 被放手了。
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        assert f"停止推进 PR #{number}" in contents

        # 通知：强提醒发给当初授权的人（alice），而不是归档的人（bob）。
        def _titles(handle: str) -> list[str]:
            notifs = client.get(
                f"/api/projects/{pid}/alerts",
                headers=session_auth_headers(handle),
            ).json()["data"]["data"]
            return [n["title"] for n in notifs]

        assert any(f"PR #{number} 还开着" in x for x in _titles("alice"))
        assert not any(f"PR #{number} 还开着" in x for x in _titles("bob"))
    finally:
        _reset_client()


def test_archiving_a_merged_card_settles_it_as_accepted(client, monkeypatch):
    """第二阶段（PR 已合并、只差部署验证）+ 归档 = 收尾成 accepted。

    说它被"撤销"是假话：人确实点过采纳，代码确实进了 main。归档只是把"等部署"
    这一步截断了，note 里必须讲清这点。
    """
    fake, _pid, tid, _cid, accepted = _open_pr_card(client, monkeypatch)
    try:
        number = accepted["pr_number"]
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全绿")
        fake.merge_sha_by_number[number] = "merge-sha-1"
        _poll(client)  # CI 绿 → 合并，卡进第二阶段（等部署）
        assert _cards(client, tid)[0]["pr_merged_at"] is not None
        assert _topic(client, tid)["status"] == "active"

        _archive(client, tid, by="bob")

        card = _cards(client, tid)[0]
        assert card["status"] == "accepted"
        assert "已合并" in card["note"]
        assert "部署结果不再跟踪" in card["note"]

        # 部署 workflow 转绿也不会再触发任何后续动作（卡已终结）。
        fake.workflow_state_by_sha["merge-sha-1"] = ("success", "deployed")
        assert _poll(client)["cards_checked"] == 0
    finally:
        _reset_client()


def test_archiving_revokes_a_pending_card(client):
    """最常见的一种：卡还等着人点，话题先被归档了。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid)
    assert _cards(client, tid)[0]["status"] == "pending"

    _archive(client, tid, by="bob")

    card = _cards(client, tid)[0]
    assert card["status"] == "revoked"
    assert "话题归档" in card["note"]
    assert card["decided_by"] == "bob"


def test_cascade_archive_also_closes_a_subtopic_card(client):
    """归档是级联的（父话题带走子话题），卡的收敛必须跟着一起级联。"""
    pid = _make_project(client)
    parent = _make_topic(client, pid, "父")
    child = client.post(
        "/api/topics", json={"project_id": pid, "title": "子", "parent_id": parent}
    ).json()["data"]["id"]
    _make_card(client, child)
    assert _cards(client, child)[0]["status"] == "pending"

    _archive(client, parent, by="bob")

    assert _topic(client, child)["status"] == "archived"
    assert _cards(client, child)[0]["status"] == "revoked"


def test_archiving_does_not_touch_already_settled_cards(client):
    """幂等 + 不越权：终态的卡（这里是 rejected）不会被归档改写。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid).json()["data"]["id"]
    r = client.post(
        f"/api/accept-cards/{cid}/reject",
        json={"decided_by": "alice", "note": "先不收"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    _archive(client, tid, by="bob")
    card = _cards(client, tid)[0]
    assert card["status"] == "rejected"
    assert card["note"] == "先不收"

    # 重复归档也不会再改一次。
    _archive(client, tid, by="bob")
    assert _cards(client, tid)[0]["note"] == "先不收"


def test_poller_skips_archived_topics_even_for_a_card_it_never_closed(
    client, monkeypatch
):
    """第二道锁：直接把话题状态改成 archived（绕过归档流程，模拟历史遗留行），
    轮询器仍然不能碰这张卡。"""
    fake, _pid, tid, _cid, accepted = _open_pr_card(client, monkeypatch)
    try:
        number = accepted["pr_number"]
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全绿")

        # 绕过 TopicService.archive，直接改库——正是那 6 张存量卡的形状。
        from app.domain.topic.models import Topic, TopicStatus

        async def _force_archive() -> None:
            async with client.test_factory() as s:
                topic = await s.get(Topic, uuid.UUID(tid))
                assert topic is not None
                topic.status = TopicStatus.archived
                await s.commit()

        asyncio.run(_force_archive())

        assert _poll(client)["cards_checked"] == 0
        assert fake.merge_calls == []
    finally:
        _reset_client()


# --------------------------------------------------------------------------
# A: note 前缀串台吞掉 CI 失败通知
# --------------------------------------------------------------------------


def test_poll_pause_note_does_not_swallow_a_later_ci_failure(client, monkeypatch):
    """A 的核心回归。

    token 失效 → note 变成 `⚠️ 轮询暂停…`；token 恢复后 CI 红了，修复前
    `_nudge_pr_fix` 的 `startswith("⚠️")` 会误命中去重：不发消息、不改 note、
    不留痕，芝士永远不知道要修，而唯一的逃生口（pr_head_sha 变化）又需要先有人
    推新提交——死锁。现在必须正常通知。
    """
    fake = _pr_ready(client, monkeypatch)
    holder: dict = {"reason": None}

    async def fake_token(_session, h, *, provider_id="github_app"):
        if h == "alice" and holder["reason"] is None:
            return "test-token", None
        return None, holder["reason"] or "not_connected"

    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle_with_reason",
        fake_token,
    )
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid).json()["data"]["id"]
        accepted = _accept(client, cid)
        number = accepted["pr_number"]

        # 1) token 失效 → 轮询暂停留在 note 上
        holder["reason"] = "undecryptable"
        _poll(client)
        assert "轮询暂停" in _cards(client, tid)[0]["note"]

        # 2) token 恢复，同一个 commit 的 CI 红了
        holder["reason"] = None
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = (
            "failure",
            "pytest: 7 failed",
        )
        _poll(client)
        wait_turns_idle()

        # 芝士必须被叫到，note 必须换成 CI 失败
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        assert "pytest: 7 failed" in contents
        note = _cards(client, tid)[0]["note"]
        assert "CI 检查未通过" in note
        assert "轮询暂停" not in note
    finally:
        _reset_client()


def test_poll_pause_note_is_cleared_once_the_token_works_again(client, monkeypatch):
    """暂停说明必须自愈：token 一恢复，那句"轮询暂停"就不该继续挂在卡上骗人。"""
    _pr_ready(client, monkeypatch)
    holder: dict = {"reason": None}

    async def fake_token(_session, h, *, provider_id="github_app"):
        if h == "alice" and holder["reason"] is None:
            return "test-token", None
        return None, holder["reason"] or "not_connected"

    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle_with_reason",
        fake_token,
    )
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid).json()["data"]["id"]
        _accept(client, cid)

        holder["reason"] = "expired_no_refresh"
        _poll(client)
        assert "轮询暂停" in _cards(client, tid)[0]["note"]

        holder["reason"] = None  # 重新连了账号
        _poll(client)  # CI 还在跑（fake 默认 pending）
        assert _cards(client, tid)[0]["note"] == ""
        assert _cards(client, tid)[0]["status"] == "pr_open"
    finally:
        _reset_client()


def test_repush_failure_still_outranks_a_ci_failure(client, monkeypatch):
    """有意保留的优先级（docs/topics/诊断信息搬上验收卡.md §优先级说明）：
    重推失败意味着芝士的修复根本没到 GitHub，比"旧 commit 上的陈年 CI 失败"
    更值得展示——A 的修复不能把这条一起改掉。"""
    fake = _pr_ready(client, monkeypatch, patch_local_head=False)
    try:
        pid = _make_project(client)
        tid = _make_topic(client, pid)
        cid = _make_card(client, tid).json()["data"]["id"]
        accepted = _accept(client, cid)
        number = accepted["pr_number"]

        # 本地分支动了 → 平台要重推；让重推失败。
        from app.core.errors import ValidationError
        from app.domain.review.services import AcceptService
        from app.domain.workspace import service as ws

        monkeypatch.setattr(
            AcceptService,
            "_local_topic_branch_head",
            lambda _self, _p, _t: "local-head-moved",
        )
        # 采纳即合并 (#296) 加了「快进不了就不推」的前置判断，靠真的
        # `git merge-base` 判祖先。这里用的是假 sha，判不出祖先关系会走到「分叉」
        # 分支而不是真去推。本用例要测的是**推送失败**那条 note 的优先级，所以
        # 让快进判断放行，push 才会被调用并抛错。
        monkeypatch.setattr(
            AcceptService, "_remote_head_ff_from_local", lambda *_a, **_k: True
        )

        def boom(*_a, **_k):
            # 与生产同一种失败：push 失败抛 ValidationError（见 ws.push_*）。
            raise ValidationError("push rejected by remote")

        monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", boom)

        # 同一轮里 CI 也是红的。
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = (
            "failure",
            "pytest: 2 failed",
        )
        _poll(client)
        wait_turns_idle()

        note = _cards(client, tid)[0]["note"]
        assert note.startswith("⚠️ 平台自动重推失败")
    finally:
        _reset_client()


# --------------------------------------------------------------------------
# C: 递卡互斥漏了 pr_open / conflict
# --------------------------------------------------------------------------


def test_cannot_hand_a_second_card_while_the_first_is_delivering(client, monkeypatch):
    """C 的核心回归：交付途中（PR 在跑）再递一张卡必须被拒。

    修复前两张卡会并存，而前端只认最新那张——旧卡连同它正在跑的 PR 一起从界面
    消失。
    """
    _fake, _pid, tid, _cid, _accepted = _open_pr_card(client, monkeypatch)
    try:
        r = _make_card(client, tid, reviewer="bob")
        assert r.status_code == 422, r.text
        assert "交付中" in r.json()["message"]
        assert len(_cards(client, tid)) == 1
    finally:
        _reset_client()


def test_cannot_hand_a_second_card_while_the_first_is_in_conflict(client, monkeypatch):
    """卡在合并冲突上时同样不许再递——出路是解冲突后重试采纳，不是新卡。"""
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid).json()["data"]["id"]

    monkeypatch.setattr(
        ws,
        "merge_topic",
        lambda *_a, **_k: {"merged": False, "conflicts": ["a.py"], "reason": "冲突"},
    )
    r = client.post(
        f"/api/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert _cards(client, tid)[0]["status"] == "conflict"

    r = _make_card(client, tid, reviewer="bob")
    assert r.status_code == 422, r.text
    assert "冲突" in r.json()["message"]
    assert len(_cards(client, tid)) == 1


def test_a_failed_gate_still_allows_re_handing_a_card(client, monkeypatch):
    """反向保护：闸门红了卡就作废，修完重新递卡是设计好的流程，不能被互斥挡住。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid).json()["data"]["id"]

    # 直接把这张卡结算成 gate_failed（跳过真正跑 check_command）。
    from app.domain.review.models import AcceptCard, AcceptStatus

    async def _fail_gate() -> None:
        async with client.test_factory() as s:
            card = await s.get(AcceptCard, uuid.UUID(cid))
            assert card is not None
            card.status = AcceptStatus.gate_failed
            await s.commit()

    asyncio.run(_fail_gate())

    r = _make_card(client, tid, reviewer="bob")
    assert r.status_code == 200, r.text
    assert len(_cards(client, tid)) == 2
