"""房间给它派出的活留话 (`POST /topics/{id}/tell`, `cheese tell`).

One direction, and nothing is woken. The worker doing that thread is a 分身
inside this very session, which the room reaches with its own tooling — waking a
place for it would mean raising a container for the shape threads stopped
having. What this channel is for is the RECORD: what the room told its worker
lands where the work is, which is the only place the person watching that thread
can read it.

Why not `POST /topics/{id}/comments`: it summons **only when the commenter is
human**, so an agent wrote a row and nothing happened, and that route is not in
`app.main._CHEESE_WRITE_PATHS` either, so an agent's call never reached the
per-turn token gate (a missing entry admits silently; it does not 401).

So every test here asserts one of three things: the message lands, NOTHING is
woken by it, and the room→its-own-thread edge is the only path open.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import wait_work_idle


def _project(client) -> dict:
    return client.post("/projects", json={"name": "P"}).json()["data"]


def _topic(client, project_id: str, title: str = "房间") -> dict:
    return client.post(
        "/topics", json={"project_id": project_id, "title": title}
    ).json()["data"]


def _split(client, parent_id: str, title: str) -> dict:
    sub = client.post(f"/topics/{parent_id}/split", json={"title": title}).json()[
        "data"
    ]
    # The 分身's auto-kickoff turn must finish before the test looks at prompts.
    wait_work_idle()
    return sub


def _tell(client, sender_id: str, target: str, content: str, **kw):
    return client.post(
        f"/topics/{sender_id}/tell",
        json={"target": target, "content": content},
        **kw,
    )


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def _card_blocks(client, room_id: str, task_id: str) -> list[dict]:
    """一张卡自己的时间线 —— 卡不是地点，读它要经过它所在的房间。"""
    r = client.get(f"/topics/{room_id}/tasks/{task_id}")
    assert r.status_code == 200, r.text
    return r.json()["data"]["blocks"]


# --- 父 → 直接子: 落地 + 真的叫醒 -------------------------------------------


def _record_screens(stub_hooks) -> list[str]:
    """每一次「起一块屏幕」的 topic id。起屏幕就是起容器，这是唯一看得见它的地方。"""
    seen: list[str] = []
    original = stub_hooks.ensure_ready

    async def _spy(**kw):
        seen.append(str(kw.get("topic_id")))
        return await original(**kw)

    stub_hooks.ensure_ready = _spy
    return seen


def test_a_room_leaves_a_note_on_its_thread_and_wakes_nobody(client, stub_hooks):
    """留话落在那条活的时间线上，而**没有任何东西被叫醒**。

    做那条活的分身就在房间自己的会话里，房间直接给它发消息就是了；朝那条活开一轮，
    平台就得为它起一整个容器 —— 正是「一条活 = 房间会话里的一个分身」拆掉的东西。
    """
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "数据清洗")

    screens = _record_screens(stub_hooks)
    r = _tell(client, parent["id"], sub["id"], "口径改了：只算活跃用户")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["target_topic_id"] == sub["id"]
    assert data["target_title"] == "数据清洗"
    wait_work_idle()

    contents = [b["content"] for b in _card_blocks(client, parent["id"], sub["id"])]
    assert any("口径改了：只算活跃用户" in c for c in contents)
    assert screens == [], f"留话起了屏幕——这是在复活容器：{screens}"


def test_relayed_block_is_authored_by_the_receiving_room(client):
    """A message from someone who is not on the receiver's roster reads as a
    ghost — same rule 结论回流 follows."""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "查一个事实")

    _tell(client, parent["id"], sub["id"], "顺带看下 B 方案")
    wait_work_idle()

    on_card = _card_blocks(client, parent["id"], sub["id"])
    relayed = [b for b in on_card if "顺带看下 B 方案" in b["content"]]
    assert len(relayed) == 1
    block = relayed[0]
    assert block["author_type"] == "ai"
    # refs points back at the sender, so the chip links home.
    assert parent["id"] in (block.get("refs") or [])
    # The room is named in the block so the thread can see where it came from.
    assert "房间追加" in block["content"]


def test_target_can_be_a_title_or_a_ref_token(client):
    """芝士 holds topics as `@标题` / `<#id>`; both spellings must work."""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "标题寻址")

    by_title = _tell(client, parent["id"], "标题寻址", "一")
    assert by_title.status_code == 200
    assert by_title.json()["data"]["target_topic_id"] == sub["id"]
    wait_work_idle()

    by_token = _tell(client, parent["id"], f"<#{sub['id']}>", "二")
    assert by_token.status_code == 200
    assert by_token.json()["data"]["target_topic_id"] == sub["id"]
    wait_work_idle()


# --- 方向限制: 只有父子这条边 -----------------------------------------------


def test_a_card_cannot_be_the_sender(client):
    """留话是**房间**对它派出的活说的。一张卡说不出话——它不是地点，那个 id 上
    没有房间，所以连不到这条路的入口。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    a = _split(client, parent["id"], "支线A")
    b = _split(client, parent["id"], "支线B")

    assert _tell(client, a["id"], b["id"], "偷偷说句话").status_code == 404
    # Nothing landed in B either — a refused relay must not leave a message.
    assert not any(
        "偷偷说句话" in blk["content"]
        for blk in _card_blocks(client, parent["id"], b["id"])
    )


