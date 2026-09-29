"""A session row that points at another teammate's runner stops pointing at it.

Such rows were recorded by turns that ran as the project's default agent under
another teammate's seat. Recovery attached a reader for each row, so two
readers wrote one runner's mirror. The migration clears the borrowed row's
location, keeps its work lease, and leaves the runner's own row alone.
"""

import asyncio
import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.domain.agent_session.models import AgentSession
from app.domain.identity.handles import agent_instance_handle
from tests.integration.conftest import post_project

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/7e4b9d2c1a60_drop_borrowed_runner_locations.py"
)


def _drop_borrowed_locations() -> str:
    spec = importlib.util.spec_from_file_location("borrowed", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DROP_BORROWED_LOCATIONS


def _location(room: str, seat: str, state: str) -> dict:
    return {
        "device_id": "device",
        "resource_id": room,
        "channel": "device",
        "runtime": {"harness": "claude-code", "agent_handle": seat, "state": state},
    }


def test_a_row_on_another_teammates_runner_is_no_longer_found(client):
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    reviewer = client.post(
        f"/projects/{project}/agents",
        json={"handle": "reviewer", "display_name": "审稿人"},
    ).json()["data"]
    rooms = [
        client.post(
            "/topics",
            json={"project_id": project, "title": title, "created_by": "alice"},
        ).json()["data"]["id"]
        for title in ("Shared", "Alone")
    ]
    shared, alone = rooms
    seat = agent_instance_handle(reviewer["id"])
    lease = {"kind": "device", "device_id": "machine", "status": "ready"}

    async def seed() -> dict[str, uuid.UUID]:
        async with client.test_factory() as session:
            rows = {
                # The runner's owner: the teammate's own session on its seat.
                "owner": AgentSession(
                    topic_id=uuid.UUID(shared),
                    agent_handle=reviewer["handle"],
                    harness="claude-code",
                    runtime_location=_location(shared, seat, "/state/shared"),
                    work_lease=lease,
                ),
                # The default agent's row, recorded on the teammate's runner.
                "borrowed": AgentSession(
                    topic_id=uuid.UUID(shared),
                    agent_handle="cheese",
                    harness="claude-code",
                    runtime_location=_location(shared, seat, "/state/shared"),
                    work_lease=lease,
                ),
                # A mismatch with nobody else on its runner is not this case.
                "alone": AgentSession(
                    topic_id=uuid.UUID(alone),
                    agent_handle="cheese",
                    harness="claude-code",
                    runtime_location=_location(alone, seat, "/state/alone"),
                ),
            }
            session.add_all(rows.values())
            await session.commit()
            return {name: row.id for name, row in rows.items()}

    ids = asyncio.run(seed())

    async def migrate_and_read() -> dict[str, AgentSession]:
        async with client.test_factory() as session:
            await session.execute(text(_drop_borrowed_locations()))
            await session.commit()
            found = await session.scalars(
                select(AgentSession).where(AgentSession.id.in_(ids.values()))
            )
            by_id = {row.id: row for row in found}
            return {name: by_id[row_id] for name, row_id in ids.items()}

    after = asyncio.run(migrate_and_read())

    assert after["borrowed"].runtime_location is None
    assert after["borrowed"].work_lease == lease
    assert after["owner"].runtime_location == _location(shared, seat, "/state/shared")
    assert after["alone"].runtime_location == _location(alone, seat, "/state/alone")
