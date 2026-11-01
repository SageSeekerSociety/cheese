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

Answering by one of its options (or 「其他」, a reply to it in one's own words)
is held to the same rule as typing an answer and skipping: only the people it
names, anyone in the channel when it asks everyone, anyone when it names nobody
in particular. Anyone else's reply to it is an ordinary message, not its answer.

Real HTTP, PostgreSQL and the room's runner; the session is the scripted stub.
"""

import asyncio
import uuid

from app.api.routes import blocks as block_routes
from tests.ask_fixtures import active_ask, wait_turn_idle
from tests.conftest import seed_user, wait_work_idle
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    mail,
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


def _ask(client, stub_hooks, monkeypatch, room, *, to=None, platform_turn=False):
    """The agent asks inside the turn alice's message started (or, with
    ``platform_turn``, one the platform started); the turn ends."""
    body = QUESTION if to is None else {**QUESTION, "to": to}
    with active_ask(
        client,
        stub_hooks,
        monkeypatch,
        room,
        actor="alice",
        platform_turn=platform_turn,
    ) as headers:
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
        (row,) = mail(client, token, "CHEESE_QUESTION")
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
    return mail(client, _TOKENS[handle], "CHEESE_QUESTION")


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


def test_an_answer_recorded_while_someone_submits_the_form_is_not_erased(
    client, stub_hooks, monkeypatch
):
    """Alice submits the answer form while bob answers the same question by a
    reply. Whichever lands first is its answer; the other is refused or stays
    talk. An answer already recorded on the question is never wiped by the
    form submitted after it."""
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])
    reached, release = asyncio.Event(), asyncio.Event()
    group_of = block_routes.group_of

    async def form_held_once_it_has_read_the_questions(*args, **kwargs):
        rows = await group_of(*args, **kwargs)
        if not reached.is_set():
            reached.set()
            await release.wait()
        return rows

    monkeypatch.setattr(
        block_routes, "group_of", form_held_once_it_has_read_the_questions
    )

    def answers_on_the_question() -> list[dict]:
        (row,) = [b for b in _blocks(client, room) if b["id"] == question["id"]]
        return [
            e for e in row["meta"].get("answer_log") or [] if e.get("kind") != "skipped"
        ]

    async def race():
        form = asyncio.ensure_future(
            asyncio.to_thread(
                client.post,
                f"/blocks/{question['id']}/answers",
                json={"answers": {question["id"]: {"options": ["按项目"]}}},
                headers=session_auth_headers("alice"),
            )
        )
        await asyncio.wait_for(reached.wait(), 10)
        reply = asyncio.ensure_future(
            asyncio.to_thread(
                client.post,
                f"/topics/{room}/messages",
                json={
                    "request_id": str(uuid.uuid4()),
                    "content": "按部门",
                    "reply_to": question["id"],
                },
                headers=session_auth_headers("bob"),
            )
        )
        await asyncio.sleep(1.0)
        recorded_meanwhile = await asyncio.to_thread(answers_on_the_question)
        release.set()
        done = await asyncio.wait_for(
            asyncio.gather(form, reply, return_exceptions=True), 20
        )
        return recorded_meanwhile, done

    recorded_meanwhile, (form, reply) = client.portal.call(race)
    wait_turn_idle(client, room)

    for response in (form, reply):
        assert not isinstance(response, BaseException), repr(response)
        assert response.status_code < 500, response.text
    final = answers_on_the_question()
    assert len(final) == 1, final
    for entry in recorded_meanwhile:
        assert entry in final, (
            f"{entry['by']} 的回答已经记在题上，随后提交的表单把它抹掉了：{final}"
        )


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


def _click(client, room, question_id, handle, text="按部门"):
    """``handle`` clicks an option: a message replying to the question, sent
    the way the browser sends it."""
    response = client.post(
        f"/topics/{room}/messages",
        json={
            "request_id": str(uuid.uuid4()),
            "content": text,
            "reply_to": question_id,
        },
        headers=session_auth_headers(handle),
    )
    wait_turn_idle(client, room)
    return response


def _answer_log(client, room, question_id) -> list[tuple[str, str]]:
    shown = next(b for b in _blocks(client, room) if b["id"] == question_id)
    return [(e["kind"], e["by"]) for e in shown["meta"]["answer_log"]]


def test_a_person_the_question_names_answers_it_by_an_option(
    client, stub_hooks, monkeypatch
):
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])

    response = _click(client, room, question["id"], "bob")

    assert response.status_code == 200, response.text
    assert _answer_log(client, room, question["id"]) == [("option", "bob")]


def test_a_reply_from_someone_the_question_does_not_name_is_talk_not_its_answer(
    client, stub_hooks, monkeypatch
):
    """A teammate quoting the question to say which option he would pick is
    posted like any other message; it just is not the answer."""
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice"])
    prompts = _prompts(stub_hooks, monkeypatch)

    reply = _click(client, room, question["id"], "bob", "我会选按部门")

    assert reply.status_code == 200, reply.text
    said = next(
        b for b in _blocks(client, room) if b["id"] == reply.json()["data"]["id"]
    )
    assert said["content"] == "我会选按部门"
    assert said["reply_to"] == question["id"]
    # Not its answer: nothing on the question, it still waits on alice.
    assert _answer_log(client, room, question["id"]) == []
    assert _waiting_questions(client, "alice") == [question["id"]]
    # The agent that asked is not handed it as an answer.
    assert prompts == []
    # Alice, whom it asks, still answers it.
    assert _click(client, room, question["id"], "alice").status_code == 200
    assert _answer_log(client, room, question["id"]) == [("option", "alice")]
    assert _waiting_questions(client, "alice") == []


def test_a_question_that_names_nobody_is_answered_by_an_option_from_anyone(
    client, stub_hooks, monkeypatch
):
    """Asked in a turn the platform started, it names nobody in particular:
    anyone may answer it by an option, as before addressees existed."""
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, platform_turn=True)
    assert question["meta"]["asked"] == []

    response = _click(client, room, question["id"], "bob")

    assert response.status_code == 200, response.text
    assert _answer_log(client, room, question["id"]) == [("option", "bob")]


def test_a_question_asked_of_everyone_is_open_to_whoever_is_in_the_channel(
    client, stub_hooks, monkeypatch
):
    """Including someone who joined after it was asked: it asks the channel,
    not the list of who was there at that moment."""
    pid, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["all"])
    seed_user(client, "carol")
    join_project_team(client, pid, "carol")
    assert "carol" not in question["meta"]["asked"]

    assert _skip(client, question["id"], by="carol").status_code == 200
    assert _undo(client, question["id"], by="carol").status_code == 200
    response = _click(client, room, question["id"], "carol")

    assert response.status_code == 200, response.text
    assert _answer_log(client, room, question["id"]) == [("option", "carol")]
