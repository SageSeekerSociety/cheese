"""Central agent sessions with independently selected room execution."""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.sandbox_auth import bind_resource_token, token_agent_handle
from app.core.sentences import say
from app.domain.agent import execution, private_chat
from app.domain.agent.admission import SESSION_MEMORY_ENV, session_memory_max
from app.domain.agent.device_provider import (
    DeviceChannel,
)
from app.domain.agent.executor_transport import DEFERRED_WORKSPACE
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent_instance.services import agent_stdio_servers
from app.domain.agent_session.services import AgentSessionService
from app.domain.remote_mcp import service as remote_mcp
from app.domain.topic.services import TopicService
from app.domain.user.services import user_by_handle

logger = logging.getLogger(__name__)

# How long a turn asks a session's leased machine whether it answers before
# starting the session on it: a turn waits this long at most, and only when a
# relaunch onto the machine is due or no session is running.
MACHINE_PROBE_TIMEOUT_S = 15


@dataclass(frozen=True, slots=True)
class PreparedSession:
    device_id: str
    agent_user_id: int
    agent_handle: str
    project_id: uuid.UUID
    topic_id: uuid.UUID
    token: str
    env: dict[str, str]


@dataclass(frozen=True, slots=True)
class Placed:
    """A session this channel placed, as its row records where it runs."""

    #: The conversation's key in its room.
    session: SessionRef
    #: The session host it runs on, and its state directory there.
    machine: str
    state: str
    #: What the room's machine is kept under, and the agent acting there.
    resource_id: str
    agent_handle: str
    #: The conversation the row resumes: what a terminal answer has to name
    #: before it may close anything (FB-56).
    resume_token: str | None
    #: Its runner was found gone after an earlier restart: nothing on the
    #: machine to ask until the next message starts it again.
    let_go: bool = False


