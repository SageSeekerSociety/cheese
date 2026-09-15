"""把设备通道包成 pi 的 SessionChannel —— 同一台机器，没有 executor。

This is the first backend in the pool to run on a bare ``DeviceChannel``. Every
other one wraps it in ``CentralChannel``, which assigns a separate executor
machine and forbids the two being the same box; pi does not need that. It does
not run on a subscription, so there is no credential to hide behind a proxy we
control, and no reason to put the agent anywhere but where the files are.

What that removes: an executor process, a second machine, the environment
preparation happening somewhere other than where the work does, and every tool
call crossing a network. What it costs: the workspace machine now runs the
agent too, which is the deal a person who lends us their machine was making
anyway.

Reaching the runner needs no new transport either. ``hub.call_executor`` looks
like an executor thing and is not: the connector derives a socket path from the
state directory the backend recorded and relays one JSON line each way
(``cli/internal/host/executor.go``). pi's runner binds the socket that path
names, so the existing channel carries it unchanged.
"""

import asyncio
import base64
import hashlib
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import machine_launcher
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.pi.device_launch import PiLaunch
from app.domain.agent.harness.pi.runtime import PI, Handle
from app.domain.topic.models import Topic
from app.domain.workspace import service as ws

SESSION_TOKEN_TTL_S = 30 * 24 * 3600

# How long a room's first call waits for the runner to bind its socket, and how
# often it asks. Generous because the cost of being wrong is asymmetric: waiting
# too long delays a turn that was going to fail anyway, while giving up too
# early refuses a session that was seconds from answering — which is what a new
# room got every time, since the wait was zero.
STARTUP_WAIT_S = 120.0
STARTUP_POLL_S = 1.0


