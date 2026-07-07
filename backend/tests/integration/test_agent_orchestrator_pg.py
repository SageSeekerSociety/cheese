"""DB-backed test for the orchestrator's open-agent flow (Act 4).

Proves the whole chain on real Postgres: opening an agent creates a real user, joins
it to the project, mints a session token, and opens a screen with that token injected
as CHEESE_TOKEN — so the agent's cheese api would authenticate as its own user. Self-
contained: its own engine + a temporary project + full row cleanup. No WebSocket, no
real device (a fake device transport records what the hub sends).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.hub import DeviceHub
from app.agent.orchestrator import AgentService
from app.common.auth import decode_token
from app.db.session import async_url
from app.domain.device import DeviceService, SqlDeviceRepository
from app.domain.device.models import DeviceProjectRow, DeviceRow
from app.domain.project.models import Project, ProjectMembership
from app.domain.user.models import User, UserProfile

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeDevice:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)

    def last(self, t: str) -> dict[str, Any]:
        for m in reversed(self.sent):
            if m.get("t") == t:
                return m
        raise AssertionError(f"no {t} message")


async def test_open_agent_creates_user_joins_project_injects_token() -> None:
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    device_service = DeviceService(SqlDeviceRepository(factory))
    hub = DeviceHub()
    agent_service = AgentService(factory, hub, device_service, "CHEESELET_SRC")

    now = datetime.now(UTC)
    project_id: int | None = None
    device_id: str | None = None
    agent_user_id: int | None = None
    try:
        # A temporary project (membership FK-references it).
        async with factory() as session:
            result = await session.execute(
                insert(Project)
                .values(
                    name=f"orch-test-{uuid.uuid4().hex[:8]}",
                    description="",
                    color_code="#123456",
                    team_id=1,
                    leader_id=1,
                    start_date=now,
                    end_date=now,
                    created_at=now,
                    updated_at=now,
                )
                .returning(Project.id)
            )
            project_id = result.scalar_one()
            await session.commit()

        # Enroll + assign a device, connect it.
        device = await device_service.approve(await device_service.start("m"), actor_user_id=1)
        device_id = device.device_id
        await device_service.assign_to_project(device_id, project_id, actor_user_id=1)
        fake = FakeDevice()
        await hub.attach_device(device_id, fake)

        opened = await agent_service.open_agent(device_id=device_id, project_id=project_id)
        agent_user_id = opened.agent_user_id

        # A real agent user + profile exist.
        async with factory() as session:
            user = await session.get(User, agent_user_id)
            assert user is not None and user.username == opened.agent_username
            assert user.hashed_password  # password-capable account (secret hashed)
            profile = (
                await session.execute(
                    ProjectMembership.__table__.select().where(
                        ProjectMembership.user_id == agent_user_id,
                        ProjectMembership.project_id == project_id,
                    )
                )
            ).first()
            assert profile is not None  # joined to the project → inherits shared perms

        # The screen opened for this agent, with the session token injected as CHEESE_TOKEN.
        create = fake.last("session.create")
        assert create["sid"] == opened.sid
        token = create["env"]["CHEESE_TOKEN"]
        payload = decode_token(token)
        assert payload["type"] == "access"
        assert int(payload["sub"]) == agent_user_id  # the agent authenticates as itself

        # Destroy the agent: its screen closes and its user is recycled (soft-deleted).
        recycled = await agent_service.close_agent(device_id=device_id, sid=opened.sid)
        assert recycled == agent_user_id
        assert fake.last("session.close")["sid"] == opened.sid
        assert hub.screen(opened.sid) is None
        async with factory() as session:
            user = await session.get(User, agent_user_id)
            assert user is not None and user.deleted_at is not None  # recycled, not hard-deleted
    finally:
        async with factory() as session:
            if agent_user_id is not None:
                await session.execute(
                    delete(ProjectMembership).where(ProjectMembership.user_id == agent_user_id)
                )
                await session.execute(delete(UserProfile).where(UserProfile.user_id == agent_user_id))
                await session.execute(delete(User).where(User.id == agent_user_id))
            if device_id is not None:
                await session.execute(delete(DeviceProjectRow).where(DeviceProjectRow.device_id == device_id))
                await session.execute(delete(DeviceRow).where(DeviceRow.device_id == device_id))
            if project_id is not None:
                await session.execute(delete(Project).where(Project.id == project_id))
            await session.commit()
        await engine.dispose()
