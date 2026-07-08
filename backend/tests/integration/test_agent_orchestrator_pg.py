"""DB-backed test for the orchestrator's open-agent flow (Act 4).

Proves the whole chain on real Postgres: opening an agent creates a real user, joins
it to the project, and opens a screen carrying a per-screen token (CHEESE_SCREEN) that
resolves server-side back to the agent user. Also guards the `cheese update` re-exec
path: a fresh client reconnecting must be re-provisioned with an `adopt` session.create
even though the server-side hub still knows the screen. Self-contained: its own engine +
a temporary project + full row cleanup. No WebSocket, no real device (a fake device
transport records what the hub sends).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.hub import DeviceHub
from app.agent.models import AgentScreenRow
from app.agent.orchestrator import AgentService
from app.core.errors import PreconditionFailedError
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
    def __init__(self, hub: DeviceHub | None = None, device_id: str | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._hub = hub
        self._device_id = device_id

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)
        # Auto-reply to exec RPCs (e.g. the cwd pre-trust before opening a screen) so the
        # orchestrator's `await hub.exec(...)` resolves instead of timing out in tests.
        if msg.get("t") == "exec" and self._hub is not None and self._device_id is not None:
            await self._hub.on_device_message(
                self._device_id, {"t": "exec.result", "id": msg["id"], "stdout": "/cwd", "exit": 0}
            )

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
        fake = FakeDevice(hub, device_id)
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

        # The screen opened for this agent, identified by its per-screen token. No
        # CHEESE_TOKEN is injected any more: the agent's `cheese api` authenticates via
        # the device token + CHEESE_SCREEN (the screen token), resolved server-side to
        # this agent user — so the screen token maps back to the right agent.
        create = fake.last("session.create")
        assert create["sid"] == opened.sid
        assert create["screen"]  # the per-screen secret is shipped as CHEESE_SCREEN
        screen = hub.screen(opened.sid)
        assert screen is not None and screen.agent_user_id == agent_user_id

        # Simulate a client-side `cheese update` re-exec: the old WS drops (the hub
        # keeps the screen, only nulls the transport) and a FRESH client process
        # reconnects. readopt_device_screens MUST re-provision the surviving screen
        # with an `adopt` session.create even though the hub still knew it — otherwise
        # the fresh client (empty local sessions map) never re-adopts its tmux and the
        # 现场 goes black (screens: 0). Regression guard for that bug.
        await hub.detach_device(device_id, fake)  # old process image's WS closes
        fresh = FakeDevice(hub, device_id)
        await hub.attach_device(device_id, fresh)  # re-exec'd binary reconnects
        assert hub.screen(opened.sid) is not None  # server never restarted; screen persists
        adopted = await agent_service.readopt_device_screens(device_id)
        assert adopted == 1
        readopt = fresh.last("session.create")
        assert readopt["sid"] == opened.sid
        assert readopt["adopt"] is True  # adopt (re-drive surviving tmux), not a fresh spawn

        # Destroy the agent: its screen closes and its user is recycled (soft-deleted).
        recycled = await agent_service.close_agent(device_id=device_id, sid=opened.sid)
        assert recycled == agent_user_id
        assert fresh.last("session.close")["sid"] == opened.sid  # goes to the re-exec'd transport
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


async def test_recreate_agent_reuses_user_and_resumes_session() -> None:
    """Recreate reuses the agent's user (identity preserved) and, with resume, relaunches
    Claude on the SAME session id. A still-live screen is only replaced with force."""
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
        async with factory() as session:
            project_id = (
                await session.execute(
                    insert(Project)
                    .values(
                        name=f"rc-{uuid.uuid4().hex[:8]}",
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
            ).scalar_one()
            await session.commit()

        device = await device_service.approve(await device_service.start("m"), actor_user_id=1)
        device_id = device.device_id
        await device_service.assign_to_project(device_id, project_id, actor_user_id=1)
        fake = FakeDevice(hub, device_id)
        await hub.attach_device(device_id, fake)

        opened = await agent_service.open_agent(device_id=device_id, project_id=project_id)
        agent_user_id = opened.agent_user_id
        # We mint Claude's session id and pass it as --session-id at launch.
        launch = fake.last("session.create")["command"][-1]
        assert "--session-id " in launch
        session_id = launch.split("--session-id ")[1].split()[0]

        # While the screen is live, recreate without force is refused (warn-first).
        with pytest.raises(PreconditionFailedError):
            await agent_service.recreate_agent(
                device_id=device_id,
                agent_user_id=agent_user_id,
                project_id=project_id,
                resume=True,
                force=False,
            )

        # With force + resume: same user, a fresh screen, relaunched on the SAME session id.
        recreated = await agent_service.recreate_agent(
            device_id=device_id,
            agent_user_id=agent_user_id,
            project_id=project_id,
            resume=True,
            force=True,
        )
        assert recreated.agent_user_id == agent_user_id  # user reused, identity preserved
        assert recreated.sid != opened.sid  # a fresh screen
        relaunch = fake.last("session.create")["command"][-1]
        assert f"--resume {session_id}" in relaunch  # continues the same Claude conversation

        async with factory() as session:
            user = await session.get(User, agent_user_id)
            assert user is not None and user.deleted_at is None  # NOT recycled — recreate keeps it

        await agent_service.close_agent(device_id=device_id, sid=recreated.sid)
    finally:
        async with factory() as session:
            if agent_user_id is not None:
                await session.execute(
                    delete(AgentScreenRow).where(AgentScreenRow.agent_user_id == agent_user_id)
                )
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
