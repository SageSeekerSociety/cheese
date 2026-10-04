"""Turns of messages answered inside another turn are ended (migration 91616b2af5b6).

A message that reached a session while another turn held its seat was answered
inside that turn, and its own turn was never given an end. The room then stayed
busy for good: an environment change was refused as if the agent were working.

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
    / "91616b2af5b6_close_turns_left_open_by_taken_messages.py"
)

SEAT = "cheese-0123456789ab"
OTHER_SEAT = "cheese-ba9876543210"


def _upgrade(client) -> None:
    spec = importlib.util.spec_from_file_location("_mig_taken_turns", _MIGRATION)
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
    seat: str = SEAT,
    author: str = "alice",
    said_at: tuple[datetime, ...] = (),
) -> uuid.UUID:
    """One turn in ``room``, and a block written under its id at each ``said_at``."""
    turn_id = uuid.uuid4()

    async def _run() -> None:
        async with client.test_factory() as s:
            await s.execute(
                text(
                    "INSERT INTO agent_turns (id, topic_id, continuation_id, author,"
                    " content, is_resume, resendable, started_at, delivered_at,"
                    " stopped_at, agent_handle) VALUES (:id, :room, :id, :author,"
                    " 'hello', false, true, :started, :delivered, :stopped, :seat)"
                ),
                {
                    "id": turn_id,
                    "room": uuid.UUID(room),
                    "author": author,
                    "started": started,
                    "delivered": started if delivered else None,
                    "stopped": stopped,
                    "seat": seat,
                },
            )
            project_id = (
                await s.execute(
                    text("SELECT project_id FROM topics WHERE id = :room"),
                    {"room": uuid.UUID(room)},
                )
            ).scalar_one()
            for at in said_at:
                await s.execute(
                    text(
                        "INSERT INTO blocks (id, project_id, topic_id, kind,"
                        " author_type, author, content, refs, turn_id, created_at,"
                        " updated_at) VALUES (:id, :project, :room, 'message',"
                        " 'participant', :seat, 'working on it', '[]', :turn, :at,"
                        " :at)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "project": project_id,
                        "room": uuid.UUID(room),
                        "seat": seat,
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


def test_a_room_held_busy_by_messages_answered_mid_turn_is_free_again(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    host_end = now - timedelta(hours=28)
    _turn(client, room, started=now - timedelta(hours=30), stopped=host_end)
    message = _turn(client, room, started=now - timedelta(hours=29))
    nudge = _turn(
        client, room, started=now - timedelta(hours=29, minutes=-5), author="system"
    )

    assert _busy(client, project_id, room, headers)
    assert _apply_environment(client, project_id, room, headers) == 422

    _upgrade(client)

    assert not _busy(client, project_id, room, headers)
    assert _apply_environment(client, project_id, room, headers) == 200
    # They end when the turn that answered them ended.
    assert _stopped_at(client, message) == host_end
    assert _stopped_at(client, nudge) == host_end


def test_a_turn_that_may_still_be_running_is_left_running(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    # Sent minutes ago while another turn was running: too recent to tell
    # whether it was answered there or is running on its own now.
    _turn(
        client,
        room,
        started=now - timedelta(minutes=40),
        stopped=now - timedelta(minutes=20),
    )
    recent = _turn(client, room, started=now - timedelta(minutes=30))
    # Sent hours ago while another turn was running, but it is still writing:
    # it is the turn the session is working on.
    _turn(
        client,
        room,
        started=now - timedelta(hours=10),
        stopped=now - timedelta(hours=9),
    )
    working = _turn(
        client,
        room,
        started=now - timedelta(hours=9, minutes=30),
        said_at=(now - timedelta(minutes=10),),
    )

    _upgrade(client)

    assert _stopped_at(client, recent) is None
    assert _stopped_at(client, working) is None
    assert _busy(client, project_id, room, headers)
    assert _apply_environment(client, project_id, room, headers) == 422


def test_a_message_answered_inside_a_turn_that_never_ended_is_ended(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    # A turn that worked for hours and then went quiet without an end.
    host = _turn(
        client,
        room,
        started=now - timedelta(hours=20),
        said_at=(now - timedelta(hours=19), now - timedelta(hours=12)),
    )
    answered = _turn(client, room, started=now - timedelta(hours=19, minutes=30))
    # Sent after that turn had gone quiet: nothing shows it was answered there.
    after_quiet = _turn(client, room, started=now - timedelta(hours=11))

    before = datetime.now(UTC)
    _upgrade(client)

    ended = _stopped_at(client, answered)
    assert ended is not None and ended >= before - timedelta(seconds=5)
    assert _stopped_at(client, host) is None
    assert _stopped_at(client, after_quiet) is None
    assert _busy(client, project_id, room, headers)


def test_another_seat_or_an_undelivered_turn_is_not_ended(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    _turn(
        client,
        room,
        started=now - timedelta(hours=30),
        stopped=now - timedelta(hours=28),
    )
    # Another teammate's turn, sent while the first one's was running.
    other_seat = _turn(client, room, started=now - timedelta(hours=29), seat=OTHER_SEAT)
    # Never reached the session: the orphan sweep's to re-send.
    undelivered = _turn(
        client, room, started=now - timedelta(hours=29), delivered=False
    )

    _upgrade(client)

    assert _stopped_at(client, other_seat) is None
    assert _stopped_at(client, undelivered) is None
    assert _busy(client, project_id, room, headers)
