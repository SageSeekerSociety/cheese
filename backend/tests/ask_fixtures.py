"""Ask consumer fixtures, with explicit creation and legacy evidence boundaries.

New creation uses a held scripted native runner and the actual origin reader.
Legacy non-group rows are persisted fixtures, not requests to the retired scalar
creation API. Neither fixture proves an external native process executed a tool.
"""

import threading
import uuid
from contextlib import contextmanager

from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from tests.integration.conftest import chat_ws_url, post_message, room_agent_seat


@contextmanager
def active_ask(client, stub_hooks, monkeypatch, topic, *, actor="user-1", seat=None):
    """Hold one admitted input until the caller finishes creating its questions."""
    seat = seat or room_agent_seat(client, topic)
    project = client.get(f"/topics/{topic}").json()["data"]["project_id"]
    started = threading.Event()

    def held(tid, prompt, reply, *, agent=None):
        stub_hooks.starts(tid, agent=agent)
        stub_hooks.acknowledges(tid, prompt, agent=agent)
        stub_hooks.says(tid, "我会在这一轮提出问题。", agent=agent)
        started.set()

    with monkeypatch.context() as patch:
        patch.setattr(stub_hooks, "emit_turn", held)
        with client.websocket_connect(chat_ws_url(topic, actor)) as ws:
            post_message(client, topic, actor, {"content": f"<@{seat}> 等我提问"})
            assert started.wait(5), "the admitted runner never received its input"
            try:
                yield {
                    "X-Cheese-Token": mint_scoped_token(
                        project_id=project, topic_id=topic, agent_handle=seat
                    )
                }
            finally:
                stub_hooks.stops(uuid.UUID(topic), "提问完成", agent=seat)
                while True:
                    frame = ws.receive_json()
                    if frame["type"] in ("done", "error"):
                        assert frame["type"] == "done", frame
                        break


def legacy_question(client, topic, *, seat=None, **extra):
    """Persist an existing non-group question for the retained answer endpoint."""
    seat = seat or room_agent_seat(client, topic)
    project = client.get(f"/topics/{topic}").json()["data"]["project_id"]
    question = extra.pop("question", "分页方案选哪个？")
    meta = {
        "options": [{"text": "cursor"}, {"text": "pageStart"}],
        "allow_other": True,
        "reject_option": True,
        "answer_log": [],
        **extra,
    }

    async def persist():
        async with client.test_request_factory() as session:
            block = await BlockRepository(session).add(
                project_id=uuid.UUID(project),
                topic_id=uuid.UUID(topic),
                author=seat,
                author_type=AuthorType.participant,
                content=question,
                meta=meta,
                own_output=True,
            )
            result = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
            return result

    return client.portal.call(persist)