def test_unrelated_topics_cannot_tell_each_other(client):
    """Two independent topics of the same project: no edge, no channel."""
    p = _project(client)
    one = _topic(client, p["id"], "话题一")
    two = _topic(client, p["id"], "话题二")

    r = _tell(client, one["id"], two["id"], "喂")
    assert r.status_code == 422
    assert not any("喂" in blk["content"] for blk in _blocks(client, two["id"]))


def test_a_card_cannot_split_out_more_work(client):
    """派活是房间的动作。一张卡派不出活，因为派活要一个地点的地址，而卡没有。

    这不是一条新规矩，是同一条规矩的另一面：一批活共用一棵树，谁往里加活是房间
    说了算。
    """
    p = _project(client)
    room = _topic(client, p["id"])
    first = _split(client, room["id"], "第一件活")

    r = client.post(f"/topics/{first['id']}/split", json={"title": "再来一件"})
    assert r.status_code == 404


def test_missing_topic_is_404_and_a_stranger_topic_says_why(client):
    p = _project(client)
    parent = _topic(client, p["id"])

    assert _tell(client, parent["id"], str(uuid.uuid4()), "x").status_code == 404
    other = _topic(client, p["id"], "别人的话题")
    r = _tell(client, parent["id"], other["id"], "x")
    assert r.status_code == 422
    # 说清为什么，否则调用方会以为自己打错了 id 并原样重试。房间的 id 尤其要说，
    # 因为它是一个**看起来最像**能收留话的东西。
    assert "房间" in r.json()["message"]

    stranger_room = _topic(client, p["id"], "别人的房间")
    theirs = _split(client, stranger_room["id"], "别人的活")
    refused = _tell(client, parent["id"], theirs["id"], "x")
    assert refused.status_code == 422
    assert "不归你" in refused.json()["message"]


def test_unknown_title_lists_the_reachable_topics(client):
    p = _project(client)
    parent = _topic(client, p["id"])
    _split(client, parent["id"], "真的子话题")

    r = _tell(client, parent["id"], "不存在的标题", "x")
    assert r.status_code == 422
    assert "真的子话题" in r.json()["message"]


def test_empty_and_oversized_content_are_refused(client):
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")

    assert _tell(client, parent["id"], sub["id"], "   ").status_code == 422
    assert _tell(client, parent["id"], sub["id"], "长" * 5000).status_code == 422


# --- per-turn token 闸门 (_CHEESE_WRITE_PATHS) -------------------------------


def test_tell_is_token_gated(client):
    """The route must be IN `_CHEESE_WRITE_PATHS`: a write route left out does not
    401, it is admitted with no scope check at all. No token → 401 proves the
    entry exists; a token scoped to another topic → 401 proves the `topic`
    capture group is being matched."""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")

    no_token = _tell(
        client, parent["id"], sub["id"], "x", headers={"X-Cheese-Token": ""}
    )
    assert no_token.status_code == 401

    elsewhere = _topic(client, p["id"], "别的话题")
    wrong_scope = mint_scoped_token(project_id=p["id"], topic_id=elsewhere["id"])
    r = _tell(
        client,
        parent["id"],
        sub["id"],
        "x",
        headers={"X-Cheese-Token": wrong_scope},
    )
    assert r.status_code == 401

    right_scope = mint_scoped_token(project_id=p["id"], topic_id=parent["id"])
    r_ok = _tell(
        client,
        parent["id"],
        sub["id"],
        "凭本话题的 token 说话",
        headers={"X-Cheese-Token": right_scope},
    )
    assert r_ok.status_code == 200
    wait_work_idle()


# --- 归档的收方: 写得进去，但没人会被叫醒 -----------------------------------


def test_a_closed_thread_can_still_be_told_something(client):
    """收起不是冻结：a finished thread still takes a message.

    A room can be talked in after it is archived, so a thread has no reason to be
    stricter than the room it lives in — otherwise "改一行" after delivery means
    opening a second piece of work and cutting the history in half.
    """
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "已经收工的活")
    wait_work_idle()

    r = _tell(client, parent["id"], sub["id"], "还有一件事")
    assert r.status_code == 200
    on_card = _card_blocks(client, parent["id"], sub["id"])
    assert any("还有一件事" in b["content"] for b in on_card)


def test_a_thread_in_an_archived_room_still_takes_the_note(client):
    """归档冻的是工作面，不是记录：留话仍然写得进去，只是本来也没人会被叫醒。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "房间要归档了")
    wait_work_idle()
    client.post(f"/topics/{parent['id']}/archive", json={"by": "user-1"})

    r = _tell(client, parent["id"], sub["id"], "还有一件事")
    assert r.status_code == 200
    on_card = _card_blocks(client, parent["id"], sub["id"])
    assert any("还有一件事" in b["content"] for b in on_card)
