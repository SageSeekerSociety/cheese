"""Ask consumer fixtures, with explicit creation and legacy evidence boundaries.

New creation uses a held scripted native runner and the actual origin reader:
a person's message admitting a turn (``actor``), or the turn a platform wake-up
starts (``platform_turn=True``, whose author is ``system`` — a question asked
inside it addresses nobody). Legacy non-group rows are persisted fixtures, not
requests to the retired scalar creation API. Neither fixture proves an external
native process executed a tool.
"""

import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from app.api.deps import get_chat_service, get_work_runner
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.runtime import addressed_to_agent
from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.identity.handles import looks_like_agent_handle, names_a_person
from tests.conftest import settle_turn
from tests.integration.conftest import chat_ws_url, post_message, room_agent_seat


def agent_credential(project, topic, seat) -> dict[str, str]:
    """The scoped token this room's seat presents to its own question routes."""
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project, topic_id=topic, agent_handle=seat
        )
    }


@contextmanager
def active_ask(
    client,
    stub_hooks,
    monkeypatch,
    topic,
    *,
    actor="user-1",
    seat=None,
    platform_turn=False,
):
    """Hold one admitted input until the caller finishes creating its questions.

    ``platform_turn=True`` starts the turn the platform's own wake-ups start
    (``author="system"``) instead of one a person's message admits: the
    difference the question's addressee turns on.
    """
    seat = seat or room_agent_seat(client, topic)
    project = client.get(f"/topics/{topic}").json()["data"]["project_id"]
    started = threading.Event()

    def held(tid, prompt, reply, *, agent=None):
        stub_hooks.starts(tid, agent=agent)
        stub_hooks.acknowledges(tid, prompt, agent=agent)
        stub_hooks.says(tid, "我会在这一轮提出问题。", agent=agent)
        started.set()

    def stop() -> None:
        stub_hooks.stops(uuid.UUID(str(topic)), "提问完成", agent=seat)

    with monkeypatch.context() as patch:
        patch.setattr(stub_hooks, "emit_turn", held)
        if platform_turn:
            chat = client.app.dependency_overrides[get_chat_service]()
            client.portal.call(
                lambda: get_work_runner().submit(
                    chat,
                    uuid.UUID(str(topic)),
                    author="system",
                    content="接着干",
                    addressed=addressed_to_agent(seat),
                )
            )
            assert started.wait(5), "the platform turn never reached its runner"
            try:
                yield agent_credential(project, topic, seat)
            finally:
                stop()
                client.portal.call(lambda: settle_turn(chat, uuid.UUID(str(topic))))
            return
        with client.websocket_connect(chat_ws_url(topic, actor)) as ws:
            post_message(client, topic, actor, {"content": f"<@{seat}> 等我提问"})
            assert started.wait(5), "the admitted runner never received its input"
            try:
                yield agent_credential(project, topic, seat)
            finally:
                stop()
                while True:
                    frame = ws.receive_json()
                    if frame["type"] in ("done", "error"):
                        assert frame["type"] == "done", frame
                        break


def wait_turn_idle(client, topic) -> None:
    """Wait out whatever turn is running on this room — e.g. the wake an answer
    sends the agent. Needed before the next ``active_ask``: a turn still in
    flight is a second live origin, and asking then is refused rather than
    guessed at.
    """
    chat = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(lambda: settle_turn(chat, uuid.UUID(str(topic))))


def legacy_question(
    client,
    topic,
    *,
    seat=None,
    author=None,
    turn=None,
    native_session_id=None,
    harness="fixture",
    **extra,
):
    """Persist an existing non-group question for the retained answer endpoint.

    ``author`` is who signed it: the seat (default) is the agent's own question,
    a handle like ``"alice"`` the shape a person's question kept after the
    migration left human history alone. ``asked`` and any other meta key can be
    passed through ``extra``.

    The question was asked inside a real turn, and the row says so. ``turn``
    names the interval it was asked in — the recovery case passes the work that
    actually ran — and without one this fixture opens a turn and ends it. Either
    way ``ask_origin`` points at that row and ``asked`` is read back off its
    author the way the create route reads it, so the block cannot claim a
    question no turn ever asked.
    """
    seat = seat or room_agent_seat(client, topic)
    author = author or seat
    project = client.get(f"/topics/{topic}").json()["data"]["project_id"]
    question = extra.pop("question", "分页方案选哪个？")
    said = extra.pop("asked", None)
    meta = {
        "options": [{"text": "cursor"}, {"text": "pageStart"}],
        "allow_other": True,
        "reject_option": True,
        "answer_log": [],
        **extra,
    }

    async def persist():
        async with client.test_request_factory() as session:
            turns = AgentTurnRepository(session)
            work = uuid.UUID(str(turn)) if turn is not None else uuid.uuid4()
            opened = await turns.get(work)
            if turn is not None:
                assert opened is not None, f"{work} 不在 agent_turns 里"
                session_id = native_session_id or opened.session_id
            else:
                session_id = native_session_id or "fixture-native-session"
                await turns.open(
                    turn_id=work,
                    topic_id=uuid.UUID(str(topic)),
                    continuation_id=work,
                    # The person who asked is the turn's author, which is what
                    # makes ``asked`` below a read of the row and not a guess.
                    author=said or author,
                    content=question,
                    is_resume=False,
                    resendable=True,
                    started_at=datetime.now(UTC),
                    agent_handle=seat,
                    session_id=session_id,
                )
                # An existing question's asking turn is over. A caller passing
                # its own ``turn`` owns that row's state and it is left alone.
                await turns.close([work], datetime.now(UTC))
                opened = await turns.get(work)
            asked = opened.author if names_a_person(opened.author) else None
            assert said in (None, asked), (said, asked)
            meta["asked"] = asked
            meta["ask_origin"] = {
                "harness": harness,
                "native_session_id": session_id,
                "work_id": str(work),
                "recipient_handle": seat,
                "asked_by": author,
                "asked": asked,
                "task_id": str(opened.task_id) if opened.task_id else None,
            }
            block = await BlockRepository(session).add(
                project_id=uuid.UUID(str(project)),
                topic_id=uuid.UUID(str(topic)),
                task_id=opened.task_id,
                author=author,
                author_type=AuthorType.participant,
                content=question,
                turn_id=work,
                meta=meta,
                # It is the agent's own output only when the agent signed it —
                # a person's question is input the agent still has to read.
                own_output=looks_like_agent_handle(author),
            )
            result = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
            return result

    return client.portal.call(persist)
