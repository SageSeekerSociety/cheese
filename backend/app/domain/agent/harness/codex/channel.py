"""Launch Codex on the session machine and reach tools on the room executor."""

import base64
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.codex.launch import script
from app.domain.agent.harness.codex.runtime import Handle
from app.domain.agent.harness.launch import ExecutorLaunch
from app.domain.agent_session.services import AgentSessionService
from app.domain.workspace import service as ws

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preparation:
    execution: ExecutorLaunch
    resume_session_id: str | None = None


class CodexChannel:
    builds_model_env = True

    def __init__(self, channel: CentralChannel, executor: ExecutorLaunch):
        self.channel = channel
        self.executor = executor
        self.name = channel.name
        self.provisions_machine = channel.provisions_machine

    def available(self):
        return self.channel.available()

    async def prepare_topic(self, **kwargs):
        return await self.channel.prepare_topic(**kwargs)

    def _mirror(self, session: SessionRef, agent: str) -> Path:
        return (
            Path(settings.workspace_root)
            / ".harness"
            / str(session.project_id)
            / str(session.topic_id)
            / "codex"
            / hashlib.sha256(agent.encode()).hexdigest()
            / "events.sqlite"
        )

    async def ensure(self, session: SessionRef, opening: Opening) -> Handle:
        precheck = await self.channel.precheck(session)
        agent = precheck[2]
        if opening.agent_handle and opening.agent_handle != agent:
            raise ScreenSetupError("The room teammate changed before session startup")
        metadata = {}

        def placement(resource):
            metadata.update(
                harness="codex",
                agent_handle=agent,
                state=(
                    f"$HOME/.cheese/harness/{session.project_id}/{resource}/codex/"
                    + hashlib.sha256(agent.encode()).hexdigest()
                ),
            )
            return metadata

        token = mint_scoped_token(
            project_id=str(session.project_id),
            topic_id=str(session.topic_id),
            ttl_s=30 * 24 * 3600,
            access_scope="project",
            agent_handle=agent,
        )
        async with self.channel.prepare_session(
            session=session,
            token=token,
            env=opening.env,
            launch=Preparation(self.executor),
            precheck=precheck,
            memory_scope=opening.memory_scope,
            owner=opening.owner,
            runtime_factory=placement,
        ) as prepared:
            state = metadata["state"]
            api = await self.channel._device_api_base(prepared.device_id)
            target = json.loads(prepared.env["CHEESE_EXECUTION_TARGET"])
            env = {
                **prepared.env,
                "CHEESE_API": api,
                "CHEESE_TOKEN": prepared.token,
                "CHEESE_PROJECT": str(session.project_id),
                "CHEESE_TOPIC": str(session.topic_id),
                "CHEESE_AUTHOR": agent,
            }
            config = {
                "binary": "~/.cheese/tools/codex/node_modules/.bin/codex",
                "opening": {**asdict(opening), "env": None, "agent_handle": agent},
                "execution_target": target,
                "mcp_servers": target["mcp_servers"],
            }
            if target["kind"] == "private":
                result = await self.channel._hub.exec(
                    prepared.device_id,
                    ["python3", "-"],
                    stdin=self.executor.private_script(target, env),
                    timeout=120,
                )
                if result.get("exit") != 0 or result.get("truncated"):
                    raise ScreenSetupError(
                        result.get("stderr") or "Private executor startup failed"
                    )
            codex_config = (
                'model_provider = "cheese"\n'
                '[model_providers.cheese]\nname = "Cheese"\n'
                f"base_url = {json.dumps(api + '/llm/v1')}\n"
                'wire_api = "responses"\nenv_key = "CHEESE_TOKEN"\n'
                "requires_openai_auth = false\n[analytics]\nenabled = false\n"
            )
            result = await self.channel._hub.exec(
                prepared.device_id,
                ["python3", "-"],
                stdin=script(
                    state=state, config=config, codex_config=codex_config, env=env
                ),
                timeout=120,
            )
            if result.get("exit") != 0 or result.get("truncated"):
                raise ScreenSetupError(result.get("stderr") or "Codex startup failed")
            status = json.loads(result["stdout"])
            return Handle(
                session,
                prepared.device_id,
                state,
                status["thread_id"],
                agent,
                self._mirror(session, str(prepared.env["CHEESE_RESOURCE_ID"]) + agent),
            )

    async def call(self, handle: Handle, method: str, params: dict) -> dict:
        return await self.channel._hub.call_executor(
            handle.device_id,
            handle.state,
            method,
            params,
        )

    async def discover(self, device_id: str | None) -> list[Handle]:
        factory = self.channel._session_factory or async_session_factory
        handles = []
        async with factory() as db:
            sessions = await AgentSessionService(db).placed_sessions()
        for project_id, room_id, handle, harness, place in sessions:
            if harness != "codex" or place.channel != self.name:
                continue
            center = place.machine
            if device_id is not None and center != device_id:
                continue
            if not self.channel._hub.is_online(center):
                continue
            try:
                status = await self.channel._hub.call_executor(
                    center, place.runtime["state"], "ping", {}, timeout=15
                )
            except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
                logger.warning(
                    "Codex discovery failed topic=%s device=%s: %s",
                    room_id,
                    center,
                    exc,
                )
                continue
            if status["alive"]:
                ref = SessionRef(project_id, room_id, handle, harness)
                agent = place.runtime["agent_handle"]
                handles.append(
                    Handle(
                        ref,
                        center,
                        place.runtime["state"],
                        status["thread_id"],
                        agent,
                        self._mirror(ref, place.resource_id + agent),
                    )
                )
        return handles

    async def images(self, handle: Handle, images: list[dict]) -> list[str]:
        if not images:
            return []
        urls = []
        for image in images:
            data = ws.read_attachment(
                handle.session.project_id, handle.session.topic_id, image["path"]
            )
            encoded = base64.b64encode(data).decode()
            await self.call(
                handle,
                "stage_file",
                {"path": image["path"], "data": encoded},
            )
            urls.append(f"data:{image['media_type']};base64,{encoded}")
        return urls
