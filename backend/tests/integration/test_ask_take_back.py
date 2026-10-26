"""Taking a question back: the asking agent, and the clock.

A question 芝士 asks waits for a person to answer it (`block.asked`). It also
waits for nobody at all once it is clear nobody is going to:

- the agent that asked takes its own question back before anyone answers
  (``DELETE /blocks/{id}/question``, `block.questions.withdraw_question`);
- its deadline passes with no answer (`agent.ask.expire_due_questions`, run on
  a clock) and the platform does the same.

Both settle it — it leaves the 待你回答 lists, its notice is settled and the
channel's yellow light goes out — without anybody answering it. That is the
whole point: a stale question otherwise sits in a person's list forever, which
is how the yellow light came to be permanently on for one channel.

Neither is an answer (``block.asked.answered`` stays false), and an expired
question may still be answered afterwards, so the card is free to keep its
field. The message the question is stays in the room untouched either way.

Real HTTP, PostgreSQL and the room's runner; the session is the scripted stub.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.block.models import Block
from tests.ask_fixtures import active_ask, wait_turn_idle
from tests.conftest import seed_user, wait_work_idle
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    mail,
    post_project,
    room_agent_headers,
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
    data = post_project(client, {"name": "Take back"}, owner="alice").json()["data"]
    return data["id"], in_thread(client, data["root_topic_id"], "alice"), token


def _ask(client, stub_hooks, monkeypatch, room, *, to=None, lifetime_hours=None):
    body = QUESTION if to is None else {**QUESTION, "to": to}
    if lifetime_hours is not None:
        body = {**body, "lifetime_hours": lifetime_hours}
    with active_ask(
        client, stub_hooks, monkeypatch, room, actor="alice", platform_turn=False
    ) as headers:
        response = client.post(f"/topics/{room}/asks", json=body, headers=headers)
        assert response.status_code == 200, response.text
    wait_turn_idle(client, room)
    (question,) = response.json()["data"]["blocks"]
    return question


def _blocks(client, room) -> list[dict]:
    return client.get(f"/topics/{room}/blocks").json()["data"]["data"]


def _waiting_questions(client, handle) -> list[str]:
    r = client.get("/awaiting-me", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return [item["blockId"] for item in r.json()["data"]["data"] if item["blockId"]]


def _its_author(client, room, question_id) -> dict[str, str]:
    """The credential of whoever wrote the question — the agent's seat here."""
    return room_agent_headers(client, room)


def _question_out(client, room, question_id) -> dict:
    return next(b for b in _blocks(client, room) if b["id"] == question_id)


def _answer_log(client, room, question_id) -> list[tuple[str, str]]:
    shown = _question_out(client, room, question_id)
    return [(e["kind"], e["by"]) for e in shown["meta"]["answer_log"]]


def test_the_agent_takes_its_own_question_back_and_it_leaves_the_person_s_list(
    client, stub_hooks, monkeypatch
):
    """The whole point: a question nobody answered stops waiting, on its own."""
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    assert _waiting_questions(client, "alice") == [question["id"]]
    before = len(_blocks(client, room))

    response = client.delete(
        f"/blocks/{question['id']}/question",
        headers=_its_author(client, room, question["id"]),
    )

    assert response.status_code == 200, response.text
    assert _waiting_questions(client, "alice") == []
    assert _answer_log(client, room, question["id"]) == [
        ("withdrawn", _seat(client, room))
    ]
    # Nothing new was said in the room: the question's own message is what it was.
    assert len(_blocks(client, room)) == before
    assert _question_out(client, room, question["id"])["content"] == question["content"]


def _seat(client, room) -> str:
    from tests.integration.conftest import room_agent_seat

    return room_agent_seat(client, room)


def test_taking_a_question_back_settles_its_notice(client, stub_hooks, monkeypatch):
    """Its line in 待办 stops saying the question is waiting on the person."""
    _, room, token = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)

    def notice() -> dict:
        (row,) = mail(client, token, "CHEESE_QUESTION")
        return row

    assert notice()["read"] is False
    client.delete(
        f"/blocks/{question['id']}/question",
        headers=_its_author(client, room, question["id"]),
    )
    assert notice()["read"] is True


def test_a_person_cannot_take_the_agent_s_question_back(
    client, stub_hooks, monkeypatch
):
    """Only the teammate that asked it can: anyone else is refused by name."""
    pid, room, _ = _room(client)
    seed_user(client, "bob")
    join_project_team(client, pid, "bob")
    question = _ask(client, stub_hooks, monkeypatch, room)
    prompts = _prompts(stub_hooks, monkeypatch)

    refused = client.delete(
        f"/blocks/{question['id']}/question", headers=session_auth_headers("bob")
    )

    assert refused.status_code == 403, refused.text
    assert _waiting_questions(client, "alice") == [question["id"]]
    assert prompts == []


def _prompts(stub_hooks, monkeypatch) -> list[str]:
    seen: list[str] = []
    emit = stub_hooks.emit_turn

    def recording(topic_id, prompt, reply, *, agent=None):
        seen.append(prompt)
        emit(topic_id, prompt, reply, agent=agent)

    monkeypatch.setattr(stub_hooks, "emit_turn", recording)
    return seen


