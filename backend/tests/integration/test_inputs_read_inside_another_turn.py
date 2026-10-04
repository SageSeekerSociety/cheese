"""Turns read inside another turn that ended are ended (migration 08b4bcff4ad2).

A message sent while a session was mid-turn was read inside that turn, and the
backend that saw it read was replaced before the turn ended, so the message's
own turn never got an end and its room stayed busy.

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
    / "08b4bcff4ad2_end_turns_read_inside_another.py"
)

SEAT = "cheese"
AGENT = "cheese-0123456789ab"


def _upgrade(client) -> None:
    spec = importlib.util.spec_from_file_location("_mig_read_inside", _MIGRATION)
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


def _sql(client, statement: str, **params):
    async def _run():
        async with client.test_factory() as s:
            result = await s.execute(text(statement), params)
            await s.commit()
            return result

    return asyncio.run(_run())


def _turn(
    client,
    room: str,
    *,
    started: datetime,
    stopped: datetime | None = None,
    by_session: bool = False,
) -> uuid.UUID:
    """One delivered turn on ``room``'s seat. A turn the session opened itself
    is authored by its agent; a platform message's by ``system``."""
    turn_id = uuid.uuid4()
    _sql(
        client,
        "INSERT INTO agent_turns (id, topic_id, continuation_id, author, content,"
        " is_resume, resendable, started_at, delivered_at, stopped_at, agent_handle)"
        " VALUES (:id, :room, :id, :author, :content, false, :resendable, :started,"
        " :started, :stopped, :seat)",
        id=turn_id,
        room=uuid.UUID(room),
        author=AGENT if by_session else "system",
        content="" if by_session else "check in",
        resendable=not by_session,
        started=started,
        stopped=stopped,
        seat=SEAT,
    )
    return turn_id


def _input(
    client,
    room: str,
    work: uuid.UUID,
    *,
    executed_in: uuid.UUID,
    at: datetime,
    echoed: bool = True,
) -> None:
    """The ledger's row for ``work``'s input: echoed by the session under
    ``executed_in``."""
    project_id = _sql(
        client, "SELECT project_id FROM topics WHERE id = :room", room=uuid.UUID(room)
    ).scalar_one()
    _sql(
        client,
        "INSERT INTO native_inputs (id, project_id, topic_id, recipient_handle,"
        " harness, native_session_id, input_id, work_id, execution_work_id,"
        " block_ids, seen_block_ids, held_block_ids, released_block_ids,"
        " registered_at, echoed_at) VALUES (:id, :project, :room, :seat,"
        " 'claude-code', 'native-session', :work, :work, :executed_in, '[]', '[]',"
        " '[]', '[]', :at, :echoed)",
        id=uuid.uuid4(),
        project=project_id,
        room=uuid.UUID(room),
        seat=SEAT,
        work=work,
        executed_in=executed_in,
        at=at,
        echoed=at if echoed else None,
    )


def _stopped_at(client, turn_id: uuid.UUID) -> datetime | None:
    return _sql(
        client, "SELECT stopped_at FROM agent_turns WHERE id = :id", id=turn_id
    ).scalar_one()


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


def test_a_check_in_read_inside_the_sessions_own_work_frees_its_room(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    # The session was working on its own when the check-in arrived, read it at
    # a tool boundary, and finished that work.
    finished = now - timedelta(hours=2, minutes=50)
    own_work = _turn(
        client,
        room,
        started=now - timedelta(hours=3),
        stopped=finished,
        by_session=True,
    )
    check_in = _turn(client, room, started=now - timedelta(hours=2, minutes=58))
    _input(
        client,
        room,
        check_in,
        executed_in=own_work,
        at=now - timedelta(hours=2, minutes=57),
    )

    assert _busy(client, project_id, room, headers)
    assert _apply_environment(client, project_id, room, headers) == 422

    _upgrade(client)

    assert not _busy(client, project_id, room, headers)
    assert _apply_environment(client, project_id, room, headers) == 200
    # It ends when the work that read it ended.
    assert _stopped_at(client, check_in) == finished


def test_a_check_in_whose_reader_has_not_ended_is_left_running(client):
    project_id, headers = _project(client)
    room = _room(client, project_id)
    now = datetime.now(UTC)
    # Read inside work the session is still doing: it ends when that work does.
    still_working = _turn(
        client, room, started=now - timedelta(minutes=20), by_session=True
    )
    riding = _turn(client, room, started=now - timedelta(minutes=10))
    _input(
        client,
        room,
        riding,
        executed_in=still_working,
        at=now - timedelta(minutes=9),
    )
    # Sent, but the session never echoed it: whether it was read is unknown.
    ended = _turn(
        client,
        room,
        started=now - timedelta(hours=5),
        stopped=now - timedelta(hours=4),
        by_session=True,
    )
    unread = _turn(client, room, started=now - timedelta(hours=4, minutes=30))
    _input(
        client,
        room,
        unread,
        executed_in=ended,
        at=now - timedelta(hours=4, minutes=29),
        echoed=False,
    )

    _upgrade(client)

    assert _stopped_at(client, riding) is None
    assert _stopped_at(client, unread) is None
    assert _busy(client, project_id, room, headers)
