"""Question fixtures: asking from inside a real turn, or a persisted question row.

``active_ask`` holds a scripted turn open while the caller asks: a person's
message admitting it (``actor``), or the turn a platform wake-up starts
(``platform_turn=True``, whose author is ``system`` — a question asked inside it
waits on nobody in particular). ``question_row`` persists a question without
any turn. Neither proves an external native process executed a tool.

``while_held`` races a write to a question against an answer given meanwhile.
"""

import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from app.api.deps import get_chat_service, get_work_runner
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.runtime import addressed_to_agent
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.event_author import AuthorType
from app.domain.identity.handles import looks_like_agent_handle
from tests.conftest import settle_turn
from tests.integration.conftest import post_message, room_agent_seat, room_socket


def agent_credential(project, topic, seat) -> dict[str, str]:
    """The scoped token this room's seat presents to its own question routes."""
    return {
        "Authorization": f"Bearer {
            mint_scoped_token(project_id=project, topic_id=topic, agent_handle=seat)
        }"
    }


def conversation_project(client, conversation) -> str:
    """The project of the conversation a seat works in.

    A room is read as `/topics/{id}`; a task is NOT (`GET /topics/{task}`
    deliberately 404s — a task is read as `/topics/{task}/task`). A question
    asked inside a task is the same shape of thing as one asked in a room, so
    the fixtures here have to reach either.
    """
    r = client.get(f"/topics/{conversation}")
    if r.status_code == 200:
        return r.json()["data"]["project_id"]
    r = client.get(f"/topics/{conversation}/task")
    assert r.status_code == 200, r.text
    return r.json()["data"]["project_id"]


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
    project = conversation_project(client, topic)
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
        with room_socket(client, topic, actor) as ws:
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
    project = conversation_project(client, topic)
    question = extra.pop("question", "分页方案选哪个？")
    meta = {
        "options": [{"text": "cursor"}, {"text": "pageStart"}],
        "asked": [],
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


def while_held(monkeypatch, module, name, write, meanwhile, *, read=None, wait_s=1.0):
    """Run ``write`` until its first call of ``module.name`` and hold it there;
    run ``meanwhile`` beside it, wait ``wait_s``, ``read()`` if given, then let
    ``write`` go on. Returns what ``read()`` saw while it was held (None without
    one), and both results.

    ``module.name`` is the step that writes the question, so ``write`` is held
    after whatever it read first: a ``meanwhile`` that lands during the hold
    shows in that ``read()``, and one that waits for ``write`` does not."""
    reached, release = threading.Event(), threading.Event()
    original = getattr(module, name)

    async def held(*args, **kwargs):
        if not reached.is_set():
            reached.set()
            await asyncio.to_thread(release.wait, 20)
        return await original(*args, **kwargs)

    monkeypatch.setattr(module, name, held)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(write)
        try:
            assert reached.wait(10), "the write never reached the step it is held at"
            second = pool.submit(meanwhile)
            time.sleep(wait_s)
            seen = read() if read is not None else None
        finally:
            release.set()
        return seen, first.result(20), second.result(20)
