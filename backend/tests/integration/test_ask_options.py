"""cheese ask: option questions in the chat, one-click structured answers.

The shape under test is the one the switchover settled on: `options` is a list
of `{text, explain?}` objects, the answer is an append-only `answer_log`, and
`allow_other` / `reject_option` are decided when the question is ASKED and live
on the question — not on the request that happens to answer it.
"""

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    chat_ws_url,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _topic(client) -> str:
    p = post_project(client, json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": "user-1"},
    ).json()["data"]
    return t["id"]


def _ask(client, tid: str, **extra) -> dict:
    body = {
        "question": "分页方案选哪个？",
        "options": [{"text": "cursor"}, {"text": "pageStart"}],
        **extra,
    }
    r = client.post(f"/topics/{tid}/ask", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _answer(client, block_id: str, payload: dict, handle: str = "user-1") -> dict:
    return client.post(
        f"/topics/blocks/{block_id}/answer",
        json={"author": handle, **payload},
    )


def _op(name: str, **extra) -> dict:
    """One answer request: the version it read, and the id it will not repeat."""
    return {"client_op_id": name, "expect_version": 0, **extra}


def test_ask_creates_option_message(client):
    tid = _topic(client)
    blk = _ask(client, tid)
    # Authored by THIS topic's 分身, not the shared platform ``cheese`` account.
    assert blk["author"] == room_agent_seat(client, tid)
    assert blk["kind"] == "message"
    # Options are objects so one can carry an explanation. Both are absent here
    # because the asker gave neither — a missing `explain` is not an empty one.
    assert blk["meta"]["options"] == [{"text": "cursor"}, {"text": "pageStart"}]
    # The permission to type a free answer is settled here and now.
    assert blk["meta"]["allow_other"] is True
    assert blk["meta"]["reject_option"] is True
    # It shows in the timeline like any message.
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert any(b["id"] == blk["id"] for b in blocks)


def test_ask_records_the_explanation_it_was_given(client):
    tid = _topic(client)
    blk = _ask(
        client,
        tid,
        options=[
            {"text": "cursor", "explain": "一条 SQL 走到底"},
            {"text": "pageStart", "explain": "跳页快"},
        ],
    )
    assert blk["meta"]["options"][0]["explain"] == "一条 SQL 走到底"
    assert blk["meta"]["options"][1]["explain"] == "跳页快"


def test_ask_refuses_a_bare_string_option_list(client):
    """`string[]` is the wrong shape, not another way of writing the right one.

    A compat branch here would leave `meta.options` with two possible readings
    for every reader of it — the renderer, the answer validator, the pending
    predicate and the CLI.
    """
    tid = _topic(client)
    r = client.post(
        f"/topics/{tid}/ask",
        json={"question": "q", "options": ["cursor", "pageStart"]},
    )
    assert r.status_code == 422, r.text


def test_the_question_it_asked_is_not_an_input_it_has_to_read(client):
    """芝士问出口的那道题落进房间，却不是一条交给它去读的输入。

    署名是房间里芝士那条 handle，轮次号这边填不出来：`cheese ask` 只在
    CHEESE_TURN 非空时才带 X-Cheese-Turn，而没有一处产品代码写那个环境变量（这
    份用例的 `_ask` 也一样不带）。按「署名是 agent 且落在某一轮里」去算，这道题
    就成了待读输入：「忘了 @」的补救按钮于是不再答「没有待读的东西」，白开一轮，
    而那一轮的 prompt 里躺着芝士刚问出口的这道题，它对着自己的问题再答一遍。
    """
    tid = _topic(client)
    _ask(client, tid)

    summoned = client.post(f"/topics/{tid}/summon", json={"author": "user-1"})
    assert summoned.status_code == 200, summoned.text
    assert summoned.json()["data"] == {
        "started": False,
        "reason": "nothing_pending",
    }, "它自己问出口的那道题不该把它自己叫起来"


def test_ask_rejects_bad_option_counts(client):
    """提问方给 2-3 项，「以上都不是」由界面补，不占名额。"""
    tid = _topic(client)

    def ask(count: int) -> int:
        return client.post(
            f"/topics/{tid}/ask",
            json={
                "question": "q",
                "options": [{"text": f"o{i}"} for i in range(count)],
            },
        ).status_code

    assert ask(1) == 422
    assert ask(2) == 200
    assert ask(3) == 200
    assert ask(4) == 422
    assert ask(5) == 422


def test_ask_requires_a_valid_topic_scoped_credential(client):
    tid = _topic(client)
    body = {
        "question": "选哪个？",
        "options": [{"text": "a"}, {"text": "b"}],
    }
    without_token = client.post(
        f"/topics/{tid}/ask",
        json=body,
        headers={"X-Cheese-Token": ""},
    )
    assert without_token.status_code == 401

    other = _topic(client)
    wrong_topic = client.post(
        f"/topics/{tid}/ask",
        json=body,
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=str(
                    client.get(f"/topics/{other}").json()["data"]["project_id"]
                ),
                topic_id=other,
            )
        },
    )
    assert wrong_topic.status_code == 403


