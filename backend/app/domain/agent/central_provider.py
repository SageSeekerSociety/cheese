"""Central Claude Code sessions with independently selected room execution."""

import asyncio
import base64
import json
import time
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.sandbox_auth import bind_resource_token
from app.domain.agent import execution, private_chat, session_transfer
from app.domain.agent.device_provider import (
    DeviceChannel,
    EnvironmentPreparationError,
    _preview_ws_url,
    device_home_dir,
    environment_status,
)
from app.domain.agent.harness.claude_code import ScreenSetupError, build_executor_launch
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws


class CentralChannel(DeviceChannel):
    def __init__(self, executor):
        super().__init__(hub=executor._hub, session_factory=executor._session_factory)
        self.executor = executor
        self.name = executor.name
        self.provisions_machine = executor.provisions_machine

    def available(self):
        return self.executor.available()

    async def prepare_topic(self, **kwargs):
        return await self.executor.prepare_topic(**kwargs)

    async def precheck(self, project_id, topic_id):
        factory = self._session_factory or async_session_factory
        async with factory() as session:
            room = await TopicService(session).get_or_404(topic_id)
            center = (
                room.session_placement["device_id"]
                if room.session_placement
                else settings.agent_session_device_id
            )
        if not center or not self._hub.is_online(center):
            raise ScreenSetupError("Claude Code 中心会话机器尚未配置或未连接")
        return await self.executor.precheck(project_id, topic_id)

    async def discover(self, device_id=None):
        factory = self._session_factory or async_session_factory
        found = []
        async with factory() as session:
            rooms = await session.scalars(
                select(Topic).where(Topic.session_placement.is_not(None))
            )
            for room in rooms:
                placement = room.session_placement
                if not placement or placement["channel"] != self.name:
                    continue
                center = placement["device_id"]
                if (device_id is None or center == device_id) and self._hub.is_online(
                    center
                ):
                    self._subscription_devices[room.id] = center
                    found.append((room.project_id, room.id, None, None))
            placed = {
                room.id
                for room in await session.scalars(
                    select(Topic).where(Topic.session_placement.is_not(None))
                )
                if room.session_placement
            }
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
        project_id,
        topic_id,
        token,
        env,
        launch,
        precheck=None,
        memory_scope=None,
        owner=None,
        turn_id=None,
    ):
        assert isinstance(precheck, tuple)
        executor_id, agent_user_id, agent_handle = precheck
        factory = self._session_factory or async_session_factory
        async with factory() as session:
            room = await TopicService(session).lock_for_execution(topic_id)
            resource = room.resource_id or room.id
            previous = room.session_placement
            center = (
                previous["device_id"] if previous else settings.agent_session_device_id
            )
            if not center or not self._hub.is_online(center):
                raise ScreenSetupError("本房间的 Claude Code 中心会话机器未连接")
            if previous and (
                previous["resource_id"] != str(resource)
                or previous["execution"]["device_id"] != executor_id
            ):
                raise ScreenSetupError("执行机器与本房间已经记录的位置不一致")
            values = {**(env or {}), "CHEESE_RESOURCE_ID": str(resource)}
            token = bind_resource_token(token, str(resource))
            if not previous and launch.resume_session_id:
                await self._transfer_history(
                    executor_id, center, project_id, resource, launch.resume_session_id
                )
            if room.is_private:
                target = private_chat.execution_target(
                    project_id, topic_id, resource, device_id=center
                )
                values.update(CHEESE_PRIVATE_CHAT="1", CHEESE_MEMORY_SCOPE="personal")
                if owner:
                    values["CHEESE_OWNER"] = owner
            else:
                if center == executor_id:
                    raise ScreenSetupError("项目执行机器与中心会话机器需要分别配置")
                api = await self.executor._device_api_base(executor_id)
                execute_env = {
                    **values,
                    "CHEESE_API": api,
                    "CHEESE_TOKEN": token,
                    "CHEESE_PROJECT": str(project_id),
                    "CHEESE_TOPIC": str(topic_id),
                    "CHEESE_AUTHOR": agent_handle,
                    "CHEESE_GIT_REMOTE": f"{api}/projects/{project_id}/git",
                    "CHEESE_GIT_BRANCH": ws.branch_for_tree(
                        ws.tree_for_place(topic_id)
                    ),
                    "CHEESE_BRANCH_URL": (
                        f"{api}/projects/{project_id}/git/branch/{topic_id}"
                    ),
                    "CHEESE_GIT_AUTHOR_NAME": agent_handle,
                    "CHEESE_GIT_AUTHOR_EMAIL": f"{agent_handle}@agent.cheese.local",
                    "GIT_AUTHOR_NAME": agent_handle,
                    "GIT_AUTHOR_EMAIL": f"{agent_handle}@agent.cheese.local",
                    "CHEESE_PREVIEW_URL": _preview_ws_url(api),
                    "CHEESE_HOOK_URL": f"{api}/sandbox/hooks/{topic_id}",
                }
                result = await self._hub.exec(
                    executor_id,
                    ["python3", "-"],
                    stdin=build_executor_launch(project_id, resource, execute_env),
                    timeout=660,
                )
                if result.get("exit") != 0 or result.get("truncated"):
                    raise ScreenSetupError(result.get("stderr") or "执行环境启动失败")
                info = json.loads(result["stdout"])
                target = {
                    "kind": "device",
                    "resource_id": str(resource),
                    "device_id": executor_id,
                    "home": device_home_dir(project_id, resource),
                    "workspace": info["workspace"],
                    "mcp_servers": info["mcp_servers"],
                    "url": (
                        f"{await self._device_api_base(center)}"
                        f"/topics/{topic_id}/execution/{resource}"
                    ),
                }
                await self._wait_executor(
                    project_id, resource, target, bool(values.get("CHEESE_ENVIRONMENT"))
                )
            placement = {
                "device_id": center,
                "resource_id": str(resource),
                "channel": self.name,
                "execution": target,
            }
            room.session_placement = placement
            # The central bootstrap calls the scoped executor endpoint before it
            # can start Claude. Publish ownership before opening the screen.
            await session.commit()
            room = await TopicService(session).lock_for_execution(topic_id)
            await session.refresh(room)
            if (room.resource_id or room.id) != resource:
                raise ScreenSetupError("房间已经重新打开，本轮没有启动旧执行环境")
            values.pop("CHEESE_ENVIRONMENT", None)
            values["CHEESE_EXECUTION_TARGET"] = json.dumps(target)
            screen = await self._ensure_screen(
                device_id=center,
                agent_user_id=agent_user_id,
                agent_handle=agent_handle,
                project_id=project_id,
                topic_id=topic_id,
                token=token,
                env=values,
                launch=launch,
                environment_before={},
            )
        self._subscription_devices[topic_id] = center
        return screen

    async def _transfer_history(self, source, center, project, resource, resume):
        if source == center:
            return
        program = Path(session_transfer.__file__).read_text()

        async def exchange(device, action, **values):
            payload = {
                "project": str(project),
                "resource": str(resource),
                "action": action,
                **values,
            }
            result = await self._hub.exec(
                device,
                ["python3", "-"],
                timeout=60,
                stdin=program
                + "\ntransfer(json.loads("
                + repr(json.dumps(payload))
                + "))\n",
            )
            if result.get("exit") != 0 or result.get("truncated"):
                raise ScreenSetupError(result.get("stderr") or "完整会话历史迁移失败")
            return json.loads(result["stdout"])

        deadline = time.monotonic() + 60
        stopped = await exchange(source, "stop", request_exit=True)
        while not stopped["stopped"]:
            if time.monotonic() >= deadline:
                raise ScreenSetupError("原机器上的会话尚未退出，尚未切换到中心")
            await asyncio.sleep(0.5)
            stopped = await exchange(source, "stop", request_exit=False)
        manifest = (await exchange(source, "list"))["files"]
        if not any(Path(item["path"]).name == resume + ".jsonl" for item in manifest):
            raise ScreenSetupError("原机器缺少要继续的完整会话文件，尚未切换到中心")
        for item in manifest:
            offset = 0
            while offset < item["size"] or item["size"] == 0 and offset == 0:
                chunk = await exchange(source, "read", path=item["path"], offset=offset)
                count = len(base64.b64decode(chunk["data"], validate=True))
                if not count and item["size"]:
                    raise ScreenSetupError("原会话文件在复制完成前结束")
                await exchange(
                    center,
                    "write",
                    path=item["path"],
                    offset=offset,
                    data=chunk["data"],
                    final=offset + count == item["size"],
                    sha256=item["sha256"],
                )
                offset += count
                if item["size"] == 0:
                    break
        if (await exchange(source, "list"))["files"] != manifest:
            raise ScreenSetupError("原会话记录仍在变化，尚未切换到中心")

    async def _wait_executor(self, project_id, resource, target, has_environment):
        deadline = time.monotonic() + (3660 if has_environment else 30)
        while True:
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
            else:
                ready = True
            if ready:
                try:
                    await execution.call(target, "ping", {}, hub=self._hub)
                    return
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
            if time.monotonic() >= deadline:
                raise ScreenSetupError("执行环境准备超时，请查看环境日志")
            await asyncio.sleep(0.2)
