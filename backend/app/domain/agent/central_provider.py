"""Central agent sessions with independently selected room execution."""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.sandbox_auth import bind_resource_token, token_agent_handle
from app.domain.agent import execution, private_chat
from app.domain.agent.device_provider import (
    DeviceChannel,
    EnvironmentPreparationError,
    _preview_ws_url,
    device_home_dir,
    environment_status,
)
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.harness.launch import LaunchPlan

# 名字而不是模块：本文件里 ``place`` 是一个局部变量（这条会话解析出来的地点），
# import 整个模块会在函数里被它盖掉。
from app.domain.agent.place import Lease, LeaseState
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.handles import topic_agent_handle
from app.domain.topic.services import TopicService
from app.domain.user.services import user_by_handle

logger = logging.getLogger(__name__)

# Setup normally clears this stretch in well under a second, and a wait that
# short has nothing to say. Past a few seconds the room is showing a spinner
# with no end in sight, so the wait has to name what is holding it — and keep
# naming it, because a line that only arrives once the wait is over is the very
# silence this reporting exists to end.
_WAIT_REPORT_AFTER = 5.0
_WAIT_REPORT_EVERY = 30.0


def _failure_reason(exc: Exception) -> str:
    """Why a call failed, as one short log field — never itself a raiser."""
    body = ""
    with contextlib.suppress(Exception):
        body = getattr(getattr(exc, "response", None), "text", "") or ""
    return " ".join((body or str(exc)).split())[:120] or type(exc).__name__


