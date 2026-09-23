"""A room's selected teammate owns the identity passed to every harness."""

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.sandbox_auth import token_agent_handle
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.codex.channel import CodexChannel
from app.domain.agent.harness.pi.channel import PiChannel
from app.domain.identity.services import IdentityService
from tests.integration.conftest import session_auth_headers


@pytest.mark.anyio
@pytest.mark.parametrize("harness", ["pi", "codex", "claude-code"])
@pytest.mark.parametrize("needs_place", [True, False])
async def test_invited_teammate_is_the_startup_identity(
    client, monkeypatch, harness, needs_place
):
    project = client.post(
        "/projects", json={"name": "Trial", "owner_handle": "alice"}
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Trial", "created_by": "alice"},
    ).json()["data"]
    made = client.post(f"/projects/{project['id']}/agents", json={"handle": "reviewer"})
    assert made.status_code == 200, made.text
    seat = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{room['id']}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    project_id, room_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub = SimpleNamespace(is_online=lambda host: host in {"center", "executor"})
    device = DeviceChannel(hub=hub, session_factory=client.test_factory)
    async with client.test_factory() as db:
        default = await IdentityService(db).ensure_room_agent_user(room_id)
        default_id, default_handle = default.id, default.username
        await db.commit()
    device._resolve_device_agent = AsyncMock(
        return_value=("executor", default_id, default_handle)
    )
    channel = device if harness == "pi" else CentralChannel(device)
    placement = await channel.precheck(
        SessionRef(project_id, room_id, "reviewer", harness=harness),
        needs_place=needs_place,
    )
    assert placement.agent_handle == seat
    assert placement.agent_user_id != default_id
    assert placement.rented is (needs_place and harness == "pi")
    assert placement.deferred is (needs_place and harness != "pi")

    ref = SessionRef(project_id, room_id, "reviewer", harness=harness)
    opening = Opening(system_prompt="Trial", agent_handle=seat, needs_place=needs_place)
    if harness == "pi":
        device.ensure_ready = AsyncMock(
            return_value=SimpleNamespace(resource_id=room_id)
        )
        hub.call_executor = AsyncMock(
            return_value={"alive": True, "session_id": "trial"}
        )
        handle = await PiChannel(device).ensure(ref, opening)
        assert handle.agent_handle == seat
        assert (
            token_agent_handle(device.ensure_ready.await_args.kwargs["token"]) == seat
        )
    elif harness == "codex":
        launched = []

        @asynccontextmanager
        async def remote_session(**kwargs):
            launched.append(kwargs)
            kwargs["runtime_factory"](str(room_id))
            yield SimpleNamespace(
                device_id="center",
                token=kwargs["token"],
                env={
                    "CHEESE_RESOURCE_ID": str(room_id),
                    "CHEESE_EXECUTION_TARGET": json.dumps(
                        {"kind": "scoped", "mcp_servers": []}
                    ),
                },
            )

        channel.prepare_session = remote_session
        channel._device_api_base = AsyncMock(return_value="http://trial.test")
        hub.exec = AsyncMock(
            return_value={"exit": 0, "stdout": '{"thread_id":"trial"}'}
        )
        handle = await CodexChannel(channel, SimpleNamespace()).ensure(ref, opening)
        assert handle.agent_handle == seat
        assert token_agent_handle(launched[0]["token"]) == seat
