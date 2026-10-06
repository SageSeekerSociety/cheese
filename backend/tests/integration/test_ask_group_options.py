"""cheese ask: option questions in the chat, one-click structured answers.

The shape under test is the one the switchover settled on: `options` is a list
of `{text, explain?}` objects, the answer is an append-only `answer_log`, and
`allow_other` / `reject_option` are decided when the question is ASKED and live
on the question — not on the request that happens to answer it.

This is the group path: `POST /topics/{id}/asks` opens a group, `POST
/topics/blocks/{id}/answers` files a versioned answer. Persisted non-group rows
(the shape a person's question kept after the migration) are answered through
the same versioned endpoint; there is no second create route any more.
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.repositories import BlockRepository
from tests.ask_fixtures import active_ask, legacy_question
from tests.integration.conftest import (
    chat_ws_url,
    join_project_team,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _topic(client) -> str:
    auth = session_auth_headers("user-1")
    p = post_project(client, json={"name": "P"}, headers=auth).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
        headers=auth,
    ).json()["data"]
    return t["id"]


@pytest.fixture
def create_question(client, stub_hooks, monkeypatch):
    def create(tid, **extra):
        with active_ask(client, stub_hooks, monkeypatch, tid) as headers:
            response = client.post(
                f"/topics/{tid}/asks",
                json={
                    "questions": [
                        {
                            "question": "分页方案选哪个？",
                            "options": [{"text": "cursor"}, {"text": "pageStart"}],
                            **extra,
                        }
                    ]
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            return response.json()["data"]["blocks"][0]

    return create


def _answer(client, block_id: str, payload: dict, handle: str = "user-1") -> dict:
    return client.post(
        f"/topics/blocks/{block_id}/answers",
        json={"author": handle, **payload},
        headers=session_auth_headers(handle),
    )


def _op(name: str, **extra) -> dict:
    """One answer request: the version it read, and the id it will not repeat."""
    return {"client_op_id": name, "expect_version": 0, **extra}


def test_ask_creates_option_message(client, create_question):
    tid = _topic(client)
    blk = create_question(tid)
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


def test_ask_records_the_explanation_it_was_given(client, create_question):
    tid = _topic(client)
    blk = create_question(
        tid,
        options=[
            {"text": "cursor", "explain": "一条 SQL 走到底"},
            {"text": "pageStart", "explain": "跳页快"},
        ],
    )
    assert blk["meta"]["options"][0]["explain"] == "一条 SQL 走到底"
    assert blk["meta"]["options"][1]["explain"] == "跳页快"


def test_ask_refuses_a_bare_string_option_list(client, stub_hooks, monkeypatch):
    """`string[]` is the wrong shape, not another way of writing the right one.

    A compat branch here would leave `meta.options` with two possible readings
    for every reader of it — the renderer, the answer validator, the pending
    predicate and the CLI.
    """
    tid = _topic(client)
    with active_ask(client, stub_hooks, monkeypatch, tid) as headers:
        r = client.post(
            f"/topics/{tid}/asks",
            json={"questions": [{"question": "q", "options": ["cursor", "pageStart"]}]},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert "对象" in r.text


def test_the_question_it_asked_is_not_an_input_it_has_to_read(client, create_question):
    """An agent's own question must not become its next unread input."""
    tid = _topic(client)
    create_question(tid)

    summoned = client.post(f"/topics/{tid}/summon", json={"author": "user-1"})
    assert summoned.status_code == 200, summoned.text
    assert summoned.json()["data"] == {
        "started": False,
        "reason": "nothing_pending",
    }, "它自己问出口的那道题不该把它自己叫起来"


def test_ask_rejects_bad_option_counts(client, stub_hooks, monkeypatch):
    """提问方给 2-3 项，「以上都不是」由界面补，不占名额。"""
    tid = _topic(client)
    with active_ask(client, stub_hooks, monkeypatch, tid) as headers:

        def ask(count: int) -> int:
            return client.post(
                f"/topics/{tid}/asks",
                json={
                    "questions": [
                        {
                            "question": "q",
                            "options": [{"text": f"o{i}"} for i in range(count)],
                        }
                    ]
                },
                headers=headers,
            ).status_code

        assert ask(1) == 422
        assert ask(2) == 200
        assert ask(3) == 200
        assert ask(4) == 422
        assert ask(5) == 422