class CentralChannel(DeviceChannel):
    def __init__(self, executor):
        super().__init__(hub=executor._hub, session_factory=executor._session_factory)
        self.executor = executor
        self.name = executor.name
        self.deferred_work = True
        # 手是执行机的，所以这条通道的供给就是被它包住的那条通道的供给：一台机器
        # 归哪条通道认领，说的是那台机器，不是中心会话机。
        self.supply = executor.supply

    def available(self):
        return bool(
            settings.agent_session_device_id
            and self._hub.is_online(settings.agent_session_device_id)
        )

    async def precheck(self, session: SessionRef, *, needs_place: bool) -> Placement:
        own = await self._session_host_agent(session)
        return own._replace(deferred=needs_place)

    async def _starts_on_machine(self, lease, center, topic_id, resource) -> bool:
        """Whether this turn's launch puts the session on its leased machine.

        A session is only ever started on the machine it can take right now:
        one started on a machine it cannot reach would see the project without
        the instructions, hooks and MCP servers it reads when it starts. So a
        placeholder session stays one — its conversation and its mapped
        commands still work — until a turn finds the machine answering, and a
        relaunch put off that way waits as one put off by a running task does.
        A session already on the machine stays there however the machine
        answers: a machine that is out of reach is not a reason to move it.
        """
        existing = self._existing_screen(center, topic_id, resource)
        if (
            existing is not None
            and (existing.execution_target or {}).get("workspace") == lease["workspace"]
        ):
            return True
        if not self._hub.is_online(lease["device_id"]):
            return False
        try:
            await execution.call(
                lease, "ping", {}, hub=self._hub, timeout=MACHINE_PROBE_TIMEOUT_S
            )
        except Exception:  # noqa: BLE001 — any failure means not now, and is logged
            logger.info(
                "central_session_stays_deferred topic=%s device=%s",
                topic_id,
                lease["device_id"],
                exc_info=True,
            )
            return False
        return True

    def _center(self, place, precheck: Placement) -> str | None:
        """The machine this session's process runs on: where it already is,
        else the deployment's session host."""
        return place.machine if place else settings.agent_session_device_id

    def _deferred_target(self, target: dict) -> dict:
        """The execution target a session starts with before its lease, as
        this channel hands it on."""
        return target

    async def placed(self, harness: str, device_id: str | None = None) -> list[Placed]:
        """This channel's placed sessions of ``harness`` — on ``device_id``,
        when it is named: what a restarted backend reads again."""
        factory = self._session_factory or async_session_factory
        async with factory() as db:
            sessions = await AgentSessionService(db).placed_sessions()
        return [
            Placed(
                SessionRef(
                    project_id, room_id, handle, harness=row_harness, inner_id=inner_id
                ),
                place.machine,
                place.runtime["state"],
                place.resource_id,
                # A row written before it recorded who acts there acts as its
                # own seat.
                place.runtime.get("agent_handle") or handle,
                resume_token,
                let_go,
            )
            for (
                project_id,
                room_id,
                inner_id,
                handle,
                row_harness,
                resume_token,
                place,
                let_go,
            ) in sessions
            if row_harness == harness
            and place.channel == self.name
            and "state" in (place.runtime or {})
            and (device_id is None or place.machine == device_id)
        ]

    async def let_go(
        self, sessions: list[SessionRef], *, placed_before: datetime
    ) -> None:
        """Record that these sessions' runners are gone, so the next restart
        does not ask their machine again (``RoomSessions.recover``)."""
        factory = self._session_factory or async_session_factory
        async with factory() as db:
            service = AgentSessionService(db)
            for session in sessions:
                await service.let_go(
                    conversation_id=session.conversation_id,
                    agent_handle=session.agent_handle,
                    harness=session.harness,
                    placed_before=placed_before,
                )
            await db.commit()

    @asynccontextmanager
    async def prepare_session(
        self,
        *,
        session: SessionRef,
        token,
        env,
        precheck=None,
        runtime_factory=None,
        scratch: bool = False,
    ) -> AsyncIterator[PreparedSession]:
        assert isinstance(precheck, Placement)
        project_id, topic_id = session.project_id, session.topic_id
        started_at = time.monotonic()

        def mark(phase):
            logger.info(
                "central_setup_timing topic=%s phase=%s elapsed_ms=%.3f",
                topic_id,
                phase,
                (time.monotonic() - started_at) * 1000,
            )

        executor_id = precheck.machine
        agent_user_id, agent_handle = precheck.agent_user_id, precheck.agent_handle
        factory = self._session_factory or async_session_factory
        # Commit placement before opening the session process. Remote startup
        # must not hold a pool connection or block other room writers.
        async with factory() as db:
            room = await TopicService(db).lock_for_execution(topic_id)
            # The machine resolver supplies the room's agent; the signed launch
            # credential names the teammate actually taking this turn. A token
            # that names nobody (a platform capability) leaves the precheck
            # identity intact.
            actor = token_agent_handle(token)
            if actor and actor != agent_handle:
                user = await user_by_handle(db, actor)
                if user is None:
                    raise ScreenSetupError(say("screenAgentIdentityMissing"))
                agent_user_id, agent_handle = user.id, user.username
            mark("room_lock")
            resource = room.resource_id or room.id
            # Where THIS conversation was, resolved from its own row. A stale
            # one — an older generation of the room, or hands that have since
            # been handed to another executor — is no place at all: this turn
            # rents again rather than being refused for not matching what some
            # other session in the same room happens to be holding.
            session_row = await AgentSessionService(db).ensure(
                session.conversation_id, session.agent_handle, harness=session.harness
            )
            session_id = session_row.id
            place = session_row.place()
            await db.commit()
        if place is not None and (
            place.resource_id != str(resource)
            # 这一轮该落在哪台：租到手的是那双手，没租手的是这条会话自己的机器
            # ——``precheck`` 已经解析过，两种情况给的都是这一位。
            or (
                not precheck.deferred
                and (place.lease or {}).get("device_id") != executor_id
            )
        ):
            place = None
        center = self._center(place, precheck)
        if not center:
            raise ScreenSetupError(say("screenCentralMachineOffline"))
        await self._wait_for_session_host(center, session)
        values = {**(env or {}), "CHEESE_RESOURCE_ID": str(resource)}
        # Every room's session shares this machine's kernel: each runs under a
        # cap, so one cannot take the others down with it (#1544).
        if memory_max := session_memory_max(center):
            values[SESSION_MEMORY_ENV] = memory_max
        # The machine this session already holds, once its lease is ready.
        lease = (place.lease if place else None) if precheck.deferred else None
        leased = (
            lease
            if lease
            and lease.get("status", "ready") == "ready"
            and lease.get("workspace")
            else None
        )
        # Its credential names that lease, as the one the lease hands out does:
        # the executor route admits nothing else, and this is the one the
        # session's calls carry again after each turn rewrites it.
        # ``scratch``: a 支线, or a task its owner has not started, whose work
        # stays on the machine and never reaches the project
        # (`routes/execution.py`).
        token = bind_resource_token(
            token,
            str(resource),
            session_id=str(session_id),
            lease_generation=(leased or {}).get("generation"),
            scratch=scratch,
        )
        # 这一轮没有租手 (``precheck`` 说的)，所以它跑在这条会话自己的草稿区里：
        # 一个有界的一次性容器，开在会话机上，不是一个地点 (结论 19)。
        if precheck.deferred:
            target = {
                "kind": "deferred",
                "resource_id": str(resource),
                "session_id": str(session_id),
                # Named by the conversation, as the credential is: a task's
                # credential is refused on its room's path.
                "lease_path": (
                    f"/topics/{session.conversation_id}/sessions/{session_id}"
                    "/work-lease"
                ),
                "setup_env": {
                    key: value
                    for key, value in values.items()
                    if key.startswith(("CHEESE_", "GIT_"))
                },
                # A session started before its lease was ready sees the project
                # at a placeholder. One started on the machine sees it where the
                # machine holds it, and that path is part of what it was
                # started with (`screen_identity.launch_identity`), so the
                # first turn that finds a placeholder session idle, with its
                # machine there, relaunches it onto the machine.
                "workspace": leased["workspace"]
                if leased
                and await self._starts_on_machine(
                    leased, center, session.conversation_id, resource
                )
                else DEFERRED_WORKSPACE,
                "mcp_servers": [],
            }
            target = self._deferred_target(target)
        else:
            target = private_chat.scratch_target(project_id, resource, device_id=center)
        # The project's remote MCP servers need no machine: the platform holds
        # their credentials and calls them (`remote_mcp`), so they are the
        # session's from its start, in a room or a private chat alike. Only the
        # usable ones: a server someone still has to connect is a capability
        # line in the prompt instead. Part of the target, so connecting one
        # relaunches an idle session with it (`screen_identity.launch_identity`).
        async with factory() as db:
            remote = await remote_mcp.session_target(
                db, project_id, topic_id, agent_handle
            )
            own = await agent_stdio_servers(db, project_id, agent_handle)
        if remote is not None:
            target["remote_mcp"] = remote
        # The teammate's type's own stdio servers, as definitions: they run
        # where the session's commands run — the room's machine, beside the
        # checkout's `.mcp.json` ones, or a private chat's scratch container —
        # and `RemoteClient` hands the executor the definition with each call.
        # Part of the target, so a changed type relaunches an idle session.
        if own:
            target["agent_mcp"] = own
        location = {
            "device_id": center,
            "resource_id": str(resource),
            "channel": self.name,
            "session_id": str(session_id),
        }
        if runtime_factory is not None:
            location["runtime"] = runtime_factory(resource)
        # The room is still on the generation this was prepared for, and the
        # lease naming it is published, in one transaction. Checked after the
        # publish instead, it left a published lease for a generation that had
        # just been rotated away; checked with it, either the un-archive lands
        # first and this raises before a harness starts, or this lands first and
        # the un-archive's own `forget_room` deletes the lease again — after
        # which the executor route admits nothing for it (`execution.py` reads
        # that same row) and the screen is retired like any other orphan.
        #
        # The central bootstrap calls the scoped executor endpoint before it can
        # start Claude, so ownership is published before the screen is opened.
        async with factory() as db:
            room = await TopicService(db).lock_for_execution(topic_id)
            if (room.resource_id or room.id) != resource:
                raise ScreenSetupError(say("screenRoomReopenedOldEnvSkipped"))
            await AgentSessionService(db).remember_place(
                conversation_id=session.conversation_id,
                agent_handle=session.agent_handle,
                harness=session.harness,
                work_lease=(place.lease if place else None)
                if precheck.deferred
                else target,
                runtime_location=location,
            )
            await db.commit()
        mark("placement_committed")
        values.pop("CHEESE_ENVIRONMENT", None)
        values["CHEESE_EXECUTION_TARGET"] = json.dumps(target)
        yield PreparedSession(
            device_id=center,
            agent_user_id=agent_user_id,
            agent_handle=agent_handle,
            project_id=project_id,
            topic_id=topic_id,
            token=token,
            env=values,
        )
        mark("screen_ready")