def test_answer_records_choice_and_posts_reply(client):
    tid = _topic(client)
    blk = _ask(client, tid)

    r = _answer(
        client,
        blk["id"],
        _op("op-1", kind="option", option="cursor"),
    )
    assert r.status_code == 200, r.text
    log = r.json()["data"]["meta"]["answer_log"]
    assert len(log) == 1
    assert log[0]["v"] == 1
    assert log[0]["kind"] == "option"
    assert log[0]["option"] == "cursor"
    assert log[0]["note"] is None
    assert log[0]["by"] == "user-1"
    assert log[0]["client_op_id"] == "op-1"
    assert log[0]["at"]  # 迁移来的历史条目才是 null；这一次是真答的，有时刻

    # The choice lands as the answerer's own message and summons 芝士. Drive
    # the submitted turn to completion the repo way: hold the WS open (replay
    # catches frames already published) until done/error.
    with client.websocket_connect(chat_ws_url(tid, "user-1")) as ws:
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    debug = [(b["author"], b["kind"], b["content"][:30]) for b in blocks]
    # 选项落成回答者自己的一条消息，并且在正文里点了问问题的那个席位的名 —— 召唤
    # 写在正文里，时间线上这条消息因此自己说明了它叫的是谁。
    seat = room_agent_seat(client, tid)
    assert any(
        b["author"] == "user-1" and b["content"] == f"<@{seat}> cursor" for b in blocks
    ), f"choice message never landed: {debug}"


def test_answer_goes_back_to_the_teammate_that_asked(client):
    """房间里坐着不止一位 AI 队友时，点选项接着答的是问这道题的那一位。

    之前点的是房间的默认席位：芝士Opus 问的题，一点选项就换成默认芝士来接，
    而默认芝士手上没有那道题的来龙去脉。
    """
    p = post_project(client, json={"name": "P", "owner_handle": "alice"}).json()["data"]
    tid = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": "alice"},
    ).json()["data"]["id"]
    default = room_agent_seat(client, tid)
    made = client.post(f"/projects/{p['id']}/agents", json={"handle": "opus"})
    assert made.status_code == 200, made.text
    teammate = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{tid}/members",
        json={"handle": teammate, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    assert teammate != default

    asked = client.post(
        f"/topics/{tid}/ask",
        json={
            "question": "分页方案选哪个？",
            "options": [{"text": "cursor"}, {"text": "pageStart"}],
        },
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=p["id"], topic_id=tid, agent_handle=teammate
            )
        },
    )
    assert asked.status_code == 200, asked.text
    blk = asked.json()["data"]
    assert blk["author"] == teammate

    r = _answer(
        client,
        blk["id"],
        _op("op-1", kind="option", option="cursor"),
        handle="alice",
    )
    assert r.status_code == 200, r.text

    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    replies = [
        b["content"]
        for b in blocks
        if b["author"] == "alice" and "cursor" in b["content"]
    ]
    assert replies == [f"<@{teammate}> cursor"], replies


def test_answer_rejects_an_option_that_is_not_there(client):
    tid = _topic(client)
    blk = _ask(client, tid)
    r = _answer(client, blk["id"], _op("op-1", kind="option", option="不存在的"))
    assert r.status_code == 422, r.text


def test_only_the_original_answerer_can_correct(client):
    """更正是本人改自己的答案，不是任何人重开这道题。

    这是房间里的规则，所以是 422（内容/域规则），不是 403 —— 后者说的是「你不是
    这个话题的成员」。
    """
    tid = _topic(client)
    blk = _ask(client, tid)

    first = _answer(client, blk["id"], _op("op-1", kind="option", option="cursor"))
    assert first.status_code == 200, first.text

    other = _answer(
        client,
        blk["id"],
        _op("op-2", kind="option", option="pageStart"),
        handle="user-2",
    )
    assert other.status_code == 422
    assert "原答者" in other.text

    # 本人可以改，旧版本留着：末条是当前生效的那一版。
    corrected = _answer(
        client,
        blk["id"],
        {
            "client_op_id": "op-3",
            "expect_version": 1,
            "kind": "option",
            "option": "pageStart",
        },
    )
    assert corrected.status_code == 200, corrected.text
    log = corrected.json()["data"]["meta"]["answer_log"]
    assert [(e["v"], e["option"]) for e in log] == [(1, "cursor"), (2, "pageStart")]


