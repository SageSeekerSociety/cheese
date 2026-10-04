"""把中心会话机包成 pi 的 SessionChannel：会话在中心机，手在房间的执行机。

pi runs where every other room's session runs — on the central session host —
and reaches the room's machine for its files and commands (`machine.py`,
#1106), the way Claude Code and Codex do. So a pi room talks before it has a
machine, takes one only when its work needs one, and every harness's room is
placed, leased and recovered by the one central channel (`CentralChannel`).

The launch is Codex's shape: a Python script over the connector's stdin
(`launch.on_host`) that leaves the room's runner running on the host
(`host.configure`), which is then reached through ``hub.call_executor`` — the
connector derives a socket path from the state directory the backend recorded
and relays one JSON line each way (``cli/internal/host/executor.go``).
"""

import base64
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.db import async_session_factory
from app.domain.agent import machine_launcher
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
from app.domain.agent.harness.launch import ExecutorLaunch
from app.domain.agent.harness.pi.launch import arguments, extension, on_host
from app.domain.agent.harness.pi.runtime import PI, Handle
from app.domain.agent.harness.prompt import PLATFORM_NOTICE
from app.domain.agent_session.services import AgentSessionService
from app.domain.library import service as library
from app.domain.project_skill.service import session_skill_files

logger = logging.getLogger(__name__)

#: How long a launch may take on the session host: the first one after a pin
#: bump downloads pi, and every one waits for the runner to answer
#: (`host.STARTUP_S`).
LAUNCH_TIMEOUT_S = 900


@dataclass(frozen=True)
class Preparation:
    execution: ExecutorLaunch
    resume_session_id: str | None = None


