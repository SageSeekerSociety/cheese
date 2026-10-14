"""A turn running on one backend is a running turn on the others.

Two ``ChatService`` objects share one database and nothing else — no lock, no
cache, exactly as two backend processes do. The authority on 「这一轮在跑」 is
the open ``AgentTurn`` row, so the second service must recognise the first's
turn from the row alone: the board reads the task as running, a message meant
for it is merged into that turn rather than opening a second one beside it, and
once the row closes the second service stops counting it. The old process-local
``hook_work`` could see none of that — the row is what carries 「在跑」 across
processes.

The row here is a REAL one, opened through the turn lifecycle with its own
author and content — the marks only confirm it (they invent nothing), which is
the whole point at `_prompt_lock` where the row does not exist yet.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.models import AgentTurn
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.delivery.input_identity import InputIdentity, InputReceipt
from app.domain.delivery.models import NativeInput
from app.main import app
from tests.conftest import stub_compute
from tests.delivery import delivery_headers, delivery_task
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)


@pytest.fixture(autouse=True)
def _restore_chat_service():
    yield
    app.dependency_overrides.pop(get_chat_service, None)


def _service(client) -> ChatService:
    """One process: its own cache, the shared database."""
    return ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="You are Cheese.",
        workspace_root="/tmp/another-process-ws",
        compute=stub_compute(),
    )


class _Screen:
    """Stands in for the session the turn runs on: it records what the platform
    pushed at it and answers the way a live transport does."""

    def __init__(self, session: SessionRef, work_id: uuid.UUID, receipts) -> None:
        self.session = session
        self.work_id = work_id
        self.receipts = receipts
        self.native_session_id = str(uuid.uuid4())
        self.pushed: list[str] = []

    async def steer(
        self,
        topic_id,
        text,
        images=None,
        *,
        register_input,
        expected_work_id=None,
        agent_handle=None,
        owes_reply=False,
    ) -> bool:
        # The cross-process turn is named by its row: the id steers the input,
        # the seat aims it — never a guess at another seat in the room.
        assert expected_work_id == self.work_id, expected_work_id
        assert agent_handle == self.session.agent_handle, agent_handle
        identity = InputIdentity(
            self.session.project_id,
            self.session.topic_id,
            self.session.agent_handle,
            self.session.harness,
            self.native_session_id,
            uuid.uuid4(),
            expected_work_id,
        )
        await register_input(identity)
        await self.receipts(InputReceipt(identity, "accepted"))
        self.pushed.append(text)
        return True


def _a_room(client):
    """A project and a room of alice's, and the seat its agent works from."""
    project = post_project(client, json={"name": "Handover"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "交接"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return project["id"], room, room_agent_seat(client, room)


def _a_reviewed_task(client):
    """A started task alice reviews — she is on this task's list whichever way
    the board reads it, and the board is what this test watches."""
    project, room, seat = _a_room(client)
    task = delivery_task(client, room)
    filed = client.post(
        f"/topics/{task.id}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": "feat: hand the room over",
            "reviewer_handle": "alice",
            "focus": "交接",
        },
    )
    assert filed.status_code == 200, filed.text
    return project, room, str(task.id), seat


def _seed_open_row(client, conversation, work, seat) -> None:
    """The row a turn's own lifecycle opened — real author, real content."""

    async def seed():
        async with client.test_request_factory() as session:
            await AgentTurnRepository(session).open(
                turn_id=work,
                conversation_id=conversation,
                continuation_id=work,
                author=seat,
                content="在跑一轮活",
                is_resume=False,
                resendable=True,
                started_at=datetime.now(UTC),
                delivered_at=datetime.now(UTC),
                agent_handle=seat,
            )
            await session.commit()

    client.portal.call(seed)


def _close_row(client, work) -> None:
    async def close():
        async with client.test_request_factory() as session:
            await AgentTurnRepository(session).close([work], datetime.now(UTC))
            await session.commit()

    client.portal.call(close)


def _rows(client, model, conversation):
    async def read():
        async with client.test_request_factory() as session:
            return list(
                await session.scalars(
                    select(model).where(model.conversation_id == conversation)
                )
            )

    return client.portal.call(read)


def _get(client, model, ident):
    async def read():
        async with client.test_request_factory() as session:
            return await session.get(model, ident)

    return client.portal.call(read)


def _awaiting(client, handle="alice") -> list[dict]:
    r = client.get("/awaiting-me", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _phrase(client, task_id: str) -> str | None:
    for item in _awaiting(client):
        if item.get("taskId") == task_id:
            return item["phrase"]
    return None


def _task_phrase(client, room: str, task_id: str) -> str | None:
    """The task's board presentation — the one cell 「运行中」 is drawn in, and
    the same facts `/awaiting-me` runs on."""
    r = client.get(f"/topics/{room}/tasks")
    assert r.status_code == 200, r.text
    for row in r.json()["data"]["data"]:
        if row["id"] == task_id:
            return row["presentation"]["phrase"]
    return None


def test_a_message_from_another_backend_lands_in_the_running_turn(client):
    project, room, task_id, seat = _a_reviewed_task(client)
    conversation = uuid.UUID(task_id)
    work = uuid.uuid4()
    _seed_open_row(client, conversation, work, seat)

    # A runs the turn and holds it in its own cache; B never saw it.
    a = _service(client)
    b = _service(client)
    client.portal.call(a.live.mark_turn_active, conversation, work)
    assert not b.live.active_turn_ids.get(conversation)
    # The app is served by B: what the sidebar reads must come from the row.
    app.dependency_overrides[get_chat_service] = lambda: b

    # The board — where 「运行中」 is drawn, and the same facts `/awaiting-me`
    # uses — says the task is running, off the row B never cached. Without the
    # row it would read its card's 「待你审阅」.
    assert _task_phrase(client, room, task_id) == "running"
    # So the card does not linger as a stale 「待你审阅」 in alice's list: the
    # run B now sees is what took that item out of her hands.
    assert _phrase(client, task_id) is None

    # A message that lands on B is merged into A's turn: steered at the expected
    # work id and its seat, its input stamped seen_by that same seat.
    screen = _Screen(
        SessionRef(uuid.UUID(project), conversation, seat, harness=CLAUDE_CODE),
        work,
        b.confirm_prompt_receipt,
    )
    b._compute.steer = screen.steer  # type: ignore[method-assign]
    delivered = client.portal.call(
        b.merge_into_running_turn,
        conversation,
        [],
        "还有一句 @芝士",
        "alice",
        None,
        seat,
    )
    assert delivered is True, delivered
    assert len(screen.pushed) == 1
    seen = _rows(client, NativeInput, conversation)
    assert [row.seen_by for row in seen] == [seat], [row.seen_by for row in seen]

    # No second turn was opened beside A's.
    assert len(_rows(client, AgentTurn, conversation)) == 1

    # The lifecycle ends it; A lets go. B stops counting it, the board stops
    # calling it running, and a later message opens its own turn instead.
    _close_row(client, work)
    client.portal.call(a.live.mark_turn_inactive, conversation, work)
    assert client.portal.call(b.has_running_turn, conversation) is False
    assert _task_phrase(client, room, task_id) != "running"
    assert (
        client.portal.call(
            b.merge_into_running_turn, conversation, [], "又一句", "alice", None, seat
        )
        is None
    )


def test_a_mark_confirms_the_row_and_never_invents_one(client, monkeypatch, caplog):
    """The marks go back to the row — they never write one.

    Both marks write THROUGH to the durable row: `mark_turn_active` confirms an
    open `AgentTurn` exists (it never inserts one, because at `_prompt_lock` the
    row does not exist yet and this path must not fabricate an author or
    content), and `mark_turn_inactive` confirms the lifecycle ended it before
    the cache lets go. Neither closes a row from the cache.
    """
    import logging

    from app.domain.agent.turn.state import authority
    from app.domain.agent.turn.state.live import LiveWork

    opened: list[uuid.UUID] = []
    closed: list[uuid.UUID] = []
    real_confirm_open = authority.confirm_open
    real_confirm_closed = authority.confirm_closed

    async def confirm_open(factory, work):
        opened.append(work)
        return await real_confirm_open(factory, work)

    async def confirm_closed(factory, work):
        closed.append(work)
        return await real_confirm_closed(factory, work)

    _project, room, _seat = _a_room(client)
    topic = uuid.UUID(room)
    factory = client.test_request_factory
    monkeypatch.setattr(authority, "confirm_open", confirm_open)
    monkeypatch.setattr(authority, "confirm_closed", confirm_closed)
    live = LiveWork(
        write_open=lambda _t, work: authority.confirm_open(factory, work),
        confirm_closed=lambda _t, work: authority.confirm_closed(factory, work),
    )

    # No row exists: the mark goes back to the row (「读不到就回库」), confirms
    # nothing and — crucially — creates nothing. No author, no content, no
    # continuation invented.
    missing = uuid.uuid4()
    client.portal.call(live.mark_turn_active, topic, missing)
    assert opened == [missing]
    assert _get(client, AgentTurn, missing) is None

    # A real row the lifecycle opened is confirmed, not rewritten: same author,
    # same content, still open.
    real = uuid.uuid4()
    _seed_open_row(client, topic, real, "芝士")
    client.portal.call(live.mark_turn_active, topic, real)
    assert opened == [missing, real]
    row = _get(client, AgentTurn, real)
    assert row is not None
    assert (row.author, row.content, row.stopped_at) == ("芝士", "在跑一轮活", None)

    # Letting go asks the row whether the lifecycle has closed it. This turn is
    # still open, so the mark must say so rather than paper it over — and it
    # must NOT close the row itself.
    with caplog.at_level(logging.WARNING, logger="app.domain.agent.turn.state.live"):
        client.portal.call(live.mark_turn_inactive, topic, real)
    assert closed == [real]
    assert "still open" in caplog.text
    assert real not in live.active_turn_ids.get(topic, set())
    assert _get(client, AgentTurn, real).stopped_at is None

    # Once the lifecycle actually ends it, letting go is quiet.
    caplog.clear()
    _close_row(client, real)
    with caplog.at_level(logging.WARNING, logger="app.domain.agent.turn.state.live"):
        client.portal.call(live.mark_turn_active, topic, real)
        client.portal.call(live.mark_turn_inactive, topic, real)
    assert "still open" not in caplog.text