class PiChannel:
    def __init__(self, channel: DeviceChannel):
        self.channel = channel
        self.name = channel.name
        self.provisions_machine = channel.provisions_machine
        self.builds_model_env = channel.builds_model_env

    def available(self) -> bool:
        return self.channel.available()

    async def prepare_topic(self, **kwargs):
        return await self.channel.prepare_topic(**kwargs)

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

    async def ensure(self, session: SessionRef, opening: Opening) -> Handle:
        precheck = await self.channel.precheck(session.project_id, session.topic_id)
        assert isinstance(precheck, tuple)
        device_id, _agent_user_id, agent = precheck
        if opening.agent_handle and opening.agent_handle != agent:
            raise ScreenSetupError("The room teammate changed before session startup")
        token = mint_scoped_token(
            project_id=str(session.project_id),
            topic_id=str(session.topic_id),
            ttl_s=SESSION_TOKEN_TTL_S,
            access_scope="project",
            agent_handle=agent,
        )
        screen = await self.channel.ensure_ready(
            project_id=session.project_id,
            topic_id=session.topic_id,
            token=token,
            env=opening.env,
            memory_scope=opening.memory_scope,
            owner=opening.owner,
            turn_id=None,
            launch=PiLaunch(
                system_prompt=opening.system_prompt,
                model=opening.model or settings.agent_model,
                resume_session_id=opening.resume_token,
                agent_handle=agent,
            ),
            precheck=precheck,
        )
        resource_id = screen.resource_id or session.topic_id
        state = machine_launcher.state_dir(session.project_id, resource_id, PI, agent)
        # The runner chose the session id (or resumed the one it was given), and
        # it is the only thing that knows which: asking beats recording a second
        # copy that a rebuilt room could disagree with.
        status = await self._greet(device_id, state)
        if not status.get("alive"):
            raise ScreenSetupError("pi 会话进程没有起来")
        await self._remember(session, device_id, resource_id, state, agent)
        return Handle(
            session,
            device_id,
            state,
            status["session_id"],
            agent,
            self._mirror(session, str(resource_id) + agent),
        )

    async def _greet(self, device_id: str, state: str) -> dict:
        """The first call into a runner that may still be starting.

        The runner binds its socket LAST — after it has started pi and traded a
        first round of RPC with it — while the screen reports ready as soon as
        the machine has a program running. A cold pi is a 100MB bun binary
        opening a session, so a room's FIRST turn asks before there is anything
        to answer: measured on dev 2026-09-15, a new room's socket appeared
        about a minute after the turn that had already been refused.

        So a refused socket is retried, not reported. Only a window that runs
        out means the session is not coming: at that point the machine's own
        record of why travels back with the refusal (``_why``).
        """
        deadline = time.monotonic() + STARTUP_WAIT_S
        while True:
            try:
                return await self.channel._hub.call_executor(
                    device_id, state, "ping", {}
                )
            except DeviceOffline:
                # Not the runner's doing, and not something waiting fixes.
                raise
            except Exception as exc:  # noqa: BLE001 — see below
                # Deliberately not `RuntimeError`. The hub the backend holds is
                # usually a PROXY: the owner raises RuntimeError in its own
                # process, answers 500, and `raise_for_status` turns that into
                # an `httpx.HTTPStatusError` here. Catching the owner's type
                # caught nothing where it mattered, and a person kept seeing
                # `500 Internal Server Error for url …/call/call_executor` —
                # the pipe the answer did not come back through, and nothing
                # about why. Whatever shape it arrives in, a ping that does not
                # come back means the session cannot be reached.
                if time.monotonic() >= deadline:
                    raise ScreenSetupError(
                        f"pi 会话进程没有起来：{await self._why(device_id, state, exc)}"
                    ) from exc
            await asyncio.sleep(STARTUP_POLL_S)

    async def _why(self, device_id: str, state: str, failure: Exception) -> str:
        """The runner's last words, when we have them.

        The machine is the only place a startup failure is written down, and
        nobody reads a file on somebody else's box — so the reason travels back
        with the refusal, into the room, where the person who asked is waiting.
        Falls back to the transport's own message: a device that cannot even be
        asked has told us something too.
        """
        try:
            result = await self.channel._hub.exec(
                device_id,
                ["sh", "-c", f'tail -c 1200 "{state}/runner.log" 2>/dev/null'],
                timeout=15,
            )
        except Exception:  # noqa: BLE001 — a failed read must not replace the failure
            return str(failure)
        return (result.get("stdout") or "").strip() or str(failure)

    async def _remember(
        self,
        session: SessionRef,
        device_id: str,
        resource_id: uuid.UUID,
        state: str,
        agent: str,
    ) -> None:
        """Write down where this session runs, so a restarted backend finds it.

        The device channel keeps its live screens in memory and re-derives the
        rest from bindings; that is enough for a harness whose session IS the
        screen's process. pi's outlives the screen, so the row has to say which
        harness and which state directory, the way the central channel's does.
        """
        factory = self.channel._session_factory or async_session_factory
        async with factory() as db:
            room = await db.get(Topic, session.topic_id)
            if room is None:
                return
            room.session_placement = {
                "device_id": device_id,
                "resource_id": str(resource_id),
                "channel": self.name,
                "runtime": {"harness": PI, "state": state, "agent_handle": agent},
            }
            await db.commit()

    async def call(self, handle: Handle, method: str, params: dict) -> dict:
        return await self.channel._hub.call_executor(
            handle.device_id, handle.state, method, params
        )

    async def discover(self, device_id: str | None) -> list[Handle]:
        factory = self.channel._session_factory or async_session_factory
        handles: list[Handle] = []
        async with factory() as db:
            rooms = await db.scalars(
                select(Topic).where(Topic.session_placement.is_not(None))
            )
            for room in rooms:
                placement = room.session_placement
                assert placement is not None
                runtime = placement.get("runtime", {})
                if runtime.get("harness") != PI or placement["channel"] != self.name:
                    continue
                machine = placement["device_id"]
                if device_id is not None and machine != device_id:
                    continue
                if not self.channel._hub.is_online(machine):
                    continue
                status = await self.channel._hub.call_executor(
                    machine, runtime["state"], "ping", {}
                )
                if not status.get("alive"):
                    continue
                ref = SessionRef(room.project_id, room.id)
                agent = runtime["agent_handle"]
                handles.append(
                    Handle(
                        ref,
                        machine,
                        runtime["state"],
                        status["session_id"],
                        agent,
                        self._mirror(ref, str(placement["resource_id"]) + agent),
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
                    ws.read_room_file(
                        handle.session.project_id,
                        handle.session.topic_id,
                        image["path"],
                    )
                ).decode(),
                "mimeType": image["media_type"],
            }
            for image in images
        ]
