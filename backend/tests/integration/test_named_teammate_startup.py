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
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.host import SessionHost
from app.domain.identity.services import IdentityService
from tests.integration.conftest import post_project, session_auth_headers


@pytest.mark.anyio
@pytest.mark.parametrize("harness", ["pi", "codex", "claude-code"])
@pytest.mark.parametrize("needs_place", [True, False])
async def test_invited_teammate_is_the_startup_identity(
    client, monkeypatch, harness, needs_place
):
    project = post_project(client, json={"name": "Trial"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Trial"},
        headers=session_auth_headers("alice"),
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
    hub = SimpleNamespace(
        target=lambda _device: "linux-amd64",
        isolates=lambda _device: None,
        is_online=lambda host: host in {"center", "executor"},
    )
    device = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    async with client.test_factory() as db:
        default = await IdentityService(db).ensure_room_agent_user(room_id)
        default_id, default_handle = default.id, default.username
        await db.commit()
    device._resolve_device_agent = AsyncMock(
        return_value=("executor", default_id, default_handle)
    )
    channel = CentralChannel(device)
    placement = client.portal.call(
        lambda: channel.precheck(
            SessionRef(project_id, room_id, "reviewer", harness=harness),
            needs_place=needs_place,
        )
    )
    assert placement.agent_handle == seat
    assert placement.agent_user_id != default_id
    assert placement.rented is False
    assert placement.deferred is needs_place

    ref = SessionRef(project_id, room_id, "reviewer", harness=harness)
    if harness in ("pi", "codex"):
        launched = []

        @asynccontextmanager
        async def remote_session(**kwargs):
            launched.append(kwargs)
            kwargs["runtime_factory"](str(room_id))
            yield SimpleNamespace(
                device_id="center",
                agent_user_id=1,
                agent_handle=token_agent_handle(kwargs["token"]),
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
            return_value={
                "exit": 0,
                "stdout": json.dumps(
                    {
                        "thread_id": "trial",
                        "session_id": "trial",
                        "alive": True,
                        "capabilities": [LONG_POLL],
                    }
                ),
            }
        )
        host = SessionHost(hub, session_factory=client.test_request_factory)
        sessions = RoomSessions(channel, harness, host)
        live = client.portal.call(
            lambda: sessions.ensure(
                ref, system_prompt="Trial", acting=seat, needs_place=needs_place
            )
        )
        assert live.acting == seat
        assert token_agent_handle(launched[0]["token"]) == seat