async def _report_while_waiting(topic_id, phase, began, detail):
    """Keep saying what a step is waiting on for as long as it waits."""
    delay = _WAIT_REPORT_AFTER
    while True:
        await asyncio.sleep(delay)
        logger.info(
            "central_setup_timing topic=%s phase=%s waited_ms=%.3f %s",
            topic_id,
            phase,
            (time.monotonic() - began) * 1000,
            detail,
        )
        delay = _WAIT_REPORT_EVERY


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
        self.provisions_machine = executor.provisions_machine
        # 手是执行机的，所以这条通道的供给就是被它包住的那条通道的供给——销毁权、
        # 休眠这两件事说的都是那台机器，不是中心会话机。
        self.supply = executor.supply
        # 而会话进程不在那台机器上：工具要从中心机再跳一程到执行机。把进程和工作
        # 区放在同一台机器上的骨架（pi）挂不到这条通道上。
        self.hands_here = False

    def available(self):
        return self.executor.available()

    async def prepare_topic(self, **kwargs):
        return await self.executor.prepare_topic(**kwargs)

    async def precheck(self, session: SessionRef, *, needs_place: bool) -> Placement:
        if needs_place:
            # 两条路共同的前提是会话机在线——会话本身跑在它上面。这一问只读机器，
            # 分身留给执行机那一步解析：在这里也解析一次，就是每一轮多借一次连接、
            # 多一次提交，而答案被丢掉。连接在这个 ``with`` 结束时就还了，不攥着
            # 它去池里要第二条 (#1312 正是并发轮次一起开场互相等到超时)。
            async with self._sessions() as db:
                await self._resolve_session_host(db, session)
            return await self.executor.precheck(session, needs_place=True)
        # 不碰文件、不跑命令的一轮不去租手 (结论 19，不变量 I2)：分身身份租手那条
        # 路也只从执行机之外取到，所以这一轮在所有执行机离线时照样跑得起来。
        return await self._session_host_agent(session)

    async def discover(self, device_id=None):
        factory = self._session_factory or async_session_factory
        scopes = []
        async with factory() as db:
            sessions = await AgentSessionService(db).placed_sessions()
        placed = {room_id for _, room_id, _, _, _ in sessions}
        for project_id, room_id, _handle, harness, place in sessions:
            if place.channel != self.name or harness != CLAUDE_CODE:
                continue
            center = place.machine
            if (device_id is None or center == device_id) and self._hub.is_online(
                center
            ):
                self._subscription_devices[room_id] = center
                scopes.append((project_id, room_id, center))
        found = await self.restore_screens(scopes)
        # Finish consuming turns that began before this deployment. Their next
        # opening transfers the transcript; no new prompt starts on the old host.
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
        # This route assigns an executor machine, so the plan has to be able to
        # install one and move a conversation onto it. A harness that runs where
        # the files already are answers neither, and belongs on the device
        # channel this one wraps. Codex reaches `prepare_session` directly with
        # exactly those two values and no screen at all, which is why the check
        # is here and not there.
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

        executor_id, agent_user_id, agent_handle, rented = precheck
        factory = self._session_factory or async_session_factory
        # Short transactions with the machine work between them, not one that
        # spans it. The room's row lock used to be taken on the first read and
        # held until the harness had started — through an executor install
        # (`_hub.exec`, up to 11 minutes) and an environment build
        # (`_wait_executor`, up to an hour). One starting room therefore held a
        # pool connection for that whole stretch, and queued every writer of its
        # row (a title, an archive, a read mark, an un-archive) behind it, each
        # of those holding a connection of its own while it waited.
        #
        # Nothing between the reads and the publish writes anything, so the lock
        # was guarding the publish alone — and the publish is atomic on its own
        # below.
        async with factory() as db:
            room = await TopicService(db).lock_for_execution(topic_id)
            # The machine resolver supplies a room identity; the signed launch
            # credential names the teammate actually taking this turn.
            # A room-scoped legacy token leaves the precheck identity intact.
            actor = token_agent_handle(token)
            if actor and actor not in (agent_handle, topic_agent_handle(topic_id)):
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
            place = await AgentSessionService(db).place(
                topic_id, session.agent_handle, harness=session.harness
            )
        if place is not None and (
            place.resource_id != str(resource)
            # 这一轮该落在哪台：租到手的是那双手，没租手的是这条会话自己的机器
            # ——``precheck`` 已经解析过，两种情况给的都是这一位。
            or (place.lease or {}).get("device_id") != executor_id
        ):
            place = None
        center = place.machine if place else settings.agent_session_device_id
        if not center or not self._hub.is_online(center):
            raise ScreenSetupError("本房间的 Claude Code 中心会话机器未连接")
        values = {**(env or {}), "CHEESE_RESOURCE_ID": str(resource)}
        token = bind_resource_token(token, str(resource))
        # 搬历史是把会话文件从租来的那双手搬回会话机；没租手的一轮，它们本来
        # 就在会话机上，没有源可搬。
        if rented and place is None and launch.resume_session_id:
            await launch.execution.transfer_history(
                self._hub,
                executor_id,
                center,
                project_id,
                resource,
                launch.resume_session_id,
            )
        # 记忆算谁的，只决定记忆算谁的：它跟着 ``memory_scope`` 走，不跟着「这
        # 一轮租没租手」走。
        if memory_scope == "personal":
            values["CHEESE_MEMORY_SCOPE"] = "personal"
            if owner:
                values["CHEESE_OWNER"] = owner
        # 这一轮没有租手 (``precheck`` 说的)，所以它跑在这条会话自己的草稿区里：
        # 一个有界的一次性容器，开在会话机上，不是一个地点 (结论 19)。
        if not rented:
            target = private_chat.scratch_target(project_id, resource, device_id=center)
        else:
            if center == executor_id:
                raise ScreenSetupError("项目执行机器与中心会话机器需要分别配置")
            api = await self.executor._device_api_base(executor_id)
            mark("executor_route")
            execute_env = {
                **values,
                "CHEESE_API": api,
                "CHEESE_TOKEN": token,
                "CHEESE_PROJECT": str(project_id),
                "CHEESE_TOPIC": str(topic_id),
                "CHEESE_AUTHOR": agent_handle,
                "CHEESE_GIT_AUTHOR_NAME": agent_handle,
                "CHEESE_GIT_AUTHOR_EMAIL": f"{agent_handle}@agent.cheese.local",
                "GIT_AUTHOR_NAME": agent_handle,
                "GIT_AUTHOR_EMAIL": f"{agent_handle}@agent.cheese.local",
                "CHEESE_PREVIEW_URL": _preview_ws_url(api),
                "CHEESE_HOOK_URL": f"{api}/sandbox/hooks/{topic_id}",
            }
            info = None
            if place is not None and place.lease:
                try:
                    running = await execution.call(
                        place.lease, "ping", {}, hub=self._hub
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 500:
                        raise
                    running = {}
                except RuntimeError:
                    # A stopped executor must take the installation path.
                    running = {}
                if launch.execution.can_prepare(running):
                    info = await execution.call(
                        place.lease,
                        "prepare",
                        launch.execution.payload_for(
                            project_id, resource, execute_env, running.get("files")
                        ),
                        hub=self._hub,
                    )
            if info is None:
                result = await self._hub.exec(
                    executor_id,
                    ["python3", "-"],
                    stdin=launch.execution.script(project_id, resource, execute_env),
                    timeout=660,
                )
                if result.get("exit") != 0 or result.get("truncated"):
                    raise ScreenSetupError(result.get("stderr") or "执行环境启动失败")
                info = json.loads(result["stdout"])
            mark("executor_launch")
            if info.get("upgrade_pending"):
                logger.info(
                    "executor_upgrade_deferred topic=%s release=%s desired=%s",
                    topic_id,
                    info.get("release"),
                    info.get("desired_release"),
                )
            target = {
                "kind": "device",
                "resource_id": str(resource),
                "device_id": executor_id,
                "home": device_home_dir(project_id, resource),
                # Where the installation actually put the executor, asked of
                # the installation. Whoever reaches it later must not derive
                # this: that reader runs in the device connection owner,
                # which an app deploy leaves alone — see
                # `agent.execution.executor_state`.
                "state": info["state"],
                "release": info.get("release"),
                "upgrade_pending": info.get("upgrade_pending", False),
                "desired_release": info.get("desired_release"),
                "workspace": info["workspace"],
                "mcp_servers": info["mcp_servers"],
                "url": (
                    f"{await self._device_api_base(center)}"
                    f"/topics/{topic_id}/execution/{resource}"
                ),
            }
            has_environment = bool(values.get("CHEESE_ENVIRONMENT"))
            # Bootstrap already checked the running executor and its environment
            # in one process. Fresh or unfinished environments still wait here.
            if not info.get("pid") or (
                has_environment and info.get("environment_status") != "ready"
            ):
                await self._wait_executor(
                    project_id, topic_id, resource, target, has_environment
                )
            target["context_tree"] = (
                info["context_tree"]
                if "context_tree" in info
                else await self._context_tree(topic_id, target, started_at)
            )
            mark("executor_ready")
        location = {
            "device_id": center,
            "resource_id": str(resource),
            "channel": self.name,
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
                # 租到手的一轮，这一行是一份租约，所以它带着自己的状态和期限出发
                # （在用 / 休眠 / 已归还，结论 24、39）。没租手的一轮写下的是这条
                # 会话自己的草稿区，不是一个地点（结论 19）——给它盖一个「在用」的
                # 租约章，回收路径就会去向一台根本没租过的机器要三张收据。
                work_lease=(
                    Lease(
                        machine=target["device_id"],
                        resource_id=target["resource_id"],
                        state=LeaseState.in_use,
                        expires_at=None,
                    ).into(target)
                    if rented
                    else target
                ),
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

    async def _wait_executor(
        self, project_id, topic_id, resource, target, has_environment
    ):
        started = time.monotonic()
        deadline = started + (3660 if has_environment else 30)
        report_at = started + _WAIT_REPORT_AFTER
        attempts = 0
        reported = False
        detail = "waiting_on=executor_ping"
        while True:
            attempts += 1
            if has_environment:
                status = await environment_status(
                    self._hub,
                    target["device_id"],
                    project_id,
                    resource,
                    wait_ready=True,
                )
                if status["state"] == "failed":
                    raise EnvironmentPreparationError(status)
                ready = status["state"] == "ready"
                detail = f"waiting_on=environment state={status['state']}"
            else:
                ready = True
            if ready:
                try:
                    await execution.call(target, "ping", {}, hub=self._hub)
                    if reported:
                        logger.info(
                            "central_setup_timing topic=%s phase=executor_wait_done "
                            "waited_ms=%.3f attempts=%d",
                            topic_id,
                            (time.monotonic() - started) * 1000,
                            attempts,
                        )
                    return
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 500:
                        raise
                    detail = (
                        "waiting_on=executor_ping status=500 "
                        f"reason={_failure_reason(exc)}"
                    )
                    if time.monotonic() >= deadline:
                        raise
                except RuntimeError as exc:
                    detail = f"waiting_on=executor_ping reason={_failure_reason(exc)}"
                    if time.monotonic() >= deadline:
                        raise
            now = time.monotonic()
            if now >= report_at:
                logger.info(
                    "central_setup_timing topic=%s phase=executor_wait "
                    "waited_ms=%.3f attempts=%d %s",
                    topic_id,
                    (now - started) * 1000,
                    attempts,
                    detail,
                )
                reported = True
                report_at = now + _WAIT_REPORT_EVERY
            if now >= deadline:
                raise ScreenSetupError("执行环境准备超时，请查看环境日志")
            await asyncio.sleep(0.2)

    async def _context_tree(self, topic_id, target, started_at):
        """Walk the room's workspace, saying so while the walk is what is slow."""
        began = time.monotonic()
        notice = asyncio.create_task(
            _report_while_waiting(
                topic_id, "context_tree", began, "waiting_on=context_fs"
            )
        )
        try:
            tree = await execution.call(
                target, "context_fs", {"operation": "tree"}, hub=self._hub
            )
        finally:
            # Cancel and let it unwind on its own: awaiting it here would make
            # the turn's own cancellation look like the notice's and swallow it.
            notice.cancel()
        now = time.monotonic()
        logger.info(
            "central_setup_timing topic=%s phase=context_tree elapsed_ms=%.3f "
            "took_ms=%.3f",
            topic_id,
            (now - started_at) * 1000,
            (now - began) * 1000,
        )
        return tree
