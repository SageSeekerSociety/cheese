"""孤儿卡 / 轮询暂停 / 递卡互斥 —— 归档与轮询交界处的功能测试。

全部走 HTTP，断言的是**外部可观察的行为**（卡的状态、note、话题里的消息、
轮询器动没动 GitHub），不看源码结构。

- **孤儿卡**：话题归档后，骑着 PR 的卡必须停止被轮询跟进——修复前
  `poll_open_prs` 只按卡的 status 选行，归档话题上的卡每 60 秒还在被拿着
  GitHub 凭据跟进。这里的判据就是"轮询器有没有再打 GitHub"。
- **轮询暂停**：凭据失效时卡面要说清原因，且不能吞掉之后真正的 CI 失败。
- **递卡互斥**：已有未决的卡（等采纳 / 卡在冲突）时，不能再递第二张。
"""

import asyncio
import uuid

from tests.conftest import wait_work_idle
from tests.integration.conftest import room_text, session_auth_headers

# Reuse the #718 App-lane harness instead of rebuilding it — see
# .claude/rules/backend-tests.md.
from tests.integration.test_accept_pr import (
    _cards,
    _make_card_response,
    _poll,
    _ready_card,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)

app_world = _app_world_fixture


def _topic(client, topic_id: str) -> dict:
    return client.get(f"/topics/{topic_id}").json()["data"]


def _archive(client, topic_id: str, by: str = "bob") -> dict:
    r = client.post(f"/topics/{topic_id}/archive", json={"by": by})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _make_project(client) -> str:
    return client.post("/projects", json={"name": "P"}).json()["data"]["id"]


def _make_topic(client, project_id: str, title: str = "做一个东西") -> str:
    return client.post(
        "/topics", json={"project_id": project_id, "title": title}
    ).json()["data"]["id"]


# --------------------------------------------------------------------------
# 孤儿卡不是"停着"，是"还在被跟进"
# --------------------------------------------------------------------------


def test_archiving_a_topic_stops_the_poller_from_touching_its_pr(client, app_world):
    """核心回归：归档后，轮询器绝不能再拿 GitHub 凭据去碰这张卡的 PR。"""
    fake = app_world["fake"]
    _pid, tid, _cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    _archive(client, tid)

    fake.status_calls.clear()
    result = _poll(client)
    assert result["cards_checked"] == 0
    assert result["errors"] == []
    assert fake.status_calls == []  # GitHub 一次都没被打
    assert fake.merge_calls == []


def test_archiving_a_card_riding_an_open_pr_revokes_it_and_leaves_the_pr(
    client, app_world
):
    """骑着未合并 PR 的卡 + 人工归档 = 撤销、停止跟进，但不替人关 PR。

    产品判断（review/archive.py 的 docstring）：PR 开着是惰性的，关掉却可能
    丢掉一段人本来打算手动合并的工作。代价是 PR 会留在 GitHub 上，所以留痕
    是这条选择的必要配套——这里一并断言。
    """
    fake = app_world["fake"]
    _pid, tid, _cid, number, _head = _ready_card(client, app_world)

    _archive(client, tid, by="bob")

    card = _cards(client, tid)[0]
    assert card["status"] == "revoked"
    assert f"#{number}" in card["note"]
    assert "未合并" in card["note"]

    # 平台没有去动 GitHub 上那个 PR（没关、没合）。
    assert fake.merge_calls == []
    assert fake.prs[number]["state"] == "open"

    # 留痕：话题里有一条系统消息说清 PR 被放手了。
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert f"停止跟进 PR #{number}" in room_text(blocks)


def test_archiving_revokes_a_pending_card(client):
    """最常见的一种：卡还等着人点，话题先被归档了。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card_response(client, tid)
    assert _cards(client, tid)[0]["status"] == "pending"

    _archive(client, tid, by="bob")

    card = _cards(client, tid)[0]
    assert card["status"] == "revoked"
    assert "话题归档" in card["note"]
    assert card["decided_by"] == "bob"


def test_cascade_archive_closes_the_work_and_settles_the_card_delivering_it(client):
    """归档是级联的（房间带走里面的活），而收卡必须和它同一趟。"""
    pid = _make_project(client)
    room = _make_topic(client, pid, "房间")
    thread = client.post(f"/topics/{room}/split", json={"title": "一件活"}).json()[
        "data"
    ]["id"]
    _make_card_response(client, room)
    assert _cards(client, room)[0]["status"] == "pending"

    _archive(client, room, by="bob")

    cards = client.get(f"/topics/{room}/tasks").json()["data"]["data"]
    assert [c["status"] for c in cards if c["id"] == thread] == ["closed"]
    assert _cards(client, room)[0]["status"] == "revoked"


def test_archiving_does_not_touch_already_settled_cards(client):
    """幂等 + 不越权：终态的卡（这里是 rejected）不会被归档改写。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card_response(client, tid).json()["data"]["id"]
    r = client.post(
        f"/accept-cards/{cid}/reject",
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
    client, app_world
):
    """第二道锁：直接把话题状态改成 archived（绕过归档流程，模拟历史遗留行），
    轮询器仍然不能碰这张卡。"""
    fake = app_world["fake"]
    _pid, tid, _cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    from app.domain.topic.models import Topic, TopicStatus

    async def _force_archive() -> None:
        async with client.test_factory() as s:
            topic = await s.get(Topic, uuid.UUID(tid))
            assert topic is not None
            topic.status = TopicStatus.archived
            await s.commit()

    asyncio.run(_force_archive())

    fake.status_calls.clear()
    assert _poll(client)["cards_checked"] == 0
    assert fake.status_calls == []


