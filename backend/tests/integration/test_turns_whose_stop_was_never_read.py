"""Turns whose Stop the room's reader never got past are ended (migration 80aabe850e1e).

A room's reader stopped at a turn's result and never read on, so that turn and
the ones after it on the same seat were never given an end, and the room stayed
busy: an environment change was refused as if the agent were working.

The migration's ``upgrade()`` itself is run, with a real alembic operations
context, the same way ``alembic upgrade head`` runs it. What is checked is what
a person sees afterwards: whether the room still refuses an environment change,
and whether each turn is still running.
"""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.conftest import seed_user
from tests.integration.conftest import post_project, session_auth_headers

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "80aabe850e1e_close_turns_whose_stop_was_never_read.py"
)

SEAT = "cheese"
AGENT = "cheese-0123456789ab"
OTHER_AGENT = "cheese-ba9876543210"


def _upgrade(client) -> None:
    spec = importlib.util.spec_from_file_location("_mig_unread_stops", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def _apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

    async def _run() -> None:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    asyncio.run(_run())


def _project(client) -> tuple[str, dict]:
    headers = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}
    response = post_project(client, json={"name": "Turns"}, owner="alice")
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"], headers


def _room(client, project_id: str) -> str:
    response = client.post(
        "/topics",
        json={"project_id": project_id, "title": "Room"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _turn(
    client,
    room: str,
    *,
    started: datetime,
    stopped: datetime | None = None,
    delivered: bool = True,
    author: str = "alice",
    self_started: bool = False,
    said: tuple[tuple[datetime, str], ...] = (),
) -> uuid.UUID:
    """One turn on ``room``'s seat, and a block under its id for each
    ``(at, author)`` in ``said``. A self-started turn is what a session opens
    for work nobody fed it: authored by its agent, no prompt to re-send."""
    turn_id = uuid.uuid4()

    async def _run() -> None:
        async with client.test_factory() as s:
            await s.execute(
                text(
                    "INSERT INTO agent_turns (id, topic_id, continuation_id, author,"
                    " content, is_resume, resendable, started_at, delivered_at,"
                    " stopped_at, agent_handle) VALUES (:id, :room, :id, :author,"
                    " :content, false, :resendable, :started, :delivered, :stopped,"
                    " :seat)"
                ),
                {
                    "id": turn_id,
                    "room": uuid.UUID(room),
                    "author": AGENT if self_started else author,
                    "content": "" if self_started else "hello",
                    "resendable": not self_started,
                    "started": started,
                    "delivered": started if delivered else None,
                    "stopped": stopped,
                    "seat": SEAT,
                },
            )
            project_id = (
                await s.execute(
                    text("SELECT project_id FROM topics WHERE id = :room"),
                    {"room": uuid.UUID(room)},
                )
            ).scalar_one()
            for at, by in said:
                await s.execute(
                    text(
                        "INSERT INTO blocks (id, project_id, topic_id, kind,"
                        " author_type, author, content, refs, turn_id, created_at,"
                        " updated_at) VALUES (:id, :project, :room, 'message',"
                        " 'participant', :by, 'working on it', '[]', :turn, :at, :at)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "project": project_id,
                        "room": uuid.UUID(room),
                        "by": by,
                        "turn": turn_id,
                        "at": at,
                    },
                )
            await s.commit()

    asyncio.run(_run())
    return turn_id


def _stopped_at(client, turn_id: uuid.UUID) -> datetime | None:
    async def _run():
        async with client.test_factory() as s:
            return (
                await s.execute(
                    text("SELECT stopped_at FROM agent_turns WHERE id = :id"),
                    {"id": turn_id},
                )
            ).scalar_one()

    return asyncio.run(_run())


def _apply_environment(client, project_id: str, room: str, headers: dict) -> int:
    return client.post(
        f"/projects/{project_id}/environment/rooms/{room}/apply",
        headers=headers,
        json={"latest": True},
    ).status_code


def _busy(client, project_id: str, room: str, headers: dict) -> bool:
    state = client.get(
        f"/projects/{project_id}/environment/rooms/{room}", headers=headers
    ).json()["data"]
    return state.get("busy", False)


def test_a_room_whose_reader_stopped_at_a_turn_is_free_again(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    # The session's own turn: it said its last word, and its Stop was never read.
    last_word = now - timedelta(hours=14)
    own = _turn(
        client,
        room,
        started=now - timedelta(hours=20),
        self_started=True,
        said=((now - timedelta(hours=19), AGENT), (last_word, AGENT)),
    )
    # A message it answered, whose Stop was not read either; the seat went on
    # to a later turn that wrote under its own id.
    answered_at = now - timedelta(hours=12)
    answered = _turn(
        client, room, started=now - timedelta(hours=13), said=((answered_at, AGENT),)
    )
    _turn(
        client,
        room,
        started=now - timedelta(hours=11),
        stopped=now - timedelta(hours=10),
        said=((now - timedelta(hours=10, minutes=30), AGENT),),
    )

    assert _busy(client, project_id, room, headers)
    assert _apply_environment(client, project_id, room, headers) == 422

    _upgrade(client)

    assert not _busy(client, project_id, room, headers)
    assert _apply_environment(client, project_id, room, headers) == 200
    # Each ends at the last thing it wrote.
    assert _stopped_at(client, own) == last_word
    assert _stopped_at(client, answered) == answered_at


def test_a_turn_the_session_may_still_be_on_is_left_running(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    # Its agent wrote in the room an hour ago: the session is still on it.
    # Another agent's block on its id is not its own and proves nothing.
    working = _turn(
        client,
        room,
        started=now - timedelta(hours=20),
        self_started=True,
        said=(
            (now - timedelta(hours=1), AGENT),
            (now - timedelta(hours=2), OTHER_AGENT),
        ),
    )
    # Quiet for hours, but nothing on its seat came after it.
    last_on_seat = _turn(
        client,
        room,
        started=now - timedelta(hours=9),
        said=((now - timedelta(hours=8), AGENT),),
    )
    # Sent recently.
    recent = _turn(client, room, started=now - timedelta(hours=2))
    # Never reached its session: the orphan sweep's to re-send.
    undelivered = _turn(
        client, room, started=now - timedelta(hours=30), delivered=False
    )

    _upgrade(client)

    assert _stopped_at(client, working) is None
    assert _stopped_at(client, last_on_seat) is None
    assert _stopped_at(client, recent) is None
    assert _stopped_at(client, undelivered) is None
    assert _busy(client, project_id, room, headers)


def test_another_agents_block_on_its_id_does_not_keep_it_open(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    # Its own last word was long ago; an hour ago another agent's message was
    # attached to it because it was the room's newest open turn.
    last_word = now - timedelta(hours=13)
    own = _turn(
        client,
        room,
        started=now - timedelta(hours=14),
        self_started=True,
        said=((last_word, AGENT), (now - timedelta(hours=1), OTHER_AGENT)),
    )

    _upgrade(client)

    assert _stopped_at(client, own) == last_word
    assert not _busy(client, project_id, room, headers)
