"""Central agent sessions with independently selected room execution."""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.sandbox_auth import bind_resource_token, token_agent_handle
from app.domain.agent import private_chat
from app.domain.agent.device_provider import (
    DeviceChannel,
)
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.harness.launch import LaunchPlan
from app.domain.agent_session.services import AgentSessionService
from app.domain.topic.services import TopicService
from app.domain.user.services import user_by_handle

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedSession:
    device_id: str
    agent_user_id: int
    agent_handle: str
    project_id: uuid.UUID
    topic_id: uuid.UUID
    token: str
    env: dict[str, str]


class CentralChannel(DeviceChannel):
    def __init__(self, executor):
        super().__init__(hub=executor._hub, session_factory=executor._session_factory)
        self.executor = executor
        self.name = executor.name
        self.provisions_machine = False
        self.deferred_work = True
        # 手是执行机的，所以这条通道的供给就是被它包住的那条通道的供给：一台机器
        # 归哪条通道认领，说的是那台机器，不是中心会话机。
        self.supply = executor.supply
        # 而会话进程不在那台机器上：工具要从中心机再跳一程到执行机。把进程和工作
        # 区放在同一台机器上的骨架（pi）挂不到这条通道上。
        self.hands_here = False

    def available(self):
        return bool(
            settings.agent_session_device_id
            and self._hub.is_online(settings.agent_session_device_id)
        )

    async def prepare_topic(self, **kwargs):
        return True, ""

    async def precheck(self, session: SessionRef, *, needs_place: bool) -> Placement:
        own = await self._session_host_agent(session)
        return own._replace(deferred=needs_place)

    async def discover(self, device_id=None):
        factory = self._session_factory or async_session_factory
        scopes = []
        async with factory() as db:
            sessions = await AgentSessionService(db).placed_sessions()
        placed = {room_id for _, room_id, _, _, _ in sessions}
        # 这条通道认领的是「落在我这里的会话」，不是「某个骨架的会话」：同一条
        # CentralChannel 被 Claude Code 和 Codex 两个 runtime 各包一次
        # (``compute.build_compute_pool``)，所以通道答不出哪一条是谁的。骨架的名
        # 字原样交回去当 ``running``，认领由 runtime 拿自己的 ``self.harness`` 去
        # 做（``Channel.discover`` 的契约就是这么写的）。
        runs: dict[uuid.UUID, str] = {}
        for project_id, room_id, _handle, harness, place in sessions:
            if place.channel != self.name:
                continue
            center = place.machine
            if (device_id is None or center == device_id) and self._hub.is_online(
                center
            ):
                self._subscription_devices[room_id] = center
                runs[room_id] = harness
                scopes.append((project_id, room_id, center))
        found = [
            (project_id, room_id, screen, runs.get(room_id))
            for project_id, room_id, screen, _ in await self.restore_screens(scopes)
        ]
        # Finish consuming turns that began before this deployment. This only
        # reattaches live screens; it does not hydrate a lost session transcript.
        for scope in await self.executor.discover(device_id):
            if scope[1] not in placed:
                found.append(scope)
                old_device = self.executor._subscription_devices.get(scope[1])
                if old_device:
                    self._subscription_devices[scope[1]] = old_device
        return found

    async def ensure_ready(
        self,
        *,
        session: SessionRef,
        token,
        env,
        launch,
        precheck=None,
        memory_scope=None,
        owner=None,
        turn_id=None,
    ):
        # Central screens require a launch plan supporting remote execution.
        # Their independent hands are acquired later by the first project tool.
        if not isinstance(launch, LaunchPlan):
            # `harness` is read through getattr because this branch is exactly
            # the one where the object did not satisfy the protocol that
            # guarantees it — a message that crashes reports nothing.
            named = getattr(launch, "harness", type(launch).__name__)
            raise ScreenSetupError(f"{named} 不能在独立执行机上运行，它没有执行器")
        async with self.prepare_session(
            session=session,
            token=token,
            env=env,
            launch=launch,
            precheck=precheck,
            memory_scope=memory_scope,
            owner=owner,
            turn_id=turn_id,
        ) as prepared:
            return await self._ensure_screen(
                device_id=prepared.device_id,
                agent_user_id=prepared.agent_user_id,
                agent_handle=prepared.agent_handle,
                project_id=prepared.project_id,
                topic_id=prepared.topic_id,
                token=prepared.token,
                env=prepared.env,
                launch=launch,
                environment_before={},
            )

    @asynccontextmanager
    async def prepare_session(
        self,
        *,
        session: SessionRef,
        token,
        env,
        launch,
        precheck=None,
        memory_scope=None,
        owner=None,
        turn_id=None,
        runtime_factory=None,
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
                    raise ScreenSetupError("本轮 agent 身份不存在，无法启动执行机")
                agent_user_id, agent_handle = user.id, user.username
            mark("room_lock")
            resource = room.resource_id or room.id
            # Where THIS conversation was, resolved from its own row. A stale
            # one — an older generation of the room, or hands that have since
            # been handed to another executor — is no place at all: this turn
            # rents again rather than being refused for not matching what some
            # other session in the same room happens to be holding.
            session_row = await AgentSessionService(db).ensure(
                topic_id, session.agent_handle, harness=session.harness
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
        center = place.machine if place else settings.agent_session_device_id
        if not center:
            raise ScreenSetupError("本房间的 Claude Code 中心会话机器未连接")
        await self._wait_for_session_host(center, session)
        values = {**(env or {}), "CHEESE_RESOURCE_ID": str(resource)}
        token = bind_resource_token(token, str(resource), session_id=str(session_id))
        # 记忆算谁的，只决定记忆算谁的：它跟着 ``memory_scope`` 走，不跟着「这
        # 一轮租没租手」走。
        if memory_scope == "personal":
            values["CHEESE_MEMORY_SCOPE"] = "personal"
            if owner:
                values["CHEESE_OWNER"] = owner
        # 这一轮没有租手 (``precheck`` 说的)，所以它跑在这条会话自己的草稿区里：
        # 一个有界的一次性容器，开在会话机上，不是一个地点 (结论 19)。
        if precheck.deferred:
            target = {
                "kind": "deferred",
                "resource_id": str(resource),
                "session_id": str(session_id),
                "lease_path": f"/topics/{topic_id}/sessions/{session_id}/work-lease",
                "setup_env": {
                    key: value
                    for key, value in values.items()
                    if key.startswith(("CHEESE_", "GIT_"))
                },
                "workspace": "/unavailable-project",
                "mcp_servers": [],
            }
        else:
            target = private_chat.scratch_target(project_id, resource, device_id=center)
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
                raise ScreenSetupError("房间已经重新打开，本轮没有启动旧执行环境")
            await AgentSessionService(db).remember_place(
                topic_id=topic_id,
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
        self._subscription_devices[topic_id] = center
