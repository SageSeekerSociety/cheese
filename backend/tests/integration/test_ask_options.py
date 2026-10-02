"""Option questions in the chat, one-click structured answers.

Any member of a room asks one: the room's agent through `cheese_ask`, a person
from the composer.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import seed_user
from tests.integration.conftest import (
    chat_ws_url,
    join_project_team,
    post_project,
    room_agent_headers,
    room_agent_seat,
    session_auth_headers,
)
from tests.turn_log import open_turn


def _topic(client) -> str:
    p = post_project(client, json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
    ).json()["data"]
    return t["id"]


def _ask(client, tid: str) -> dict:
    r = client.post(
        f"/topics/{tid}/ask",
        json={"question": "分页方案选哪个？", "options": ["cursor", "pageStart"]},
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_ask_creates_option_message(client):
    tid = _topic(client)
    blk = _ask(client, tid)
    # Authored by THIS topic's 分身, not the shared platform ``cheese`` account.
    assert blk["author"] == room_agent_seat(client, tid)
    assert blk["kind"] == "message"
    assert blk["meta"]["options"] == ["cursor", "pageStart"]
    # It shows in the timeline like any message.
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert any(b["id"] == blk["id"] for b in blocks)


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

    summoned = client.post(f"/topics/{tid}/summon", json={})
    assert summoned.status_code == 200, summoned.text
    assert summoned.json()["data"] == {
        "started": False,
        "reason": "nothing_pending",
    }, "它自己问出口的那道题不该把它自己叫起来"


def test_ask_rejects_bad_option_counts(client):
    tid = _topic(client)
    r = client.post(
        f"/topics/{tid}/ask", json={"question": "q", "options": ["only-one"]}
    )
    assert r.status_code == 422
    r = client.post(
        f"/topics/{tid}/ask",
        json={"question": "q", "options": ["a", "b", "c", "d", "e"]},
    )
    assert r.status_code == 422


def test_ask_requires_a_valid_topic_scoped_credential(client):
    tid = _topic(client)
    body = {"question": "选哪个？", "options": ["a", "b"]}
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
    room, _ = _shared_room(client)
    blk = _ask(client, room)

    r = _answer(client, blk["id"], "cursor", "bob")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["meta"]["answered"] == "cursor"
    assert data["meta"]["answered_by"] == "bob"

    # The choice lands as the answerer's own message and summons 芝士. Drive
    # the submitted turn to completion the repo way: hold the WS open (replay
    # catches frames already published) until done/error.
    with client.websocket_connect(chat_ws_url(room, "bob")) as ws:
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    debug = [(b["author"], b["kind"], b["content"][:30]) for b in blocks]
    # 选项落成回答者自己的一条消息，并且在正文里点了问问题的那个席位的名 —— 召唤
    # 写在正文里，时间线上这条消息因此自己说明了它叫的是谁。
    seat = room_agent_seat(client, room)
    assert any(
        b["author"] == "bob" and b["content"] == f"<@{seat}> cursor" for b in blocks
    ), f"choice message never landed: {debug}"


def test_a_name_in_the_body_does_not_change_who_answered(client):
    """bob 登录着，请求体里写的是 alice：答案记在 bob 名下，
    时间线上那句也是 bob 说的。"""
    room, _ = _shared_room(client)
    blk = _ask(client, room)

    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor", "author": "alice"},
        headers=session_auth_headers("bob"),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["meta"]["answered_by"] == "bob"
    seat = room_agent_seat(client, room)
    assert _messages_by(client, room, "bob") == [f"<@{seat}> cursor"]
    assert not any("cursor" in text for text in _messages_by(client, room, "alice"))


def test_the_dev_credential_alone_names_nobody_to_answer_as(client):
    """沙箱 token 开得了门，但它不是任何人：请求体里写了谁也一样，题还开着。"""
    room, _ = _shared_room(client)
    blk = _ask(client, room)

    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor", "author": "bob"},
    )

    assert r.status_code == 401, r.text
    shown = next(
        b
        for b in client.get(f"/topics/{room}/blocks").json()["data"]["data"]
        if b["id"] == blk["id"]
    )
    assert not shown["meta"].get("answered")
    assert _answer(client, blk["id"], "cursor", "bob").status_code == 200


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

    asked = client.post(
        f"/topics/{tid}/ask",
        json={"question": "分页方案选哪个？", "options": ["cursor", "pageStart"]},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=p["id"], topic_id=tid, agent_handle=teammate
            )
        },
    )
    assert asked.status_code == 200, asked.text
    blk = asked.json()["data"]
    assert blk["author"] == teammate

    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    replies = [
        b["content"]
        for b in blocks
        if b["author"] == "alice" and "cursor" in b["content"]
    ]
    assert replies == [f"<@{teammate}> cursor"], replies


def test_answer_validates_option_and_single_shot(client):
    room, _ = _shared_room(client)
    blk = _ask(client, room)

    assert _answer(client, blk["id"], "不存在的", "bob").status_code == 422

    assert _answer(client, blk["id"], "cursor", "bob").status_code == 200
    assert _answer(client, blk["id"], "pageStart", "alice").status_code == 422


# ---- A person's question: any member asks the room, the answer goes back to them.


def _shared_room(client) -> tuple[str, str]:
    """A room of alice's project that bob is also in; returns (room, project)."""
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "周会"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return room["id"], project["id"]


