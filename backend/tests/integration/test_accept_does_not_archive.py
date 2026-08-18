"""合并结束的是这次改动，不是这个话题 (#442 decision 1).

采纳过去一口气做三件事：把卡置成 accepted、释放话题的算力、把话题归档。只有第一
件是「合并」这个事实本身；另外两件是搭在它上面的两个不同决定，而且都错了——归档
是人整理列表的动作，容器是话题接着干活要用的东西。

这个文件把拆开之后的四条契约钉住：

1. 合并后话题仍是 ``active``，并带上自动的交付标记（``accepted_by``/``accepted_at``）。
2. 已交付的话题**不能**再递验收卡 —— 它的分支已经在 main 上，再开的 PR 没有新提交
   （GitHub 422 → 平台降级成本地合并 → 卡看着采纳了却什么都没交付）。
3. 采纳不再删容器。
4. 手动归档照旧：仍然归档，归档后仍然不能递卡。

第 2 条是这次改动里唯一**新增**的闸门。它必须存在，因为归档以前兼任「工作面冻结」
的开关，而现在归档不再自动发生了。
"""

import uuid

import pytest

from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _card(
    client,
    topic_id: str,
    reviewer: str = "alice",
    subject: str = "chore(test): file an accept card",
):
    return client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": subject,
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )


def _commit_to_topic_branch(project_id: str, topic_id: str, text: str) -> None:
    """在这个话题的分支上再落一个提交 —— 也就是「房间里又干了一件活」。

    直接写工作区再让平台快照，是这条路径在真实使用里的样子：人和分身都不手动
    commit，改动由平台折成提交。
    """
    import uuid as _uuid

    from app.domain.workspace import service as ws

    pid, tid = _uuid.UUID(project_id), _uuid.UUID(topic_id)
    worktree = ws._ensure_worktree(pid, tid)
    (worktree / "next-task.txt").write_text(text, encoding="utf-8")
    ws.snapshot_worktree(pid, tid)


def _accept(client, card_id: str, by: str = "alice"):
    return client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": by},
        headers=session_auth_headers(by),
    )


def _state(client, topic_id: str) -> dict:
    r = client.get(f"/topics/{topic_id}")
    assert r.status_code == 200
    return r.json()["data"]


def _delivered_topic(client) -> tuple[str, str]:
    """一个已经交付过一次的话题 (project_id, topic_id)。"""
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid).json()["data"]["id"]
    assert _accept(client, cid).status_code == 200
    return pid, tid


def test_merge_leaves_the_topic_active_with_a_delivery_marker(client):
    _pid, tid = _delivered_topic(client)

    topic = _state(client, tid)
    assert topic["status"] == "active"  # 不是 archived
    assert topic["accepted_by"] == "alice"  # 交付标记，自动打上
    assert topic["accepted_at"] is not None


def test_a_delivered_topic_with_nothing_new_cannot_file_a_second_card(client):
    """防空 PR：分支上没有 main 没有的东西，这一张卡交付不了任何改动。

    注意拒绝的**理由**：不是「这个话题交付过了」，而是「这条分支现在没有新提交」。
    两者在这一刻的结论相同，往后就不同了 —— 见下面那条。
    """
    _pid, tid = _delivered_topic(client)

    r = _card(client, tid, reviewer="bob")
    assert r.status_code == 422
    # 拒绝话术要说清出路（读它的是一轮之后就要再试一次的芝士）。
    message = r.json()["message"]
    assert "没有新提交" in message
    assert "先把改动提交到工作区" in message
    # 而且这个拒绝不靠归档 —— 话题还活着。
    assert _state(client, tid)["status"] == "active"


def test_a_delivered_room_can_deliver_again_once_there_are_new_commits(client):
    """一个 task 完成了可以再新开 task —— 这是房间比它承载的活儿长的全部意义。

    这条曾经是不成立的：守卫看的是「历史上有没有一张卡到过 accepted」，于是
    房间交付一次之后就永久冻住，而 #536 让话题在合并后活下来，恰恰是为了让它
    接着干下一件事。守卫现在问的是分支的事实，所以工作区里有了新提交就能再递。
    """
    pid, tid = _delivered_topic(client)

    # 干下一件活：往这个房间的分支上再写一笔。
    _commit_to_topic_branch(pid, tid, "next task")

    r = _card(client, tid, reviewer="bob", subject="feat(x): the next task")
    assert r.status_code == 200, r.text


def test_revoking_the_accept_lets_the_topic_deliver_again(client):
    """撤回采纳＝这次验收不算，于是「已交付」这个冻结也不再成立。"""
    _pid, tid = _delivered_topic(client)
    cid = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]["id"]

    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert _state(client, tid)["accepted_at"] is None

    assert _card(client, tid, reviewer="bob").status_code == 200


def test_revoking_does_not_undo_a_persons_archive(client):
    """两个动作互不覆盖：取消归档不改写采纳记录（TopicService.unarchive），
    反过来撤回采纳也不改写归档状态 —— 那是人的决定。"""
    _pid, tid = _delivered_topic(client)
    cid = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]["id"]

    assert (
        client.post(f"/topics/{tid}/archive", json={"by": "alice"}).status_code == 200
    )
    assert _state(client, tid)["status"] == "archived"

    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    # 交付标记清了，但话题还是人放下的那个状态。
    topic = _state(client, tid)
    assert topic["accepted_by"] is None
    assert topic["status"] == "archived"


def test_merge_does_not_stop_the_container(client, monkeypatch):
    """采纳不再删容器：话题接着干活要用它，而闲置回收器管它的死活。"""
    from app.domain.workspace import service as ws

    stopped: list[uuid.UUID] = []
    monkeypatch.setattr(ws, "stop_topic_container", stopped.append)

    _pid, tid = _delivered_topic(client)

    assert stopped == []
    assert _state(client, tid)["status"] == "active"


def test_manual_archive_still_archives_and_still_freezes_new_cards(client):
    """归档这条路一点没变，它只是不再由合并触发。"""
    pid = _project(client)
    tid = _topic(client, pid)

    r = client.post(f"/topics/{tid}/archive", json={"by": "alice"})
    assert r.status_code == 200
    topic = _state(client, tid)
    assert topic["status"] == "archived"
    assert topic["archived_at"] is not None
    # 归档过的话题没交付过 —— 两个标记互相独立。
    assert topic["accepted_at"] is None

    r = _card(client, tid)
    assert r.status_code == 422
    assert "已归档" in r.json()["message"]

    # 取消归档回到可递卡。
    assert (
        client.post(f"/topics/{tid}/unarchive", json={"by": "alice"}).status_code == 200
    )
    assert _card(client, tid).status_code == 200


@pytest.mark.parametrize("blocked_status", ["pending", "accepted"])
def test_one_card_at_a_time_covers_both_live_and_delivered(client, blocked_status):
    """递卡互斥的两半：一张还活着的卡挡新卡（老行为），一张已交付的卡也挡（新行为）。
    两条走的是同一个闸门，但拒绝理由必须不一样——出路完全不同。"""
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid).json()["data"]["id"]
    if blocked_status == "accepted":
        assert _accept(client, cid).status_code == 200

    r = _card(client, tid, reviewer="bob")
    assert r.status_code == 422
    message = r.json()["message"]
    if blocked_status == "pending":
        assert "改验收人" in message
    else:
        assert "没有新提交" in message