def test_two_equal_options_are_refused(client, stub_hooks, monkeypatch):
    """两个选项写着一样的字，界面就分不出这一题在问什么。"""
    tid = _topic(client)
    with active_ask(client, stub_hooks, monkeypatch, tid) as headers:
        r = client.post(
            f"/topics/{tid}/asks",
            json={
                "questions": [
                    {
                        "question": "选哪个？",
                        "options": [{"text": "行"}, {"text": "行"}],
                    }
                ]
            },
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert "重复" in r.text


def test_ask_requires_a_valid_topic_scoped_credential(client):
    tid = _topic(client)
    body = {
        "questions": [
            {
                "question": "选哪个？",
                "options": [{"text": "a"}, {"text": "b"}],
            }
        ]
    }
    without_token = client.post(
        f"/topics/{tid}/asks",
        json=body,
        headers={"X-Cheese-Token": ""},
    )
    assert without_token.status_code == 403
    assert "登录" in without_token.text

    other = _topic(client)
    wrong_topic = client.post(
        f"/topics/{tid}/asks",
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
    blk = legacy_question(client, tid)

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
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    tid = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
        headers=session_auth_headers("alice"),
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

    # This endpoint still serves persisted non-group questions after the switch.
    blk = legacy_question(client, tid, seat=teammate)
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
    blk = legacy_question(client, tid)
    r = _answer(client, blk["id"], _op("op-1", kind="option", option="不存在的"))
    assert r.status_code == 422, r.text


def test_only_the_original_answerer_can_correct(client):
    """更正是本人改自己的答案，不是任何人重开这道题。

    这是房间里的规则，所以是 422（内容/域规则），不是 403 —— 后者说的是「你不是
    这个话题的成员」。
    """
    tid = _topic(client)
    project_id = client.get(f"/topics/{tid}").json()["data"]["project_id"]
    # user-2 is a room member too, so its refusal is the room rule (422), not the
    # non-member 403 the answer route raises before it.
    join_project_team(client, project_id, "user-2")
    blk = legacy_question(client, tid)

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
    blk = legacy_question(client, tid)

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
    blk = legacy_question(client, tid)

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
    blk = legacy_question(client, closed, allow_other=False)
    r = _answer(client, blk["id"], _op("op-1", kind="note", note="走第三条路"))
    assert r.status_code == 422
    assert "自由输入" in r.text

    opened = _topic(client)
    blk = legacy_question(client, opened, allow_other=True)
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
    blk = legacy_question(client, tid, reject_option=True)
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
    blk = legacy_question(client, tid)
    r = _answer(client, blk["id"], _op("op-1", kind="option", option="cursor"))
    assert r.status_code == 200, r.text

    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    seat = room_agent_seat(client, tid)
    texts = [b for b in blocks if b["content"] == f"<@{seat}> cursor"]
    assert len(texts) == 1, [b["content"] for b in blocks]
    assert texts[0]["meta"]["answer_to"] == blk["id"]
    assert texts[0]["meta"]["delivery_event_id"]


def test_an_unfinished_group_is_not_shadowed_by_a_later_one(
    client, stub_hooks, monkeypatch
):
    """后发的组全答完之后，待答的还是前面那组没答完的。

    待答判断原来对所有带 `options` 的题做「每处只取最近一题」：A 组没答完，B 组
    后发，B 一答完这一处就只看得到 B 组那几道已答题，于是整处被判成「没有待
    答」——A 组里没答的成员从此不再显示。候选现在按**真实存储的 `ask_group`**
    分开取：组按组取未答成员，不参加那个「最近一题」的 distinct，所以 B 组答完
    不会把 A 组藏掉。
    """
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "周会"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]

    with active_ask(client, stub_hooks, monkeypatch, room, actor="alice") as headers:
        made = client.post(
            f"/topics/{room}/asks",
            json={
                "questions": [
                    {
                        "question": "分页方案选哪个？",
                        "options": [{"text": "cursor"}, {"text": "pageStart"}],
                    },
                    {
                        "question": "缓存放哪？",
                        "options": [{"text": "redis"}, {"text": "memory"}],
                    },
                ]
            },
            headers=headers,
        )
        assert made.status_code == 200, made.text
        group_a = made.json()["data"]["blocks"]
    assert len(group_a) == 2

    with active_ask(client, stub_hooks, monkeypatch, room, actor="alice") as headers:
        made = client.post(
            f"/topics/{room}/asks",
            json={
                "questions": [
                    {
                        "question": "周会挪到周四行吗？",
                        "options": [{"text": "行"}, {"text": "不行"}],
                    }
                ]
            },
            headers=headers,
        )
        assert made.status_code == 200, made.text
        group_b_data = made.json()["data"]
        group_b = group_b_data["blocks"]
    assert len(group_b) == 1

    # B 组只有一道题，答完它就整组收口。组成员不能逐题作答（服务端对组成员的
    # `/answers` 一律 422「问题组必须整组提交」），所以这里走整组 settle ——
    # `_answer` 只用来答迁移后仍是单题的那些行。
    answered = client.post(
        f"/topics/asks/{group_b_data['group']['id']}/settle",
        json={
            "topic_id": group_b_data["group"]["topic_id"],
            "asked_by": group_b_data["group"]["asked_by"],
            "client_op_id": "op-1",
            "expect_version": 0,
            "answered": [
                {
                    "block_id": group_b[0]["id"],
                    "kind": "option",
                    "option": "行",
                    "client_op_id": "op-1-0",
                    "expect_version": 0,
                }
            ],
            "later": [],
            "unanswered": [],
        },
        headers=session_auth_headers("alice"),
    )
    assert answered.status_code == 200, answered.text
    assert (
        answered.json()["data"]["blocks"][0]["meta"]["answer_log"][-1]["option"] == "行"
    )

    detail = client.get(
        f"/topics/{room}", headers=session_auth_headers("alice")
    ).json()["data"]
    assert detail["presentation"]["phrase"] == "awaiting_answer", (
        "A 组里还有没答的成员，房间不该显示成没有待答"
    )

    async def read_rooms():
        async with client.test_request_factory() as session:
            return await BlockRepository(session).awaiting_answer_blocks(
                [uuid.UUID(room)]
            )

    rooms = client.portal.call(read_rooms)
    asked, pending_id = rooms[uuid.UUID(room)]
    assert asked == "alice", "挂在的人是 A 组的发起人，不是已答完的 B 组"
    assert pending_id == uuid.UUID(group_a[0]["id"]), (
        "选中的必须是 A 组那个未答成员，不是 B 组"
    )