class PiChannel:
    builds_model_env = True

    def __init__(self, channel: CentralChannel, executor: ExecutorLaunch):
        self.channel = channel
        self.executor = executor
        self.name = channel.name
        self.deferred_work = channel.deferred_work

    def available(self) -> bool:
        return self.channel.available()

    def _mirror(self, session: SessionRef, agent: str) -> Path:
        return (
            Path(settings.workspace_root)
            / ".harness"
            / str(session.project_id)
            / str(session.topic_id)
            / PI
            / hashlib.sha256(agent.encode()).hexdigest()
            / "entries.sqlite"
        )

    async def ensure(
        self, session: SessionRef, opening: Opening, live: Handle | None = None
    ) -> Handle:
        precheck = await self.channel.precheck(session, needs_place=opening.needs_place)
        assert isinstance(precheck, Placement)
        agent = precheck.agent_handle
        if opening.agent_handle and opening.agent_handle != agent:
            raise ScreenSetupError("The room teammate changed before session startup")
        placed: dict = {}

        def placement(resource):
            placed.update(
                harness=PI,
                agent_handle=agent,
                state=machine_launcher.state_dir(
                    session.project_id, resource, PI, agent
                ),
            )
            return placed

        token = mint_session_token(session.project_id, session.topic_id, agent)
        async with self.channel.prepare_session(
            session=session,
            token=token,
            env=opening.env,
            launch=Preparation(self.executor, opening.resume_token),
            precheck=precheck,
            memory_scope=opening.memory_scope,
            owner=opening.owner,
            runtime_factory=placement,
        ) as prepared:
            state = placed["state"]
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
            if target["kind"] == "private":
                await self._run(
                    prepared.device_id,
                    self.executor.private_script(target, env),
                    timeout=120,
                )
            model = opening.model or settings.agent_model
            launch = on_host(
                state=state,
                config={
                    "opening": {
                        "system_prompt": opening.system_prompt,
                        "resume_token": opening.resume_token,
                        "model": model,
                        "agent_handle": agent,
                    },
                    "args": arguments(model),
                    "execution_target": target,
                    # Carried as content, not as paths: these are the
                    # platform's files, and the session host has no copy of
                    # them. The runner writes them and points pi at them.
                    "skills": session_skill_files(session.project_id),
                    "extension": extension(),
                    # The marker platform instructions carry in this room,
                    # so the one the extension raises is not a second
                    # convention the agent has to learn.
                    "notice": PLATFORM_NOTICE,
                },
                api_base=api,
                model=model,
                env=env,
            )
            if (
                live is not None
                and (live.device_id, live.state, live.contract)
                == (prepared.device_id, state, launch.contract)
                and opening.resume_token in (None, "", live.session_id)
            ):
                # The runner that answered the last read was started with this
                # launch, so the host would only say so again.
                return live
            status = await self._run(
                prepared.device_id,
                launch.program(ship=False),
                timeout=LAUNCH_TIMEOUT_S,
            )
            if status.get("runner") == "missing":
                status = await self._run(
                    prepared.device_id,
                    launch.program(ship=True),
                    timeout=LAUNCH_TIMEOUT_S,
                )
            return Handle(
                session,
                prepared.device_id,
                state,
                status["session_id"],
                agent,
                self._mirror(session, str(prepared.env["CHEESE_RESOURCE_ID"]) + agent),
                frozenset(status.get("capabilities") or ()),
                contract=status.get("contract", ""),
            )

    async def _run(self, device_id: str, program: str, *, timeout: int) -> dict:
        """One launch step on the session host: its answer, or the reason it
        gave for having none, as the room is told it (`startup_refused`)."""
        result = await self.channel._hub.exec(
            device_id, ["python3", "-"], stdin=program, timeout=timeout
        )
        if result.get("exit") != 0 or result.get("truncated"):
            raise startup_refused(
                result.get("stderr") or "pi did not start", harness=PI
            )
        output = (result.get("stdout") or "").strip()
        return json.loads(output.splitlines()[-1]) if output else {}

    async def call(self, handle: Handle, method: str, params: dict) -> dict:
        return await self.channel._hub.call_executor(
            handle.device_id, handle.state, method, params
        )

    async def discover(self, device_id: str | None) -> list[Handle]:
        factory = self.channel._session_factory or async_session_factory
        handles: list[Handle] = []
        # Per-conversation observations from THIS round, keyed by the stored
        # pointer identity (FB-56 legacy③): "alive" only on an alive=true
        # answer carrying the conversation's id; "dead" only when an
        # alive=false answer's session_id matches the stored resume token
        # exactly — the terminal answer must bind to the stored provenance.
        # Everything else (offline machine, timeout, call error, a missing
        # or mismatched id) is "unknown" and closes nothing.
        self.last_outcomes: dict[tuple[uuid.UUID, str, str], str] = {}
        async with factory() as db:
            sessions = await AgentSessionService(db).placed_sessions()
        for project_id, room_id, handle, harness, resume_token, place in sessions:
            if harness != PI or place.channel != self.name:
                continue
            center = place.machine
            if device_id is not None and center != device_id:
                continue
            key = (room_id, handle, resume_token or "")
            if not self.channel._hub.is_online(center):
                self.last_outcomes[key] = "unknown"
                continue
            try:
                status = await self.channel._hub.call_executor(
                    center, place.runtime["state"], "ping", {}, timeout=15
                )
            except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
                discovery_missed(logger, PI, room_id, center, exc)
                self.last_outcomes[key] = "unknown"
                continue
            alive = status.get("alive")
            if alive is not True:
                # Only an explicit alive=False is a terminal answer, and only
                # when its session_id binds to the stored resume token. The
                # hub hands the RPC result through without field validation:
                # a missing or non-boolean ``alive`` is no observation at
                # all, and no observation is unknown, never dead (FB-56).
                self.last_outcomes[key] = (
                    "dead"
                    if alive is False
                    and resume_token
                    and status.get("session_id") == resume_token
                    else "unknown"
                )
                continue
            self.last_outcomes[key] = "alive"
            ref = SessionRef(project_id, room_id, handle, harness=harness)
            agent = place.runtime["agent_handle"]
            handles.append(
                Handle(
                    ref,
                    center,
                    place.runtime["state"],
                    status["session_id"],
                    agent,
                    self._mirror(ref, place.resource_id + agent),
                    frozenset(status.get("capabilities") or ()),
                )
            )
        return handles

    async def images(self, handle: Handle, images: list[dict]) -> list[dict]:
        """pi takes images inline, so the bytes travel with the message.

        Not also written to the machine's workspace: an agent that wants the
        file rather than the picture has no way to ask for it yet, and planting
        one it cannot be told about is a file nobody deletes.
        """
        return [
            {
                "data": base64.b64encode(
                    library.read_attachment(
                        handle.session.project_id,
                        handle.session.topic_id,
                        image["path"],
                    )
                ).decode(),
                "mimeType": image["media_type"],
            }
            for image in images
        ]
