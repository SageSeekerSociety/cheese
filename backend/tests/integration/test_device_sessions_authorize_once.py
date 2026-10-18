"""Listing the agent sessions on a device judges every room's readability in
one batch, not one ``authorize_topic`` per session: the statement count of
GET …/devices/{id}/sessions does not grow with the number of sessions, and a
private room stays counted in ``hidden``.
"""

import uuid

import pytest
from sqlalchemy import event

from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import session_reports
from tests.integration.conftest import session_auth_headers
from tests.integration.test_bulk_switch_from_distribution import (
    _project_on_a_device,
)

pytestmark = pytest.mark.anyio


async def _more_sessions(client, p, count: int) -> None:
    for i in range(count):
        topic_id = uuid.UUID(
            client.post(
                "/topics",
                json={"project_id": str(p.project_id), "title": f"Extra {i}"},
                headers=session_auth_headers("alice"),
            ).json()["data"]["id"]
        )
        async with client.test_factory() as db:
            session = await AgentSessionService(db).ensure(
                topic_id, f"agent-x{i}", harness="claude-code"
            )
            generation = str(uuid.uuid4())
            session.execution_request = {"generation": generation}
            await AgentSessionService(db).set_environment(
                topic_id,
                session.agent_handle,
                {"kind": "self_hosted", "device_id": p.old},
                chosen_by=None,
            )
            session.work_lease = {
                "kind": "device",
                "session_id": str(session.id),
                "generation": generation,
                "resource_id": str(uuid.uuid4()),
                "room_resource_id": str(topic_id),
                "device_id": p.old,
                "status": "ready",
                "home": "/old",
                "state": "/old/state",
                "workspace": "/old/work",
                "mcp_servers": [],
            }
            await db.commit()


def _listing(client, p) -> tuple[int, dict]:
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    engine = client.test_app_engine.sync_engine  # type: ignore[attr-defined]
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.get(
            f"/projects/{p.project_id}/devices/{p.old}/sessions", headers=p.alice
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    return len(statements), response.json()["data"]


async def test_statements_do_not_grow_with_the_session_count(client, monkeypatch):
    # Naming each session's agent is its own per-session read, outside this
    # route; stub it so the count isolates the readability check.
    async def _name(*_args, **_kwargs):
        return {"agent_name": "agent"}

    monkeypatch.setattr(session_reports, "_agent_name", _name)
    p = await _project_on_a_device(client)
    few_cost, few = _listing(client, p)
    await _more_sessions(client, p, 9)
    many_cost, many = _listing(client, p)

    assert (len(few["sessions"]), len(many["sessions"])) == (2, 11)
    # Bob's private room is still counted, not listed, at either size.
    assert few["hidden"] == many["hidden"] == 1
    assert few_cost == many_cost
