"""Whom a question asks, and skipping it.

The agent chooses whom it asks, as a message @-mentions people: one person,
several, or everyone in the channel (`to`). Every one of them is notified and
has it on their 待你回答 list until someone answers; one answer settles it for
all of them.

A skip is one person saying he will not answer: it takes the question off his
list and settles his own notice, without anything being said in the room. When
every person it names has skipped, the agent that asked is told in its next
turn — otherwise it waits for an answer that is not coming. A question asked of
everyone is never settled by skips. Taking a skip back (`DELETE
/blocks/{id}/answer`, the same undo an answer has) puts it back on that
person's list.

Real HTTP, PostgreSQL and the room's runner; the session is the scripted stub.
"""

from tests.ask_fixtures import active_ask, wait_turn_idle
from tests.conftest import seed_user, wait_work_idle
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)

QUESTION = {
    "questions": [
        {
            "question": "预算按哪个口径统计？",
            "options": [{"text": "按部门"}, {"text": "按项目"}],
        }
    ]
}


def _room(client) -> tuple[str, str, str]:
    """A project, a 支线 of its channel, and alice's token."""
    token = seed_user(client, "alice")
    data = post_project(client, {"name": "Ask"}, owner="alice").json()["data"]
    return data["id"], in_thread(client, data["root_topic_id"], "alice"), token


def _ask(client, stub_hooks, monkeypatch, room, *, to=None) -> dict:
    """The agent asks inside the turn alice's message started; the turn ends."""
    body = QUESTION if to is None else {**QUESTION, "to": to}
    with active_ask(client, stub_hooks, monkeypatch, room, actor="alice") as headers:
        response = client.post(f"/topics/{room}/asks", json=body, headers=headers)
        assert response.status_code == 200, response.text
    wait_turn_idle(client, room)
    (question,) = response.json()["data"]["blocks"]
    return question


def _prompts(stub_hooks, monkeypatch) -> list[str]:
    """Every prompt the agent's session is handed from here on."""
    seen: list[str] = []
    emit = stub_hooks.emit_turn

    def recording(topic_id, prompt, reply, *, agent=None):
        seen.append(prompt)
        emit(topic_id, prompt, reply, agent=agent)

    monkeypatch.setattr(stub_hooks, "emit_turn", recording)
    return seen


def _skip(client, question_id, *, by="alice"):
    """Skip and wait out the turn it starts: the agent's turn is run from a
    delivery the request records, not by the request itself."""
    response = client.post(
        f"/blocks/{question_id}/skip", headers=session_auth_headers(by)
    )
    wait_work_idle()
    return response


def _undo(client, question_id, *, by="alice"):
    response = client.delete(
        f"/blocks/{question_id}/answer", headers=session_auth_headers(by)
    )
    wait_work_idle()
    return response


def _blocks(client, room) -> list[dict]:
    return client.get(f"/topics/{room}/blocks").json()["data"]["data"]


def _waiting_questions(client, handle) -> list[str]:
    """The questions on ``handle``'s 待你回答 list."""
    r = client.get("/awaiting-me", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return [item["blockId"] for item in r.json()["data"]["data"] if item["blockId"]]


def test_the_person_asked_skips_and_it_leaves_his_list_without_a_word_in_the_room(
    client, stub_hooks, monkeypatch
):
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    assert _waiting_questions(client, "alice") == [question["id"]]
    before = len(_blocks(client, room))
    prompts = _prompts(stub_hooks, monkeypatch)

    response = _skip(client, question["id"])

    assert response.status_code == 200, response.text
    assert _waiting_questions(client, "alice") == []
    # No message from alice: what the room shows is the question, now skipped.
    people_said = [b for b in _blocks(client, room)[before:] if b["author"] == "alice"]
    assert people_said == []
    # The agent is told, in the turn the skip starts, that no answer is coming.
    assert len(prompts) == 1
    assert "跳过" in prompts[0] and "没有回答" in prompts[0]
    assert "预算按哪个口径统计？" in prompts[0]


def test_only_the_person_the_question_waits_on_can_skip_it(
    client, stub_hooks, monkeypatch
):
    pid, room, _ = _room(client)
    seed_user(client, "bob")
    join_project_team(client, pid, "bob")
    question = _ask(client, stub_hooks, monkeypatch, room)
    prompts = _prompts(stub_hooks, monkeypatch)

    refused = _skip(client, question["id"], by="bob")

    assert refused.status_code == 403, refused.text
    assert _waiting_questions(client, "alice") == [question["id"]]
    assert prompts == []


def test_taking_the_skip_back_makes_the_question_wait_again(
    client, stub_hooks, monkeypatch
):
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    _skip(client, question["id"])
    prompts = _prompts(stub_hooks, monkeypatch)

    response = _undo(client, question["id"])

    assert response.status_code == 200, response.text
    assert _waiting_questions(client, "alice") == [question["id"]]
    # The agent that had been told "skipped" is told it waits again.
    assert len(prompts) == 1 and "撤销" in prompts[0]


def test_an_answered_question_cannot_also_be_skipped(client, stub_hooks, monkeypatch):
    """One response closes a question, a skip as much as an answer."""
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    _skip(client, question["id"])

    again = _skip(client, question["id"])

    assert again.status_code == 409, again.text


def test_skipping_settles_the_question_s_notice(client, stub_hooks, monkeypatch):
    """The inbox notice stops saying 待你回答 once the question is skipped."""
    _, room, token = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)

    def notice() -> dict:
        r = client.get(
            "/notifications",
            params={"type": "CHEESE_QUESTION"},
            headers={"Authorization": f"Bearer {token}"},
        )
        (row,) = r.json()["data"]["notifications"]
        return row

    assert notice()["read"] is False
    _skip(client, question["id"])
    assert notice()["read"] is True


