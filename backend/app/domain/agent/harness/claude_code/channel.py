"""Start Claude Code on the session host, and reach the runner that owns it.

The launch is the central channel's: a screen on the session host whose program
is the runner (``device_launch.launch_holes``), prepared with the room's
placement, its credentials and the executor its tools run on. What this adds is
the runner's address. The runner keeps its state where the connector derives a
socket from (``machine_launcher.state_dir``), and ``hub.call_executor`` relays
one JSON line each way to it, so reaching a session needs no transport of its
own.
"""

import asyncio
import base64
import hashlib
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import machine_launcher
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import CLAUDE_CODE, Opening, SessionRef
from app.domain.agent.harness.channel import (
    SESSION_TOKEN_TTL_S,
    Placement,
    ScreenSetupError,
)
from app.domain.agent.harness.claude_code.runner import LAUNCH, ended
from app.domain.agent.harness.claude_code.runtime import Handle
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent_session.services import AgentSessionService
from app.domain.library import service as library

if TYPE_CHECKING:
    # The central channel's module imports this package (for the tunnel probe
    # and the release), so the class is only named here, never loaded.
    from app.domain.agent.central_provider import CentralChannel

logger = logging.getLogger(__name__)

# How long a room's first call waits for the runner to bind its socket, and how
# often it asks. The screen reports ready as soon as the machine has a program
# running, while the runner binds its socket only after the launcher has
# prepared the session (the executor's view of the project is mounted first),
# so a cold room's first question often arrives before there is anything to
# answer it. A runner that has already ended says so in its log, and the wait
# stops there.
STARTUP_WAIT_S = 120.0
STARTUP_POLL_S = 1.0


