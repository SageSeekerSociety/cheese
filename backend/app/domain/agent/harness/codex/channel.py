"""Launch Codex on the session machine and reach tools on the room executor."""

import base64
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import settings
from app.core.db import async_session_factory
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.channel import (
    Placement,
    ScreenSetupError,
    discovery_missed,
    mint_session_token,
    startup_refused,
)
from app.domain.agent.harness.codex.launch import launch_identity, script
from app.domain.agent.harness.codex.runtime import Handle
from app.domain.agent.harness.launch import ExecutorLaunch
from app.domain.agent_session.services import AgentSessionService
from app.domain.library import service as library
from app.domain.project_skill.service import session_skill_files

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
        self.deferred_work = channel.deferred_work

    def available(self):
        return self.channel.available()

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

    async def ensure(
        self, session: SessionRef, opening: Opening, live: Handle | None = None
    ) -> Handle:
        precheck = await self.channel.precheck(session, needs_place=opening.needs_place)
        assert isinstance(precheck, Placement)
        agent = precheck.agent_handle
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

        token = mint_session_token(session.project_id, session.topic_id, agent)
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
                # The platform's own skills, as content: the runner writes them
                # where the session's Codex reads them (`tools.ship_skills`).
                "skills": session_skill_files(session.project_id),
            }
            if target["kind"] == "private":
                result = await self.channel._hub.exec(
                    prepared.device_id,
                    ["python3", "-"],
                    stdin=self.executor.private_script(target, env),
                    timeout=120,
                )
                if result.get("exit") != 0 or result.get("truncated"):
                    raise startup_refused(
                        result.get("stderr") or "Private executor startup failed",
                        harness="Codex",
                    )
            codex_config = (
                'model_provider = "cheese"\n'
                '[model_providers.cheese]\nname = "Cheese"\n'
                f"base_url = {json.dumps(api + '/llm/v1')}\n"
                'wire_api = "responses"\nenv_key = "CHEESE_TOKEN"\n'
                "requires_openai_auth = false\n[analytics]\nenabled = false\n"
            )
            identity = launch_identity(config)
            if (
                live is not None
                and (live.device_id, live.state, live.launch)
                == (prepared.device_id, state, identity)
                and opening.resume_token in (None, "", live.thread_id)
            ):
                # The runner that answered the last read was ensured with this
                # launch, so the host would only say so again.
                return live
            launch = {
                "state": state,
                "config": config,
                "codex_config": codex_config,
                "env": env,
            }
            status = await self._run(prepared.device_id, script(**launch, ship=False))
            if status.get("runner") == "missing":
                status = await self._run(
                    prepared.device_id, script(**launch, ship=True)
                )
            return Handle(
                session,
                prepared.device_id,
                state,
                status["thread_id"],
                agent,
                self._mirror(session, str(prepared.env["CHEESE_RESOURCE_ID"]) + agent),
                frozenset(status.get("capabilities") or ()),
                launch=identity,
            )

    async def _run(self, device_id: str, program: str) -> dict:
        """One launch step on the session host: its answer, or the reason it
        gave for having none, as the room is told it (`startup_refused`)."""
        result = await self.channel._hub.exec(
            device_id, ["python3", "-"], stdin=program, timeout=120
        )
        if result.get("exit") != 0 or result.get("truncated"):
            raise startup_refused(
                result.get("stderr") or "Codex startup failed", harness="Codex"
            )
        return json.loads(result["stdout"])

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
        for project_id, room_id, handle, harness, _token, place in sessions:
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
                discovery_missed(logger, "Codex", room_id, center, exc)
                continue
            if status["alive"]:
                ref = SessionRef(project_id, room_id, handle, harness=harness)
                agent = place.runtime["agent_handle"]
                handles.append(
                    Handle(
                        ref,
                        center,
                        place.runtime["state"],
                        status["thread_id"],
                        agent,
                        self._mirror(ref, place.resource_id + agent),
                        frozenset(status.get("capabilities") or ()),
                    )
                )
        return handles

    async def images(self, handle: Handle, images: list[dict]) -> list[str]:
        """Codex takes images inline, so the bytes travel with the message.

        Not also written to the machine, the way the pi channel next door
        already says it: the data URL below is what Codex reads, so the second
        copy was a file the platform put in the agent's checkout that nothing
        ever mentioned to it and nothing ever deleted — an untracked file in
        somebody's repository (结论 49，不变量 I21b).
        """
        return [
            "data:{};base64,{}".format(
                image["media_type"],
                base64.b64encode(
                    library.read_attachment(
                        handle.session.project_id,
                        handle.session.topic_id,
                        image["path"],
                    )
                ).decode(),
            )
            for image in images
        ]