# --------------------------------------------------------------------------
# 轮询暂停：凭据没了要说清，且不吞后面的 CI 失败
# --------------------------------------------------------------------------


def _flaky_app_tokens(monkeypatch):
    """让平台 App 凭据可开关地失效（None = 拿不到）。"""
    from app.domain.agent import github_app
    from tests.integration.test_accept_pr import _FakeTokens

    holder: dict = {"broken": False}

    async def tokens_for_project(_project_id, _session):
        return None if holder["broken"] else _FakeTokens()

    monkeypatch.setattr(github_app, "github_app_tokens_for_project", tokens_for_project)
    return holder


def test_poll_pause_note_does_not_swallow_a_later_ci_failure(
    client, app_world, monkeypatch
):
    """凭据失效 → note 变成「轮询暂停」；恢复后 CI 红了，必须正常通知 —— 修复前
    `startswith("⚠️")` 的去重会把它吞掉，芝士永远不知道要修。"""
    fake = app_world["fake"]
    _pid, tid, _cid, number, head_sha = _ready_card(client, app_world)
    holder = _flaky_app_tokens(monkeypatch)

    holder["broken"] = True
    _poll(client)
    assert "轮询暂停" in _cards(client, tid)[0]["note"]

    holder["broken"] = False
    fake.check_state_by_sha[head_sha] = ("failure", "pytest: 7 failed")
    _poll(client)
    wait_work_idle()

    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert "pytest: 7 failed" in room_text(blocks)
    note = _cards(client, tid)[0]["note"]
    assert "轮询暂停" not in note


def test_poll_pause_note_is_cleared_once_the_credentials_work_again(
    client, app_world, monkeypatch
):
    """暂停说明必须自愈：凭据一恢复，那句"轮询暂停"就不该继续挂在卡上骗人。"""
    _pid, tid, _cid, _number, head_sha = _ready_card(client, app_world)
    holder = _flaky_app_tokens(monkeypatch)

    holder["broken"] = True
    _poll(client)
    assert "轮询暂停" in _cards(client, tid)[0]["note"]

    holder["broken"] = False
    _poll(client)  # CI 还在跑（fake 默认 pending）
    card = _cards(client, tid)[0]
    assert "轮询暂停" not in (card["note"] or "")
    assert card["status"] == "pending"
    # 等待本身不再占 note：卡面状态由合并态镜像说（#718）。
    assert card["merge_state"]["state"] in ("unstable", "clean", "unknown")


# --------------------------------------------------------------------------
# 递卡互斥
# --------------------------------------------------------------------------


def test_cannot_hand_a_second_card_while_one_awaits_accept(client, app_world):
    """已有一张等采纳的卡（骑着 PR）时再递一张必须被拒 —— 一棵树一个 PR。"""
    _pid, tid, _cid, _number, _head = _ready_card(client, app_world)

    r = _make_card_response(client, tid, reviewer="bob")
    assert r.status_code == 422, r.text
    assert "已有待处理的验收卡" in r.json()["message"]
    assert len(_cards(client, tid)) == 1


def test_cannot_hand_a_second_card_while_the_first_is_in_conflict(client, monkeypatch):
    """卡在合并冲突上时同样不许再递——出路是解冲突后重试采纳，不是新卡。"""
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card_response(client, tid).json()["data"]["id"]

    monkeypatch.setattr(
        ws,
        "merge_topic",
        lambda *_a, **_k: {"merged": False, "conflicts": ["a.py"], "reason": "冲突"},
    )
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert _cards(client, tid)[0]["status"] == "conflict"

    r = _make_card_response(client, tid, reviewer="bob")
    assert r.status_code == 422, r.text
    assert "冲突" in r.json()["message"]
    assert len(_cards(client, tid)) == 1


def test_the_conflict_refusal_tells_you_how_to_get_unstuck(client, monkeypatch):
    """被互斥挡住的时候，拒绝语要给出那条走得通的路。

    「解决冲突后重试采纳」对一次还打算继续的采纳是对的；对一次不该继续的采纳
    它就是死路——而读拒绝语的往往是芝士，它会照着那句话继续等一个永远不来的
    结果。所以这条拒绝语得点名「作废」。
    """
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card_response(client, tid).json()["data"]["id"]

    monkeypatch.setattr(
        ws,
        "merge_topic",
        lambda *_a, **_k: {"merged": False, "conflicts": ["a.py"], "reason": "冲突"},
    )
    client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert _cards(client, tid)[0]["status"] == "conflict"

    assert "作废" in _make_card_response(client, tid, reviewer="bob").json()["message"]


def test_a_failed_gate_still_allows_re_handing_a_card(client):
    """反向保护：历史 gate_failed 卡不挡新递卡。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card_response(client, tid).json()["data"]["id"]

    from app.domain.review.models import AcceptCard, AcceptStatus

    async def _fail_gate() -> None:
        async with client.test_factory() as s:
            card = await s.get(AcceptCard, uuid.UUID(cid))
            assert card is not None
            card.status = AcceptStatus.gate_failed
            await s.commit()

    asyncio.run(_fail_gate())

    r = _make_card_response(client, tid, reviewer="bob")
    assert r.status_code == 200, r.text
    assert len(_cards(client, tid)) == 2
