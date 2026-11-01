"""Whether a handle is an AI teammate is answered in one statement.

Permission checks ask it of the person acting (`project_roles._standing`, on
every addressing preview and every message), and rosters ask it of everyone in
a room (`IdentityService.agents_among`). It was the user row first and its
agent binding second, two round trips each time.
"""

import asyncio

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.domain.identity.services import IdentityService
from tests.integration.test_switch_pushes_first import _room


def _statements_of(client, ask):
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    async def run():
        async with client.test_factory() as db:
            event.listen(Engine, "after_cursor_execute", record)
            try:
                return await ask(IdentityService(db))
            finally:
                event.remove(Engine, "after_cursor_execute", record)

    # Its own task: no request facts from an earlier read answer it.
    return asyncio.run(run()), seen


def test_a_person_is_told_apart_from_an_agent_in_one_statement(client):
    room = asyncio.run(_room(client))

    person, seen = _statements_of(client, lambda ids: ids.is_agent("alice"))
    agent, seen_agent = _statements_of(
        client, lambda ids: ids.is_agent(room.agent_handle)
    )

    assert (person, agent) == (False, True)
    assert len(seen) == 1, seen
    assert len(seen_agent) == 1, seen_agent


def test_a_roster_is_split_into_people_and_agents_in_one_statement(client):
    room = asyncio.run(_room(client))

    agents, seen = _statements_of(
        client, lambda ids: ids.agents_among(["alice", room.agent_handle, "nobody"])
    )

    assert agents == {room.agent_handle}
    assert len(seen) == 1, seen