def test_an_answered_question_cannot_be_taken_back_by_its_asker(
    client, stub_hooks, monkeypatch
):
    """The answer belongs to whoever gave it; that route is theirs, not the asker's."""
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])
    _click(client, room, question["id"], "bob")

    refused = client.delete(
        f"/blocks/{question['id']}/question",
        headers=_its_author(client, room, question["id"]),
    )

    assert refused.status_code == 409, refused.text


def test_taking_a_question_back_does_not_run_anyone_s_turn(
    client, stub_hooks, monkeypatch
):
    """The asker is the one acting — nobody has to be told, so nothing is spent."""
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    prompts = _prompts(stub_hooks, monkeypatch)

    client.delete(
        f"/blocks/{question['id']}/question",
        headers=_its_author(client, room, question["id"]),
    )
    wait_work_idle()

    assert prompts == []


# ---- the clock ----


async def _sweep(*, days_ago: float | None = None) -> int:
    """Run the expiry sweep over the client's own database.

    ``days_ago`` first drags every open question's deadline that far into the
    past — the sweep is what is under test, and this is how a 72-hour wait is
    tested in a second. A NullPool engine of its own (the shape
    `test_a_recorded_decision_reads_as_a_message` uses for the same reason):
    the ``client`` fixture binds the app to ``TEST_DATABASE_URL``, and its
    requests run on their own loop.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.domain.agent.periodic import expire_due_questions
    from tests.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            if days_ago is not None:
                rows = list(
                    await session.scalars(
                        select(Block).where(
                            Block.meta["deadline"].as_string().is_not(None)
                        )
                    )
                )
                for row in rows:
                    meta = dict(row.meta or {})
                    meta["deadline"] = (
                        datetime.now(UTC) - timedelta(days=days_ago)
                    ).isoformat()
                    row.meta = meta
                await session.commit()
            taken = await expire_due_questions(session, now=datetime.now(UTC))
            await session.commit()
        return taken
    finally:
        await engine.dispose()


def _expire(*, days_ago: float = 1.0) -> int:
    import asyncio

    return asyncio.run(_sweep(days_ago=days_ago))


def test_a_question_past_its_deadline_leaves_the_list_on_its_own(
    client, stub_hooks, monkeypatch
):
    """No person and no agent touches it: the clock settles it."""
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    assert _waiting_questions(client, "alice") == [question["id"]]

    _expire()
    wait_work_idle()

    assert _waiting_questions(client, "alice") == []
    assert _answer_log(client, room, question["id"]) == [
        ("expired", _seat(client, room))
    ]


def test_an_unexpired_question_stays(client, stub_hooks, monkeypatch):
    """The deadline that has not passed settles nothing."""
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room, lifetime_hours=72)

    import asyncio

    assert asyncio.run(_sweep()) == 0
    assert _waiting_questions(client, "alice") == [question["id"]]


def test_a_question_with_no_deadline_never_expires(client, stub_hooks, monkeypatch):
    """``lifetime_hours: 0`` is "wait however long it takes"."""
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room, lifetime_hours=0)

    _expire()
    wait_work_idle()

    assert _waiting_questions(client, "alice") == [question["id"]]


def test_an_expired_question_can_still_be_answered(client, stub_hooks, monkeypatch):
    """Expiry is not a refusal: the card keeps its field and the answer lands."""
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])
    _expire()
    wait_work_idle()

    response = _click(client, room, question["id"], "bob")

    assert response.status_code == 200, response.text
    assert ("option", "bob") in _answer_log(client, room, question["id"])


def test_a_question_taken_back_can_no_longer_be_answered(
    client, stub_hooks, monkeypatch
):
    """The asker closed it: the reply is talk in the room, not an answer."""
    _, room = _with_bob(client)
    question = _ask(client, stub_hooks, monkeypatch, room, to=["alice", "bob"])
    client.delete(
        f"/blocks/{question['id']}/question",
        headers=_its_author(client, room, question["id"]),
    )

    response = _click(client, room, question["id"], "bob")

    assert response.status_code == 200, response.text
    assert _answer_log(client, room, question["id"]) == [
        ("withdrawn", _seat(client, room))
    ]


def _with_bob(client) -> tuple[str, str]:
    pid, room, _ = _room(client)
    seed_user(client, "bob")
    join_project_team(client, pid, "bob")
    return pid, room


def _click(client, room, question_id, who) -> object:
    """Answer a question by clicking one of its options — what the card sends:
    a message replying to the question with the option's text."""
    import uuid

    option = next(
        o["text"] for o in _question_out(client, room, question_id)["meta"]["options"]
    )
    response = client.post(
        f"/topics/{room}/messages",
        json={
            "request_id": str(uuid.uuid4()),
            "content": option,
            "reply_to": question_id,
        },
        headers=session_auth_headers(who),
    )
    wait_work_idle()
    return response
