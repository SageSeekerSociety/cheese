"""上下传话 (`POST /topics/{id}/tell`, `cheese tell`).

What was broken and is under test here: the room → running-thread direction
had no working channel at all. `POST /topics/{id}/comments` — the only thing that
looked like one — summons **only when the commenter is human**, so a parent's
芝士 wrote a row and woke nobody, and that route is not in
`app.main._CHEESE_WRITE_PATHS` either, so an agent's call never reached the
per-turn token gate (a missing entry admits silently; it does not 401).

So every test here asserts one of three things: the message lands, the receiver
is really WOKEN (the stub agent saw a prompt containing it), and the edge is the
only path open.
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


# --- 父 → 直接子: 落地 + 真的叫醒 -------------------------------------------


def test_parent_tells_child_and_the_child_is_woken(client, stub_hooks):
    """The whole point: the message reaches the child's timeline AND a turn runs
    there with the text in its prompt."""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "数据清洗")

    r = _tell(client, parent["id"], sub["id"], "口径改了：只算活跃用户")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["direction"] == "to_child"
    assert data["delivery"] == "woke"
    assert data["target_topic_id"] == sub["id"]
    wait_work_idle()

    contents = [b["content"] for b in _blocks(client, sub["id"])]
    assert any("口径改了：只算活跃用户" in c for c in contents)
    # 叫醒: the agent actually got the text, tagged as a platform instruction.
    assert stub_hooks.last_prompt is not None
    assert "口径改了：只算活跃用户" in stub_hooks.last_prompt
    assert "【平台】" in stub_hooks.last_prompt
    # …and it is told the relay OVERRIDES the one-shot brief, which is the whole
    # reason this channel exists.
    assert "以这条为准" in stub_hooks.last_prompt


def test_relayed_block_is_authored_by_the_receiving_room(client):
    """A message from someone who is not on the receiver's roster reads as a
    ghost — same rule 结论回流 follows."""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "查一个事实")

    _tell(client, parent["id"], sub["id"], "顺带看下 B 方案")
    wait_work_idle()

    relayed = [
        b for b in _blocks(client, sub["id"]) if "顺带看下 B 方案" in b["content"]
    ]
    assert len(relayed) == 1
    block = relayed[0]
    assert block["author_type"] == "ai"
    # refs points back at the sender, so the chip links home.
    assert parent["id"] in (block.get("refs") or [])
    # The room is named in the block so the thread can see where it came from.
    assert "房间追加" in block["content"]


def test_child_tells_parent(client, stub_hooks):
    """The other allowed direction. Distinct from `conclude`: no card, no
    settlement — it is a question or a mid-flight finding."""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")

    r = _tell(client, sub["id"], parent["id"], "发现简报里那条前提不成立")
    assert r.status_code == 200
    assert r.json()["data"]["direction"] == "to_parent"
    wait_work_idle()

    assert any(
        "发现简报里那条前提不成立" in b["content"]
        for b in _blocks(client, parent["id"])
    )
    assert "发现简报里那条前提不成立" in (stub_hooks.last_prompt or "")
    assert "支线" in (stub_hooks.last_prompt or "")


def test_a_thread_can_address_its_room_as_parent(client):
    """回话不该先要求查到房间叫什么 —— `cheese tell parent "..."`."""
    p = _project(client)
    parent = _topic(client, p["id"], "上面那个房间")
    sub = _split(client, parent["id"], "子活")

    r = _tell(client, sub["id"], "parent", "问一句：口径按哪版？")
    assert r.status_code == 200
    assert r.json()["data"]["target_topic_id"] == parent["id"]
    wait_work_idle()

    # A ROOM has nothing above it to answer — plain answer, not a confusing 404.
    r2 = _tell(client, parent["id"], "parent", "x")
    assert r2.status_code == 422
    assert "没有可以回话的地方" in r2.json()["message"]


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


def test_siblings_cannot_tell_each_other(client):
    """同一个房间里的两条支线之间发不通 —— a general place-to-place mailbox is
    the end of place-level isolation."""
    p = _project(client)
    parent = _topic(client, p["id"])
    a = _split(client, parent["id"], "支线A")
    b = _split(client, parent["id"], "支线B")

    r = _tell(client, a["id"], b["id"], "偷偷说句话")
    assert r.status_code == 422
    assert "上下关系" in r.json()["message"]
    # Nothing landed in B either — a refused relay must not leave a message.
    assert not any("偷偷说句话" in blk["content"] for blk in _blocks(client, b["id"]))


def test_unrelated_topics_cannot_tell_each_other(client):
    """Two independent topics of the same project: no edge, no channel."""
    p = _project(client)
    one = _topic(client, p["id"], "话题一")
    two = _topic(client, p["id"], "话题二")

    r = _tell(client, one["id"], two["id"], "喂")
    assert r.status_code == 422
    assert not any("喂" in blk["content"] for blk in _blocks(client, two["id"]))


def test_splitting_from_a_thread_makes_a_sibling_not_a_child(client):
    """Work does not nest, so there is no third level to be two levels down.

    Splitting from inside a piece of work puts the new work in the SAME room —
    it is that room's to reach, and the thread it was split from cannot reach it
    any more than any other sibling can.
    """
    p = _project(client)
    room = _topic(client, p["id"])
    first = _split(client, room["id"], "第一件活")
    second = _split(client, first["id"], "干着干着发现的第二件活")

    assert second["room_id"] == room["id"]
    # The room reaches it directly...
    assert _tell(client, room["id"], second["id"], "正常说话").status_code == 200
    wait_work_idle()
    # ...and the thread it came out of does not.
    assert _tell(client, first["id"], second["id"], "偷偷说句话").status_code == 422


def test_missing_topic_is_404_and_a_stranger_topic_says_why(client):
    p = _project(client)
    parent = _topic(client, p["id"])

    assert _tell(client, parent["id"], str(uuid.uuid4()), "x").status_code == 404
    other = _topic(client, p["id"], "别人的话题")
    r = _tell(client, parent["id"], other["id"], "x")
    assert r.status_code == 422
    # 说清为什么，否则调用方会以为自己打错了 id 并原样重试。
    assert "上下关系" in r.json()["message"]


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
    assert r.json()["data"]["delivery"] != "archived"


def test_a_thread_in_an_archived_room_is_not_woken_and_says_so(client):
    """The room is the work面 — freezing it freezes what is inside it."""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "房间要归档了")
    wait_work_idle()
    client.post(f"/topics/{parent['id']}/archive", json={"by": "user-1"})

    r = _tell(client, parent["id"], sub["id"], "还有一件事")
    assert r.status_code == 200
    assert r.json()["data"]["delivery"] == "archived"
