"""Who is at work in a channel is read for the conversations the page draws.

`GET /topics` and `GET /topics/{id}` mark the members working in a room, in
one of its 支线 and on its tasks, from the runners' own word in
``session_states``. That read used to fetch every session running on the
platform and keep the page's rows in memory; it now asks for the page's rooms,
their 支线 and the named tasks only. A session running in another project's
room is not read, and the page still says who works where.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime

from app.domain.agent.models import SessionState
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_topic_list_batches import _seed_populated_project
from tests.support.counting_session import counting_sql

_RUNNING = re.compile(r"FROM session_states WHERE session_states\.worker_status")
_FOR_THE_PAGE = re.compile(r"session_states\.conversation_id = ANY")


def _running(client, *conversations: tuple[str, str]) -> None:
    async def _add() -> None:
        async with client.test_factory() as s:
            for conversation, seat in conversations:
                s.add(
                    SessionState(
                        conversation_id=uuid.UUID(conversation),
                        agent_handle=seat,
                        harness="claude-code",
                        worker_epoch=1,
                        worker_status="running",
                        changed_at=datetime.now(UTC),
                    )
                )
            await s.commit()

    asyncio.run(_add())


def test_the_channel_list_and_a_channel_read_only_their_own_running_sessions(client):
    project_id, rooms = _seed_populated_project(client)
    elsewhere = post_project(client, json={"name": "Elsewhere"}, owner="dave").json()[
        "data"
    ]["root_topic_id"]
    _running(
        client,
        (rooms[0], "cheese-here"),
        (elsewhere, "cheese-elsewhere"),
    )
    headers = session_auth_headers("alice")

    for url in (f"/topics?project_id={project_id}", f"/topics/{rooms[0]}"):
        with counting_sql(client) as statements:
            r = client.get(url, headers=headers)
        assert r.status_code == 200, r.text
        running = [s for s in statements if _RUNNING.search(s)]
        assert len(running) == 1, (url, running)
        assert _FOR_THE_PAGE.search(running[0]), (url, running)

    listed = client.get(f"/topics?project_id={project_id}", headers=headers).json()
    rows = (
        listed["data"]["data"] if isinstance(listed["data"], dict) else listed["data"]
    )
    row = next(t for t in rows if t["id"] == rooms[0])
    assert [a["member"] for a in row["activity"]] == ["cheese-here"]