class ClaudeCodeChannel:
    builds_model_env = True

    def __init__(self, channel: "CentralChannel"):
        self.channel = channel
        self.name = channel.name
        self.provisions_machine = channel.provisions_machine
        self.deferred_work = channel.deferred_work

    def available(self) -> bool:
        return self.channel.available()

    async def prepare_topic(self, **kwargs):
        return await self.channel.prepare_topic(**kwargs)

    def _mirror(self, session: SessionRef, key: str) -> Path:
        return (
            Path(settings.workspace_root)
            / ".harness"
            / str(session.project_id)
            / str(session.topic_id)
            / CLAUDE_CODE
            / hashlib.sha256(key.encode()).hexdigest()
            / "records.sqlite"
        )

    async def ensure(self, session: SessionRef, opening: Opening) -> Handle:
        precheck = await self.channel.precheck(session, needs_place=opening.needs_place)
        assert isinstance(precheck, Placement)
        # WHO acts with it. The caller pins a teammate when a message named one;
        # unnamed, it is the agent the machine resolver resolved for this room.
        # The room itself never answers: it may seat several agents, and a name
        # signed into a token cannot be taken back.
        agent = opening.agent_handle or precheck.agent_handle
        token = mint_scoped_token(
            project_id=str(session.project_id),
            topic_id=str(session.topic_id),
            ttl_s=SESSION_TOKEN_TTL_S,
            access_scope="project",
            agent_handle=agent,
        )
        placed: dict = {}
        launch = uuid.uuid4().hex

        def runtime(resource) -> dict:
            placed.update(
                harness=CLAUDE_CODE,
                agent_handle=agent,
                resource=str(resource),
                state=machine_launcher.state_dir(
                    session.project_id, resource, CLAUDE_CODE, agent
                ),
            )
            return {key: placed[key] for key in ("harness", "agent_handle", "state")}

        screen = await self.channel.ensure_ready(
            session=session,
            token=token,
            env={**(opening.env or {}), LAUNCH: launch},
            memory_scope=opening.memory_scope,
            owner=opening.owner,
            turn_id=None,
            launch=ClaudeLaunch(
                system_prompt=opening.system_prompt,
                model=opening.model,
                resume_session_id=opening.resume_token,
            ),
            precheck=precheck,
            runtime_factory=runtime,
        )
        status = await self._greet(screen.device_id, placed["state"], launch)
        return Handle(
            session,
            screen.device_id,
            placed["state"],
            status["session_id"],
            agent,
            self._mirror(session, placed["resource"] + agent),
        )

    async def _greet(self, device_id: str, state: str, launch: str) -> dict:
        """The first call into a runner that may still be starting.

        A refused socket is retried while the runner may still be on its way,
        and reported as soon as its log says this launch has ended: the socket
        went with it, and nothing will answer however long the room waits.
        Past the window with no such record, the session is not coming either,
        and whatever the log last said travels back with the refusal.
        """
        deadline = time.monotonic() + STARTUP_WAIT_S
        while True:
            try:
                status = await self.channel._hub.call_executor(
                    device_id, state, "ping", {}
                )
                if status.get("alive"):
                    return status
                failure: Exception = RuntimeError("Claude Code exited")
            except DeviceOffline:
                raise
            except Exception as exc:  # noqa: BLE001 — see pi/channel.py `_greet`
                # The hub the backend holds is usually a proxy whose failures
                # arrive as HTTP errors; whatever shape it takes, a ping that
                # does not come back means the session cannot be reached yet.
                failure = exc
            reason = await self._ended(device_id, state, launch)
            if not reason and time.monotonic() >= deadline:
                reason = await self._why(device_id, state, failure)
            if reason:
                raise ScreenSetupError("Claude Code 会话进程没有起来：" + reason)
            await asyncio.sleep(STARTUP_POLL_S)

    async def _ended(self, device_id: str, state: str, launch: str) -> str:
        """What this launch's runner wrote on ending, or nothing yet.

        The log is appended across launches, and an earlier launch's ending
        says nothing about this one, so only the part from this launch's own
        record on is read.
        """
        try:
            result = await self.channel._hub.exec(
                device_id,
                [
                    "sh",
                    "-c",
                    f'sed -n "/{ended(launch)}/,\\$p" "{state}/runner.log" '
                    "2>/dev/null | tail -c 1200",
                ],
                timeout=15,
            )
        except Exception:  # noqa: BLE001 — a failed read is not the runner's ending
            return ""
        return (result.get("stdout") or "").strip()

    async def _why(self, device_id: str, state: str, failure: Exception) -> str:
        """The log's last words, when the window ran out with no record of
        this launch ending: a runner that died before it could name its
        launch still left a traceback there."""
        try:
            result = await self.channel._hub.exec(
                device_id,
                ["sh", "-c", f'tail -c 1200 "{state}/runner.log" 2>/dev/null'],
                timeout=15,
            )
        except Exception:  # noqa: BLE001 — a failed read must not replace the failure
            return str(failure)
        return (result.get("stdout") or "").strip() or str(failure)

    async def call(self, handle: Handle, method: str, params: dict) -> dict:
        return await self.channel._hub.call_executor(
            handle.device_id, handle.state, method, params
        )

    async def discover(self, device_id: str | None) -> list[Handle]:
        """The sessions of this channel that outlived the backend, still running.

        Their screens are adopted into the hub first, so the next turn reuses
        the one that is there instead of opening a second.
        """
        await self.channel.restore(device_id)
        factory = self.channel._session_factory or async_session_factory
        handles: list[Handle] = []
        async with factory() as db:
            sessions = await AgentSessionService(db).placed_sessions()
        for project_id, room_id, handle, harness, place in sessions:
            if harness != CLAUDE_CODE or place.channel != self.name:
                continue
            runtime = place.runtime or {}
            if "state" not in runtime:
                continue
            center = place.machine
            if device_id is not None and center != device_id:
                continue
            if not self.channel._hub.is_online(center):
                continue
            try:
                status = await self.channel._hub.call_executor(
                    center, runtime["state"], "ping", {}, timeout=15
                )
            except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
                logger.warning(
                    "Claude Code discovery failed topic=%s device=%s: %s",
                    room_id,
                    center,
                    exc,
                )
                continue
            if not status.get("alive"):
                continue
            ref = SessionRef(project_id, room_id, handle, harness=harness)
            agent = runtime["agent_handle"]
            handles.append(
                Handle(
                    ref,
                    center,
                    runtime["state"],
                    status["session_id"],
                    agent,
                    self._mirror(ref, place.resource_id + agent),
                )
            )
        return handles

    async def images(self, handle: Handle, images: list[dict]) -> list[dict]:
        """Claude Code takes images inline, so the bytes travel with the message.

        Not also written to the machine: an attachment the platform puts in the
        agent's checkout is an untracked file in somebody's repository (结论 49，
        不变量 I21b), and the model reads the picture from the message itself.
        """
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image["media_type"],
                    "data": base64.b64encode(
                        library.read_attachment(
                            handle.session.project_id,
                            handle.session.topic_id,
                            image["path"],
                        )
                    ).decode(),
                },
            }
            for image in images
        ]
