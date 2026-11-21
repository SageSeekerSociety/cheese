"""A session that already works in an environment of its choice keeps working
there, with its files, when the conversation comes to point elsewhere.

Two agents of a room's default each work in an environment of their own (as
sessions placed before environments were shared are left), and the room points
to one of them. The other's next call still lands in its own home: moving it
would leave whatever it had not pushed behind, with nobody to keep it.
"""

import uuid

import pytest

from app.domain.agent_session.services import AgentSessionService
from app.domain.topic.models import Topic
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_a_conversation_points_to_where_its_work_is import (
    _hands,
    _machines,
    _seat,
)
from tests.integration.test_agents_of_one_conversation_share_its_environment import (
    _computers,
)
from tests.support.environments import give_environment

pytestmark = pytest.mark.anyio


def _lease(device: str, resource: str, room_resource: str) -> dict:
    return {
        "kind": "device",
        "device_id": device,
        "resource_id": resource,
        "room_resource_id": room_resource,
        "status": "ready",
        "home": f"/{resource}",
        "state": f"/{resource}/state",
        "workspace": f"/{resource}/work",
        "mcp_servers": [],
    }


async def test_a_session_in_an_environment_of_its_choice_is_not_moved(
    client, monkeypatch
):
    project = post_project(client, json={"name": "Stays"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, room_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    ada_home, bob_home = str(uuid.uuid4()), str(uuid.uuid4())
    async with client.test_factory() as db:
        (shared,) = await _computers(db, project_id, ("shared",))
        topic = await db.get(Topic, room_id)
        topic.default_environment = {"kind": "self_hosted", "device_id": shared}
        resource = str(topic.resource_id or room_id)
        ada = await _seat(
            db, project_id, room_id, room_id, resource, "ada", channel="device"
        )
        bob = await _seat(
            db, project_id, room_id, room_id, resource, "bob", channel="device"
        )
        sessions = AgentSessionService(db)
        for handle, session_id, home in (
            ("ada", ada[0], ada_home),
            ("bob", bob[0], bob_home),
        ):
            row = await sessions.by_id(session_id)
            assert row is not None
            await give_environment(
                db,
                row,
                _lease(shared, home, resource),
                # The room points to Ada's.
                pointed=handle == "ada",
            )
        await db.commit()
    _machines(monkeypatch)

    assert _hands(client, room_id, *bob)["resource_id"] == bob_home
    assert _hands(client, room_id, *ada)["resource_id"] == ada_home
