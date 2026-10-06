"""Question fixtures: asking from inside a real turn, or a persisted question row.

``active_ask`` holds a scripted turn open while the caller asks: a person's
message admitting it (``actor``), or the turn a platform wake-up starts
(``platform_turn=True``, whose author is ``system`` — a question asked inside it
waits on nobody in particular). ``question_row`` persists a question without
any turn. Neither proves an external native process executed a tool.
"""

import threading
import uuid
from contextlib import contextmanager

from app.api.deps import get_chat_service, get_work_runner
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.runtime import addressed_to_agent
from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.identity.handles import looks_like_agent_handle
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
    """Wait out whatever turn is running on this room — e.g. the one an answer
    starts — before the test looks at what it left behind."""
    chat = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(lambda: settle_turn(chat, uuid.UUID(str(topic))))


def question_row(client, topic, *, seat=None, author=None, **extra):
    """Persist a question block of the shape `cheese_ask` writes, without a turn.

    ``author`` is who signed it: the seat (default) is the agent's own question,
    a handle like ``"alice"`` the shape a person's historical question kept.
    ``asked``, ``options`` and any other meta key can be passed through ``extra``.
    """
    seat = seat or room_agent_seat(client, topic)
    author = author or seat
    project = client.get(f"/topics/{topic}").json()["data"]["project_id"]
    question = extra.pop("question", "分页方案选哪个？")
    meta = {
        "options": [{"text": "cursor"}, {"text": "pageStart"}],
        "asked": None,
        "answer_log": [],
        **extra,
    }

    async def persist():
        async with client.test_request_factory() as session:
            block = await BlockRepository(session).add(
                project_id=uuid.UUID(str(project)),
                conversation_id=uuid.UUID(str(topic)),
                author=author,
                author_type=AuthorType.participant,
                content=question,
                meta=meta,
                # It is the agent's own output only when the agent signed it —
                # a person's question is input the agent still has to read.
                own_output=looks_like_agent_handle(author),
            )
            result = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
            return result

    return client.portal.call(persist)