#: Each person's signed-in token, for the inbox (which wants a numeric user id).
_TOKENS: dict[str, str] = {}


def _with_bob(client) -> tuple[str, str]:
    pid, room, alice = _room(client)
    _TOKENS["alice"] = alice
    _TOKENS["bob"] = seed_user(client, "bob")
    join_project_team(client, pid, "bob")
    return pid, room


def _notified(client, handle) -> list[dict]:
    r = client.get(
        "/notifications",
        params={"type": "CHEESE_QUESTION"},
        headers={"Authorization": f"Bearer {_TOKENS[handle]}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["notifications"]


def test_the_agent_asks_the_people_it_names_and_each_is_waited_on(
    client, stub_hooks, monkeypatch
):
    _, room = _with_bob(client)

    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])

    assert question["meta"]["asked"] == ["alice", "bob"]
    assert _waiting_questions(client, "alice") == [question["id"]]
    assert _waiting_questions(client, "bob") == [question["id"]]
    assert len(_notified(client, "bob")) == 1


def test_a_question_asked_of_everyone_waits_on_everyone_in_the_channel(
    client, stub_hooks, monkeypatch
):
    _, room = _with_bob(client)

    question = _ask(client, stub_hooks, monkeypatch, room, to=["all"])

    assert question["meta"]["asked_all"] is True
    assert sorted(question["meta"]["asked"]) == ["alice", "bob"]
    assert _waiting_questions(client, "bob") == [question["id"]]
    assert len(_notified(client, "bob")) == 1


def test_asking_someone_outside_the_channel_is_refused_by_name(
    client, stub_hooks, monkeypatch
):
    _, room, _ = _room(client)
    seed_user(client, "mallory")  # exists, but not in this project
    with active_ask(client, stub_hooks, monkeypatch, room, actor="alice") as headers:
        refused = client.post(
            f"/topics/{room}/asks",
            json={**QUESTION, "to": ["alice", "mallory"]},
            headers=headers,
        )
    wait_turn_idle(client, room)

    assert refused.status_code == 422, refused.text
    assert "mallory" in refused.text
    assert _waiting_questions(client, "alice") == []


def test_one_answer_settles_it_for_everyone_asked(client, stub_hooks, monkeypatch):
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])
    prompts = _prompts(stub_hooks, monkeypatch)

    with room_socket(client, room, "bob") as ws:
        post_message(
            client, room, "bob", {"content": "按部门", "reply_to": question["id"]}
        )
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    wait_turn_idle(client, room)

    assert _waiting_questions(client, "alice") == []
    assert _waiting_questions(client, "bob") == []
    assert len(prompts) == 1 and "按部门" in prompts[0]


def test_skipping_a_question_asked_of_several_takes_it_off_only_your_list(
    client, stub_hooks, monkeypatch
):
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])
    prompts = _prompts(stub_hooks, monkeypatch)

    assert _skip(client, question["id"], by="alice").status_code == 200

    assert _waiting_questions(client, "alice") == []
    assert _waiting_questions(client, "bob") == [question["id"]]
    assert [n["read"] for n in _notified(client, "alice")] == [True]
    assert [n["read"] for n in _notified(client, "bob")] == [False]
    # Bob may still answer: the agent is not told anything yet.
    assert prompts == []

    _skip(client, question["id"], by="bob")

    assert _waiting_questions(client, "bob") == []
    assert len(prompts) == 1 and "跳过" in prompts[0]


def test_taking_back_your_skip_puts_it_back_on_your_list_only(
    client, stub_hooks, monkeypatch
):
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])
    _skip(client, question["id"], by="alice")
    prompts = _prompts(stub_hooks, monkeypatch)

    assert _undo(client, question["id"], by="alice").status_code == 200

    assert _waiting_questions(client, "alice") == [question["id"]]
    assert _waiting_questions(client, "bob") == [question["id"]]
    # The agent never heard of that skip, so its undo is no news to it.
    assert prompts == []


def test_a_question_asked_of_everyone_is_never_settled_by_skips(
    client, stub_hooks, monkeypatch
):
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["all"])
    prompts = _prompts(stub_hooks, monkeypatch)

    _skip(client, question["id"], by="alice")
    _skip(client, question["id"], by="bob")

    assert _waiting_questions(client, "alice") == []
    assert _waiting_questions(client, "bob") == []
    assert prompts == []


def test_what_you_say_after_skipping_is_not_your_answer(
    client, stub_hooks, monkeypatch
):
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])
    _skip(client, question["id"], by="alice")

    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "我去吃饭了"})
        ws.receive_json()
    wait_turn_idle(client, room)

    shown = next(b for b in _blocks(client, room) if b["id"] == question["id"])
    assert [e["kind"] for e in shown["meta"]["answer_log"]] == ["skipped"]
    assert _waiting_questions(client, "bob") == [question["id"]]


def test_a_question_naming_one_person_the_old_way_is_answered_by_his_words(
    client, stub_hooks, monkeypatch
):
    """A backend from before the addressee list, still running past the
    migration in a blue/green deploy, writes ``asked`` as a bare handle."""
    import asyncio
    import uuid

    from sqlalchemy import update

    from app.domain.block.models import Block

    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)

    async def old_shape():
        async with client.test_factory() as session:
            row = await session.get(Block, uuid.UUID(question["id"]))
            await session.execute(
                update(Block)
                .where(Block.id == row.id)
                .values(meta={**row.meta, "asked": "alice"})
            )
            await session.commit()

    asyncio.run(old_shape())
    assert _waiting_questions(client, "alice") != []

    with room_socket(client, room, "alice") as ws:
        post_message(client, room, "alice", {"content": "按项目"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    wait_turn_idle(client, room)

    assert _waiting_questions(client, "alice") == []
