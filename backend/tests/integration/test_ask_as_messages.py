"""`cheese_ask`: a question is a message with quick replies, and nothing waits on it.

The agent posts its question and ends its turn. A person answers by clicking an
option — the browser sends the option's text as a reply to the question — or by
typing; either way it is an ordinary message, and that message starts the
agent's next turn. No turn is held open across the wait, so neither a backend
handover nor a conversation that ended in between can stand between the answer
and the agent.

Real HTTP, PostgreSQL and the room's runner; the session is the scripted stub.
"""

import threading
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent.repositories import AgentTurnRepository
from app.main import app
from tests.ask_fixtures import active_ask, wait_turn_idle
from tests.conftest import StubChannel, seed_user, stub_compute
from tests.integration.conftest import (
    chat_ws_url,
    join_project_team,
    post_message,
    post_project,
    room_agent_headers,
    room_agent_seat,
)

QUESTION = {
    "questions": [
        {
            "question": "预算按哪个口径统计？",
            "options": [
                {"text": "按部门", "explain": "和去年的报表对得上"},
                {"text": "按项目"},
            ],
        }
    ]
}


def _room(client) -> tuple[str, str]:
    seed_user(client, "alice")
    data = post_project(client, {"name": "Ask"}, owner="alice").json()["data"]
    return data["id"], data["root_topic_id"]


def _ask_in_a_turn(client, stub_hooks, monkeypatch, room, *, by="alice") -> dict:
    """The agent asks inside the turn ``by``'s message started, and the turn ends."""
    with active_ask(client, stub_hooks, monkeypatch, room, actor=by) as headers:
        response = client.post(f"/topics/{room}/asks", json=QUESTION, headers=headers)
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


def _say(client, room, handle, body) -> dict:
    """``handle`` sends a message and the turn it starts, if any, runs out."""
    with client.websocket_connect(chat_ws_url(room, handle)) as ws:
        sent = post_message(client, room, handle, body)
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    wait_turn_idle(client, room)
    return sent


def _block(client, room, block_id) -> dict:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return next(block for block in blocks if block["id"] == block_id)


def _turns(client, room) -> list[AgentTurn]:
    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(AgentTurn)
                    .where(AgentTurn.topic_id == uuid.UUID(room))
                    .order_by(AgentTurn.started_at)
                )
            )

    return client.portal.call(read)


def test_a_question_is_an_ordinary_message_carrying_its_options(
    client, stub_hooks, monkeypatch
):
    _, room = _room(client)
    seat = room_agent_seat(client, room)

    question = _ask_in_a_turn(client, stub_hooks, monkeypatch, room)

    shown = _block(client, room, question["id"])
    assert shown["kind"] == "message"
    assert shown["author"] == seat
    assert shown["content"] == "预算按哪个口径统计？"
    assert [option["text"] for option in shown["meta"]["options"]] == [
        "按部门",
        "按项目",
    ]
    assert shown["meta"]["asked"] == "alice"
    assert shown["meta"]["answer_log"] == []
    # The turn that asked is over: the question waits on its own.
    assert all(turn.stopped_at is not None for turn in _turns(client, room))


def test_clicking_an_option_starts_the_agents_next_turn_with_that_answer(
    client, stub_hooks, monkeypatch
):
    _, room = _room(client)
    question = _ask_in_a_turn(client, stub_hooks, monkeypatch, room)
    asked_in = {turn.id for turn in _turns(client, room)}
    prompts = _prompts(stub_hooks, monkeypatch)

    reply = _say(
        client, room, "alice", {"content": "按部门", "reply_to": question["id"]}
    )

    assert len(prompts) == 1 and "按部门" in prompts[0]
    (next_turn,) = [turn for turn in _turns(client, room) if turn.id not in asked_in]
    assert next_turn.author == "alice" and next_turn.stopped_at is not None
    (answer,) = _block(client, room, question["id"])["meta"]["answer_log"]
    assert (answer["by"], answer["kind"], answer["option"], answer["reply_id"]) == (
        "alice",
        "option",
        "按部门",
        reply["id"],
    )


