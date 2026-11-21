"""A session at work while the environment tables are filled keeps working.

Its turn holds a credential minted before (naming the session and its lease's
generation), and a command already running on its machine names the session
(``CHEESE_ATTACHMENT``). Once the reconciliation that fills the environment
tables from the session's lease has run (migration 4c21cc2f20a4), the
credential still reaches the machine, and the command is still answered as
the session.
"""

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.machine.caller import caller_of
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.support.devices import enrollment_team, place_in_project_team

pytestmark = pytest.mark.anyio

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "4c21cc2f20a4_*.py"
    )
)


def _reconciliation():
    spec = importlib.util.spec_from_file_location("fill_environments", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_a_credential_and_a_command_from_before_still_act_for_the_session(
    client, monkeypatch
):
    project = post_project(client, json={"name": "Before"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, room_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    seat = room_agent_seat(client, room_id)
    generation = str(uuid.uuid4())
    async with client.test_factory() as db:
        owner = await db.scalar(select(User).where(User.username == "alice"))
        if owner is None:
            owner = User(
                username="alice",
                email="alice@example.test",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(owner)
            await db.flush()
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("ada"),
            owner_user_id=owner.id,
            supply=Supply.self_hosted,
            team_id=await enrollment_team(devices, owner.id),
        )
        await place_in_project_team(
            devices, device.device_id, project_id, actor_user_id=owner.id
        )
        topic = await db.get(Topic, room_id)
        assert topic is not None
        resource = str(topic.resource_id or room_id)
        session = await AgentSessionService(db).ensure(
            room_id, seat, harness="claude-code"
        )
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        await AgentSessionService(db).set_environment(
            room_id,
            seat,
            {"kind": "self_hosted", "device_id": device.device_id},
            chosen_by=None,
        )
        session_id = session.id
        await db.flush()
        # Where the previous release kept it: on the session row.
        await db.execute(
            text(
                "UPDATE agent_sessions SET work_lease = CAST(:lease AS json),"
                " execution_request = CAST(:request AS json) WHERE id = :id"
            ),
            {
                "id": session_id,
                "lease": json.dumps(
                    {
                        "kind": "device",
                        "status": "ready",
                        "session_id": str(session_id),
                        "generation": generation,
                        "resource_id": resource,
                        "room_resource_id": resource,
                        "device_id": device.device_id,
                        "state": "/ada/state",
                        "workspace": "/work",
                        "mcp_servers": [],
                        "own": True,
                        "context_tree": {"files": 3},
                        "url": (
                            f"http://api/topics/{room_id}/execution/session-{resource}"
                        ),
                    }
                ),
                "request": json.dumps({"generation": generation}),
            },
        )
        await db.commit()
    launch = mint_scoped_token(
        project_id=str(project_id), topic_id=str(room_id), agent_handle=seat
    )
    minted_before = bind_resource_token(
        launch, resource, session_id=str(session_id), lease_generation=generation
    )

    async with client.test_factory() as db:
        for statement in _reconciliation().RECONCILE:
            await db.execute(text(statement))
        await db.commit()

    remote = AsyncMock(return_value={"done": True})
    monkeypatch.setattr(execution, "call", remote)
    response = client.post(
        f"/topics/{room_id}/execution/session-{resource}",
        headers={"Authorization": f"Bearer {minted_before}"},
        json={
            "method": "invoke",
            "params": {"id": "1", "tool": "Read", "args": {"file_path": "a"}},
        },
    )
    assert response.status_code == 200, response.text
    remote.assert_awaited()

    async with client.test_factory() as db:
        caller = await caller_of(db, device.device_id, "/ada/state", str(session_id))
    assert caller is not None and caller.session_id == str(session_id)
    # What its install left that the platform does not name is still there.
    assert caller.environment["context_tree"] == {"files": 3}