def test_answer_is_idempotent_per_client_op_id(client):
    """同一次操作重试拿回的是同一版，不是第二次记账。

    幂等查重在版本拒绝**之前**：重试的人手上那个 expect_version 可能已经过期，
    但他那一次确实已经落库了，这时该拿 200 而不是 409。
    """
    tid = _topic(client)
    blk = _ask(client, tid)

    body = _op("op-1", kind="option", option="cursor")
    assert _answer(client, blk["id"], body).status_code == 200

    # 同 key 同内容：200，日志不长。
    again = _answer(client, blk["id"], body)
    assert again.status_code == 200, again.text
    assert len(again.json()["data"]["meta"]["answer_log"]) == 1

    # 同 key 换了内容：这不是重试，是一次没被承认的新操作。
    clash = _answer(
        client,
        blk["id"],
        _op("op-1", kind="option", option="pageStart"),
    )
    assert clash.status_code == 409, clash.text


def test_a_stale_expect_version_loses_the_race(client):
    tid = _topic(client)
    blk = _ask(client, tid)

    first = _answer(client, blk["id"], _op("op-1", kind="option", option="cursor"))
    assert first.status_code == 200

    stale = _answer(
        client,
        blk["id"],
        {
            "client_op_id": "op-2",
            "expect_version": 0,
            "kind": "option",
            "option": "pageStart",
        },
    )
    assert stale.status_code == 409, stale.text


def test_a_free_text_answer_needs_permission_and_fakes_no_option(client):
    """note 是一种答案，不是「某个选项」。

    `allow_other` 是作答许可，写在建题那一刻。没有它，`kind=note` 被拒；有了它，
    note 落库时 `option` 保持 null —— 不为了凑一个合法项去编。
    """
    closed = _topic(client)
    blk = _ask(client, closed, allow_other=False)
    r = _answer(client, blk["id"], _op("op-1", kind="note", note="走第三条路"))
    assert r.status_code == 422
    assert "自由输入" in r.text

    opened = _topic(client)
    blk = _ask(client, opened, allow_other=True)
    r = _answer(
        client,
        blk["id"],
        _op("op-1", kind="note", note="走第三条路"),
    )
    assert r.status_code == 200, r.text
    entry = r.json()["data"]["meta"]["answer_log"][0]
    assert entry["kind"] == "note"
    assert entry["option"] is None, "note 不伪造一个合法选项出来"
    assert entry["note"] == "走第三条路"


def test_reject_is_not_written_into_the_options(client):
    """「以上都不是」是界面补的，不是提问方给的选项之一。"""
    tid = _topic(client)
    blk = _ask(client, tid, reject_option=True)
    r = _answer(client, blk["id"], _op("op-1", kind="reject"))
    assert r.status_code == 200, r.text
    meta = r.json()["data"]["meta"]
    assert [o["text"] for o in meta["options"]] == ["cursor", "pageStart"]
    assert meta["answer_log"][0]["kind"] == "reject"
    assert meta["answer_log"][0]["option"] is None


def test_the_answer_wakes_the_seat_once_and_writes_the_text_once(client):
    """作答只落一份回答文本，只发一条投递。

    以前这条路同时做两件事：`broker.receive_message` 既落文本又起一轮，另外再记
    一次唤醒 —— 同一个答案叫醒席位两次、写两遍。现在只有 `record_agent`，文本由
    这里写一份，正文里带着同一个 `delivery_event_id`，两边对得上账。
    """
    tid = _topic(client)
    blk = _ask(client, tid)
    r = _answer(client, blk["id"], _op("op-1", kind="option", option="cursor"))
    assert r.status_code == 200, r.text

    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    seat = room_agent_seat(client, tid)
    texts = [b for b in blocks if b["content"] == f"<@{seat}> cursor"]
    assert len(texts) == 1, [b["content"] for b in blocks]
    assert texts[0]["meta"]["answer_to"] == blk["id"]
    assert texts[0]["meta"]["delivery_event_id"]
