"""轮询替没人看着的卡看一眼，它发现的事得有路到人手上。

一张骑着 PR 的卡，上一次有人看它可能是几个小时前。CI 什么时候绿的、PR 什么时候被
人关掉的、谁又推了一个提交把已有的批准票作废的 —— 这些全都发生在没有人打开这个房
间的时候，而房间里留一行只对已经回来的人成立，「回来」恰恰是这里要触发的那件事。

所以轮询发现的、要人动手的那几件事，在房间里留一行的同时通知这张卡的验收人。对照
面一样要钉住：点采纳当场停下的那些不通知 —— 点的人正读着自己那次请求的回应；派给
芝士的那些也不通知 —— 下一步不在人手上。

GitHub 全程是 test double，复用 `test_accept_pr` 那套世界（平台 GitHub App 开 PR，
scheduler 轮询推进），也就是生产上真正跑着的那条路。
"""

import pytest

from tests.conftest import seed_user, wait_work_idle
from tests.integration.test_accept_pr import (
    _accept,
    _approve,
    _poll,
    _protect,
    _ready_card,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)

#: 见 `test_nudge_pr_signals` 里同一处包装：直接 import 那个 fixture 会让它在本模
#: 块里既是导入名又是 fixture 名，读起来像一次意外的覆盖。
app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _events(client, topic_id: str, event_type: str) -> list[dict]:
    """房间里这一类平台提示的全部事件行。

    读 `meta.event_type` 而不是文案：文案随时会改，类别码是契约。
    """
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [
        b
        for b in blocks
        if b["kind"] == "event"
        and (b.get("meta") or {}).get("event_type") == event_type
    ]


def _notices(client, token: str, event_type: str | None = None) -> list[dict]:
    r = client.get(
        "/notifications",
        params={"type": "ROOM_NOTICE"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["notifications"]
    if event_type is None:
        return rows
    return [
        n
        for n in rows
        if (n.get("contextMetadata") or {}).get("eventType") == event_type
    ]


def _same_words(notice_row: dict, room_line: dict) -> bool:
    """通知里读到的和回房间看到的是同一句 —— 同一件事不许有两种说法。"""
    return notice_row["contextMetadata"]["content"] == room_line["content"]


def test_the_reviewer_hears_when_the_checks_go_green(client, app_world):
    alice = seed_user(client, "alice")
    fake = app_world["fake"]
    _pid, tid, _cid, _number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    _poll(client)
    wait_work_idle()

    (line,) = _events(client, tid, "accept_ready")
    assert "可以合并了" in line["content"]
    (row,) = _notices(client, alice, "accept_ready")
    assert _same_words(row, line)
    assert row["contextMetadata"]["topicId"] == tid
    assert row["read"] is False


def test_a_second_poll_on_the_same_commit_does_not_ring_again(client, app_world):
    """去重的账记在卡上（`nudge_state`），按 head 走。

    轮询每 60 秒一拍，一个 head 上「可以采纳了」只成立一次；重复提醒是人关掉通知
    的头号原因。
    """
    alice = seed_user(client, "alice")
    fake = app_world["fake"]
    _pid, _tid, _cid, _number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    for _ in range(3):
        _poll(client)
    wait_work_idle()

    assert len(_notices(client, alice, "accept_ready")) == 1


def test_the_reviewer_hears_when_the_pr_is_closed_without_merging(client, app_world):
    """有人在 GitHub 上把 PR 关了 —— 平台不会替他合，也不会自己收尾。

    这件事完全发生在平台之外，房间里不会有任何别的痕迹。
    """
    alice = seed_user(client, "alice")
    fake = app_world["fake"]
    _pid, tid, _cid, number, _head_sha = _ready_card(client, app_world)
    fake.close_unmerged(number)

    _poll(client)
    wait_work_idle()

    (line,) = _events(client, tid, "pr_closed")
    (row,) = _notices(client, alice, "pr_closed")
    assert _same_words(row, line)


def test_a_new_commit_voids_the_approvals_and_both_the_reviewer_and_the_voter_hear(
    client, app_world
):
    """又推了一个提交，旧 head 挣到的票全作废，得重新看一遍。

    批准是对**某一版**的批准。作废这件事由轮询发现，而原来投票的人此刻在别处 ——
    重新投一次只有他能做，而房间里那一行已经点了他的名字。
    """
    alice = seed_user(client, "alice")
    bob = seed_user(client, "bob")
    fake = app_world["fake"]
    _pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)
    _approve(client, cid, "bob")

    fake.push_new_commit(number)
    _poll(client)
    wait_work_idle()

    (line,) = _events(client, tid, "accept_dismissed")
    assert "bob" in line["meta"]["detail"]  # 点了名
    for token in (alice, bob):  # 就得送到
        (row,) = _notices(client, token, "accept_dismissed")
        assert _same_words(row, line)


def test_a_red_check_dispatches_cheese_and_rings_nobody(client, app_world):
    """检查红了是派芝士去修 —— 下一步在它手上。

    房间里那一行照样有，但谁的铃都不该响：一轮里芝士要红几次、修几次，按这个发通
    知，人会直接关掉这个渠道，之后真正要他动手的那条也就收不到了。
    """
    alice = seed_user(client, "alice")
    fake = app_world["fake"]
    _pid, tid, _cid, _number, head_sha = _ready_card(client, app_world)
    before = len(_notices(client, alice))
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

    _poll(client)
    wait_work_idle()

    assert _events(client, tid, "ci_failed")  # 房间里说了
    assert len(_notices(client, alice)) == before  # 铃没响


def test_an_accept_that_stops_in_front_of_the_person_rings_nobody(client, app_world):
    """点采纳当场被拦下 —— 拦下的理由写在那次请求的回应里。

    那个人正读着它。再投一条通知给他，说的是他刚刚已经读到的那句话。
    """
    alice = seed_user(client, "alice")
    fake = app_world["fake"]
    pid, _tid, cid, _number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test"}])
    fake.check_state_by_sha[head_sha] = ("failure", "pytest: 3 failed")
    before = len(_notices(client, alice))

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "test" in r.json()["message"]  # 理由当场就到了他眼前
    wait_work_idle()

    assert len(_notices(client, alice)) == before