def test_typing_a_reply_instead_starts_the_next_turn_too(
    client, stub_hooks, monkeypatch
):
    _, room = _room(client)
    question = _ask_in_a_turn(client, stub_hooks, monkeypatch, room)
    prompts = _prompts(stub_hooks, monkeypatch)

    _say(client, room, "alice", {"content": "都不是，按季度统计"})

    assert len(prompts) == 1 and "按季度统计" in prompts[0]
    (answer,) = _block(client, room, question["id"])["meta"]["answer_log"]
    assert (answer["by"], answer["kind"], answer["note"]) == (
        "alice",
        "note",
        "都不是，按季度统计",
    )


def test_talk_to_someone_else_while_a_question_waits_is_not_its_answer(
    client, stub_hooks, monkeypatch
):
    """The input box stays an input box: what is said to a person is said to them."""
    pid, room = _room(client)
    seed_user(client, "bob")
    join_project_team(client, pid, "bob")
    question = _ask_in_a_turn(client, stub_hooks, monkeypatch, room)
    prompts = _prompts(stub_hooks, monkeypatch)

    _say(client, room, "alice", {"content": "<@bob> 你那边按什么口径？"})

    assert prompts == []
    assert _block(client, room, question["id"])["meta"]["answer_log"] == []


def test_each_person_who_answers_reaches_the_agent(client, stub_hooks, monkeypatch):
    pid, room = _room(client)
    seed_user(client, "bob")
    join_project_team(client, pid, "bob")
    question = _ask_in_a_turn(client, stub_hooks, monkeypatch, room)
    prompts = _prompts(stub_hooks, monkeypatch)

    _say(client, room, "alice", {"content": "按部门", "reply_to": question["id"]})
    _say(client, room, "bob", {"content": "按项目", "reply_to": question["id"]})

    answers = _block(client, room, question["id"])["meta"]["answer_log"]
    assert [(a["by"], a["option"]) for a in answers] == [
        ("alice", "按部门"),
        ("bob", "按项目"),
    ]
    assert "按部门" in "\n".join(prompts) and "按项目" in "\n".join(prompts)


def test_asking_needs_no_turn_this_backend_is_holding(client):
    """The handover window that used to refuse a question with 403.

    The incoming backend serves requests before it has taken the running work
    over: it knows nothing yet of the turn still running elsewhere, which only
    the database records. Asking reads that record, and answers at once.
    """
    pid, room = _room(client)
    seat = room_agent_seat(client, room)
    turn = uuid.uuid4()

    async def running_elsewhere():
        async with client.test_factory() as session:
            await AgentTurnRepository(session).open(
                turn_id=turn,
                topic_id=uuid.UUID(room),
                continuation_id=turn,
                author="alice",
                content="核一下预算",
                is_resume=False,
                resendable=True,
                started_at=datetime.now(UTC),
                agent_handle=seat,
            )
            await session.commit()

    client.portal.call(running_elsewhere)
    runner = get_work_runner()
    runner.hold_turns()
    answered: dict = {}
    try:
        asking = threading.Thread(
            target=lambda: answered.setdefault(
                "response",
                client.post(
                    f"/topics/{room}/asks",
                    json=QUESTION,
                    headers=room_agent_headers(client, room),
                ),
            )
        )
        asking.start()
        asking.join(5)
    finally:
        runner.start_turns()
    assert "response" in answered, "asking waited for the takeover"
    response = answered["response"]
    assert response.status_code == 200, response.text
    (question,) = response.json()["data"]["blocks"]
    assert question["meta"]["asked"] == "alice"


def test_an_answer_reaches_the_agent_after_the_conversation_that_asked_is_gone(
    client, stub_hooks, monkeypatch
):
    """The answer is a message, not an input for one conversation: the agent's next
    turn runs wherever its next turn would, here a backend that never saw the
    session the question was asked in."""
    _, room = _room(client)
    question = _ask_in_a_turn(client, stub_hooks, monkeypatch, room)
    after = StubChannel()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(after.root),
        compute=stub_compute(after),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    prompts = _prompts(after, monkeypatch)

    _say(client, room, "alice", {"content": "按项目", "reply_to": question["id"]})

    assert len(prompts) == 1 and "按项目" in prompts[0]
