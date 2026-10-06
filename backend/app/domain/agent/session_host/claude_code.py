"""Claude Code, as the session core starts and reads it.

The process runs in a screen on the session host, one per owner (the agent in a
room): its program is the runner (``device_launch``), and the screen machinery
(``DeviceChannel._ensure_screen``) reuses the one there, reopens one whose
runner died or whose credential is expiring, and relaunches one started from a
different configuration once it is idle. The runner keeps its state where the
connector derives a socket from, and every call reaches it there. Reading is
its journal (`harness/claude_code/subscription.py`), where an input counts as
read when its echo comes back.
"""

import asyncio
import base64
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness import SessionRef as Seat
from app.domain.agent.harness.claude_code import (
    ClaudeLaunch,
    Subscription,
    accepts_inputs,
    control_state,
    ended,
)
from app.domain.agent.session_host.contract import (
    Access,
    Image,
    SessionError,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.driver import (
    Launched,
    Readers,
    Wire,
    startup_refused,
)

if TYPE_CHECKING:
    from app.domain.agent.device_provider import DeviceChannel

# How long a first call waits for the runner to bind its socket, and how often
# it asks. The screen reports ready as soon as the machine has a program
# running, while the runner binds its socket only after the launcher has
# prepared the session (the executor's view of the project is mounted first),
# so a cold session's first question often arrives before there is anything to
# answer it. A runner that has already ended says so in its log, and the wait
# stops there.
STARTUP_WAIT_S = 120.0
STARTUP_POLL_S = 1.0


class ClaudeCodeDriver:
    harness = CLAUDE_CODE
    label = "Claude Code"
    read_failure = "Claude Code journal read failed"
    # Written to stdin like any message; the build reads it at the next tool
    # boundary. `steer` rather than `send` only so a message the session reads
    # after its turn ended opens a turn of its own instead of the one it was
    # said to.
    steer = "steer"
    stop: tuple[str, str] = ("interrupt", "interrupted")
    # Written is not read: the echo of the input is (`Subscription.receipt`).
    receipt_on_accept = False
    mirror = "records.sqlite"

    def __init__(self, screens: "DeviceChannel"):
        self.screens = screens

    def working(self, status: dict) -> bool:
        return bool(status.get("working"))

    def conversation(self, status: dict) -> str:
        return str(status.get("session_id") or "")

    def takes_inputs(self, status: dict) -> bool:
        return accepts_inputs(status)

    async def launch(
        self,
        wire: Wire,
        host: str,
        ref: SessionRef,
        spec: SessionSpec,
        access: Access,
        known: Launched | None,
    ) -> Launched:
        owner = access.owner
        if owner is None or owner.user_id is None:
            raise SessionError("A Claude Code session is started for an owner")
        # Names this start of the runner (``runner.LAUNCH``); new every time,
        # so it is never part of what a live session is compared against.
        name = uuid.uuid4().hex
        screen = await self.screens._ensure_screen(
            device_id=host,
            agent_user_id=owner.user_id,
            agent_handle=owner.handle,
            project_id=owner.project_id,
            topic_id=owner.place_id,
            seat=owner.seat,
            token=access.credential,
            env=dict(spec.env),
            launch=ClaudeLaunch(
                system_prompt=spec.system_prompt,
                model=spec.model or None,
                resume_session_id=spec.resume_token,
                launch_name=name,
            ),
            environment_before={},
            runner_alive=known is not None,
        )
        if known is not None and known.launch == screen.sid:
            # The runner that answered the last read is the one this screen
            # still runs, so greeting it would say what is already known.
            return known
        status = await self._greet(wire, host, ref, name)
        return Launched(
            status["session_id"],
            frozenset(status.get("capabilities") or ()),
            screen.sid,
            status.get("input_protocol"),
        )

    async def _greet(self, wire: Wire, host: str, ref: SessionRef, name: str) -> dict:
        """The first call into a runner that may still be starting.

        A refused socket is retried while the runner may still be on its way,
        and reported as soon as its log says this launch has ended: the socket
        went with it, and nothing will answer however long the wait. Past the
        window with no such record, the session is not coming either, and
        whatever the log last said travels back with the refusal.
        """
        deadline = time.monotonic() + STARTUP_WAIT_S
        while True:
            try:
                status = await wire.call(host, ref, "ping", {})
                if status.get("alive"):
                    return status
                failure: Exception = RuntimeError("Claude Code exited")
            except DeviceOffline:
                raise
            except Exception as exc:  # noqa: BLE001 — a ping that does not come back
                # The hub the backend holds is usually a proxy whose failures
                # arrive as HTTP errors; whatever shape it takes, a ping that
                # does not come back means the session cannot be reached yet.
                failure = exc
            if record := await self._ended(wire, host, ref.state, name):
                raise startup_refused(record, harness=self.label)
            if time.monotonic() >= deadline:
                raise startup_refused(
                    await self._why(wire, host, ref.state, failure),
                    harness=self.label,
                    timed_out=True,
                )
            await asyncio.sleep(STARTUP_POLL_S)

    async def _ended(self, wire: Wire, host: str, state: str, name: str) -> str:
        """What this launch's runner wrote on ending, or nothing yet.

        The log is appended across launches, and an earlier launch's ending
        says nothing about this one, so only the part from this launch's own
        record on is read. The machine puts the record's name on a line of its
        own first, and only an answer that starts with it counts: the tail of
        a long record loses its first line, and a reply that is not this
        command's output names no ending at all.
        """
        marker = ended(name)
        try:
            result = await wire.hub.exec(
                host,
                [
                    "sh",
                    "-c",
                    f'record="$(sed -n "/{marker}/,\\$p" "{state}/runner.log" '
                    '2>/dev/null)"; [ -n "$record" ] || exit 0; '
                    f'printf "%s\\n" "{marker}"; '
                    'printf "%s" "$record" | tail -c 1200',
                ],
                timeout=15,
            )
        except Exception:  # noqa: BLE001 — a failed read is not the runner's ending
            return ""
        said = result.get("stdout") or ""
        if not said.startswith(marker + "\n"):
            return ""
        return said[len(marker) + 1 :].strip()

    async def _why(self, wire: Wire, host: str, state: str, failure: Exception) -> str:
        """The log's last words, when the window ran out with no record of
        this launch ending: a runner that died before it could name its
        launch still left a traceback there."""
        try:
            result = await wire.hub.exec(
                host,
                ["sh", "-c", f'tail -c 1200 "{state}/runner.log" 2>/dev/null'],
                timeout=15,
            )
        except Exception:  # noqa: BLE001 — a failed read must not replace the failure
            return str(failure)
        return (result.get("stdout") or "").strip() or str(failure)

    def subscription(
        self,
        seat: Seat,
        acting: str,
        mirror: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        launched: Launched,
        readers: Readers,
    ) -> Subscription:
        return Subscription(
            seat,
            mirror,
            call,
            readers.consume,
            readers.activity,
            session_id=launched.conversation,
            recipient_handle=acting,
            announce=readers.announce,
            receipts=readers.receipts,
            completions=readers.completions,
            terminations=readers.terminations,
            moved=readers.moved,
            input_protocol=launched.input_protocol,
        )

    async def adopt(self, wire: Wire, found: list[tuple[str, Access]]) -> None:
        """The screen machinery forgets every screen when the backend restarts,
        and a screen it does not know is one the next start opens a second copy
        of; so the screens of the sessions found are adopted first, once per
        place and host."""
        scopes = list(
            dict.fromkeys(
                (access.owner.project_id, access.owner.place_id, host)
                for host, access in found
                if access.owner is not None and wire.hub.is_online(host)
            )
        )
        if scopes:
            await self.screens.restore_screens(scopes)

    async def control_state(self, mirror: Path) -> dict:
        """What the session's controls show, from its mirror alone."""
        return await asyncio.to_thread(control_state, mirror)

    def images(self, images: tuple[Image, ...]) -> list:
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
                    "media_type": image.media_type,
                    "data": base64.b64encode(image.data).decode(),
                },
            }
            for image in images
        ]
