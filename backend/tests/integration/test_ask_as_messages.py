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

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent.repositories import AgentTurnRepository
from app.main import app
from tests.ask_fixtures import active_ask, question_row, wait_turn_idle
from tests.conftest import StubChannel, seed_user, stub_compute
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    join_project_team,
    post_message,
    post_project,
    room_agent_headers,
    room_agent_seat,
    session_auth_headers,
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


#: The channel each test's 支线 hangs in, for what only a channel answers (its roster).
_CHANNEL: dict[str, str] = {}


def _room(client) -> tuple[str, str]:
    """A project and a 支线 of its channel: where 芝士 answers once it is called,
    and so where it asks."""
    seed_user(client, "alice")
    data = post_project(client, {"name": "Ask"}, owner="alice").json()["data"]
    thread = in_thread(client, data["root_topic_id"], "alice")
    _CHANNEL[thread] = data["root_topic_id"]
    return data["id"], thread


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
                    .where(AgentTurn.conversation_id == uuid.UUID(room))
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
                conversation_id=uuid.UUID(room),
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
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(after.root),
        compute=stub_compute(after),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    prompts = _prompts(after, monkeypatch)

    _say(client, room, "alice", {"content": "按项目", "reply_to": question["id"]})

    assert len(prompts) == 1 and "按项目" in prompts[0]


def _teammate(client, pid, room) -> str:
    """A second AI teammate, seated in ``room``."""
    made = client.post(f"/projects/{pid}/agents", json={"handle": "opus"})
    assert made.status_code == 200, made.text
    seat = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{_CHANNEL.get(room, room)}/members",
        json={"handle": seat, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    return seat


def test_a_question_whose_options_are_plain_strings_never_breaks_posting(
    client, stub_hooks, monkeypatch
):
    """A person's question from before options carried explanations stores them
    as plain strings; talking in that room, or replying to it, still works."""
    pid, room = _room(client)
    seed_user(client, "bob")
    join_project_team(client, pid, "bob")
    old = question_row(
        client,
        room,
        author="alice",
        question="周会挪到周四行吗",
        options=["行", "不行"],
    )
    _prompts(stub_hooks, monkeypatch)

    _say(client, room, "bob", {"content": "我先看看日程"})
    _say(client, room, "bob", {"content": "行", "reply_to": old["id"]})

    (answer,) = _block(client, room, old["id"])["meta"]["answer_log"]
    assert (answer["by"], answer["kind"], answer["option"]) == ("bob", "option", "行")


def test_a_typed_reply_goes_to_the_teammate_who_asked_last_only(
    client, stub_hooks, monkeypatch
):
    pid, room = _room(client)
    first = room_agent_seat(client, room)
    second = _teammate(client, pid, room)
    earlier = question_row(
        client, room, seat=first, question="用哪份数据？", asked="alice"
    )
    later = question_row(
        client, room, seat=second, question="图用什么颜色？", asked="alice"
    )
    agents: list = []
    emit = stub_hooks.emit_turn

    def recording(topic_id, prompt, reply, *, agent=None):
        agents.append(agent)
        emit(topic_id, prompt, reply, agent=agent)

    monkeypatch.setattr(stub_hooks, "emit_turn", recording)

    _say(client, room, "alice", {"content": "蓝色"})

    assert _block(client, room, earlier["id"])["meta"]["answer_log"] == []
    (answer,) = _block(client, room, later["id"])["meta"]["answer_log"]
    assert answer["note"] == "蓝色"
    assert agents == [second]


def test_people_talking_is_no_answer_to_a_question_that_waits_on_nobody(
    client, stub_hooks, monkeypatch
):
    """A question asked in a turn the platform started waits on nobody in
    particular. A message that names nobody is people talking, not its answer;
    one addressed to the asker is."""
    _, room = _room(client)
    seat = room_agent_seat(client, room)
    question = question_row(client, room, question="周报发给谁？", asked=None)
    prompts = _prompts(stub_hooks, monkeypatch)

    _say(client, room, "alice", {"content": "今天谁值班？"})
    assert prompts == []
    assert _block(client, room, question["id"])["meta"]["answer_log"] == []

    _say(client, room, "alice", {"content": f"<@{seat}> 发给全组"})
    assert len(prompts) == 1
    (answer,) = _block(client, room, question["id"])["meta"]["answer_log"]
    assert answer["note"] == "发给全组"


def test_the_notice_stays_open_until_every_question_of_the_call_is_answered(
    client, stub_hooks, monkeypatch
):
    token = seed_user(client, "alice")
    data = post_project(client, {"name": "Ask"}, owner="alice").json()["data"]
    room = in_thread(client, data["root_topic_id"], "alice")
    two = {
        "questions": [
            {"question": "口径？", "options": [{"text": "按部门"}, {"text": "按项目"}]},
            {"question": "格式？", "options": [{"text": "表格"}, {"text": "图"}]},
        ]
    }
    with active_ask(client, stub_hooks, monkeypatch, room, actor="alice") as headers:
        response = client.post(f"/topics/{room}/asks", json=two, headers=headers)
        assert response.status_code == 200, response.text
    wait_turn_idle(client, room)
    first, second = response.json()["data"]["blocks"]

    def notice() -> dict:
        r = client.get(
            "/notifications",
            params={"type": "CHEESE_QUESTION"},
            headers={"Authorization": f"Bearer {token}"},
        )
        (row,) = r.json()["data"]["notifications"]
        return row

    _say(client, room, "alice", {"content": "图", "reply_to": second["id"]})
    assert notice()["read"] is False
    _say(client, room, "alice", {"content": "按部门", "reply_to": first["id"]})
    assert notice()["read"] is True