def _person_asks(client, room: str, handle: str):
    return client.post(
        f"/topics/{room}/ask",
        json={"question": "周会挪到周四行吗？", "options": ["行", "不行"]},
        headers=session_auth_headers(handle),
    )


def _answer(client, block_id: str, option: str, handle: str):
    return client.post(
        f"/topics/blocks/{block_id}/answer",
        json={"option": option},
        headers=session_auth_headers(handle),
    )


def _messages_by(client, room: str, handle: str) -> list[str]:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return [b["content"] for b in blocks if b["author"] == handle]


def test_a_member_puts_an_option_question_to_the_room(client):
    room, _ = _shared_room(client)

    asked = _person_asks(client, room, "alice")

    assert asked.status_code == 200, asked.text
    blk = asked.json()["data"]
    assert blk["author"] == "alice"
    assert blk["meta"]["options"] == ["行", "不行"]
    assert "周会挪到周四行吗？" in _messages_by(client, room, "alice")


def test_the_answer_to_a_persons_question_goes_back_to_them(client):
    """bob 点了 alice 那道题的选项：这句话 @ 的是 alice，不叫醒房间里的 AI 队友，
    alice 收到一条点名她的通知。"""
    room, project = _shared_room(client)
    blk = _person_asks(client, room, "alice").json()["data"]

    answered = _answer(client, blk["id"], "行", "bob")

    assert answered.status_code == 200, answered.text
    assert answered.json()["data"]["meta"]["answered"] == "行"
    assert answered.json()["data"]["meta"]["answered_by"] == "bob"
    assert _messages_by(client, room, "bob") == ["<@alice> 行"]
    seat = room_agent_seat(client, room)
    assert not any(seat in text for text in _messages_by(client, room, "bob"))
    inbox = client.get(
        f"/projects/{project}/alerts", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert any("bob" in row["title"] for row in inbox), inbox


def test_nobody_answers_their_own_question(client):
    room, _ = _shared_room(client)
    blk = _person_asks(client, room, "alice").json()["data"]

    refused = _answer(client, blk["id"], "行", "alice")

    assert refused.status_code == 403, refused.text
    shown = next(
        b
        for b in client.get(f"/topics/{room}/blocks").json()["data"]["data"]
        if b["id"] == blk["id"]
    )
    assert not shown["meta"].get("answered")
    # It stays open for everyone else.
    assert _answer(client, blk["id"], "不行", "bob").status_code == 200


def test_someone_outside_the_room_cannot_ask_in_it(client):
    room, _ = _shared_room(client)

    refused = _person_asks(client, room, "mallory")

    assert refused.status_code == 403, refused.text
    assert "mallory" not in {
        b["author"] for b in client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    }


def test_two_equal_options_are_refused(client):
    room, _ = _shared_room(client)
    r = client.post(
        f"/topics/{room}/ask",
        json={"question": "选哪个？", "options": ["行", "行"]},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422


def test_a_persons_question_does_not_wait_on_whoever_started_the_turn(client):
    """芝士问的题等的是这一轮背后的那个人；人问的题问的是整个房间，不挂在谁一个人
    的待办上，也不给谁发「芝士在等你回答」。"""
    bob = seed_user(client, "bob")
    room, _ = _shared_room(client)
    client.portal.call(
        lambda: open_turn(client.test_request_factory, uuid.UUID(room), author="bob")
    )

    blk = _person_asks(client, room, "alice").json()["data"]

    assert blk["meta"]["asked"] is None
    mine = client.get("/awaiting-me", headers=session_auth_headers("bob"))
    assert mine.status_code == 200, mine.text
    assert mine.json()["data"]["data"] == []
    questions = client.get(
        "/notifications",
        params={"type": "CHEESE_QUESTION"},
        headers={"Authorization": f"Bearer {bob}"},
    )
    assert questions.status_code == 200, questions.text
    assert questions.json()["data"]["notifications"] == []


def test_the_agent_asks_with_its_own_credential_and_its_answer_wakes_it(client):
    room, _ = _shared_room(client)
    seat = room_agent_seat(client, room)
    asked = client.post(
        f"/topics/{room}/ask",
        json={"question": "分页方案选哪个？", "options": ["cursor", "pageStart"]},
        headers=room_agent_headers(client, room),
    )
    assert asked.status_code == 200, asked.text
    blk = asked.json()["data"]
    assert blk["author"] == seat

    assert _answer(client, blk["id"], "cursor", "alice").status_code == 200

    assert _messages_by(client, room, "alice") == [f"<@{seat}> cursor"]
