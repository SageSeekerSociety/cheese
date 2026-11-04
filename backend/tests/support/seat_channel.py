"""A machine pool for tests that bring their own runners.

What production does around a room's session is real here: the room places it
(``precheck`` / ``prepare_session``), the session core starts it through its
harness's driver and reads it, and the room keeps its books on it
(``RoomSessions``). What a test supplies is the two things a real session host
does: opening a seat's runner (``open``), and answering a call to it
(``call``). A restarted backend finds the seats again through ``placed``.
"""

import ast
import atexit
import json
import re
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from app.core.sandbox_auth import token_agent_handle
from app.domain.agent import session_state
from app.domain.agent.central_provider import Placed, PreparedSession
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.channel import Placement
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.host import SessionHost
from app.domain.agent.session_state import SeatKey
from app.domain.delivery.receipts import hand_to

# The driver's read of a launch's ending from its runner log
# (`ClaudeCodeDriver._ended`): the record's marker is what it searches for.
_ENDING_READ = re.compile(r'sed -n "/([^/]+)/,')


class _Epochs:
    """The database the running test keeps its rooms' books in, while it has
    one (the ``db_factory`` and ``client`` fixtures set it)."""

    sessions = None


EPOCHS = _Epochs()


class _Hub:
    """The session host, as the test's runners answer it."""

    def __init__(self, channel: "SeatChannel") -> None:
        self.channel = channel

    def is_online(self, device_id: str) -> bool:
        return True

    def online_device_ids(self) -> list[str]:
        return [self.channel.device]

    async def call_executor(
        self, device_id: str, state: str, method: str, params: dict, **_: object
    ) -> dict:
        handle = self.channel.at(state)
        if handle is None:
            raise DeviceCallError(f"no session at {state}")
        return await self.channel.call(handle, method, params)

    async def exec(
        self, device_id: str, argv: list, *, stdin: str | None = None, **_: object
    ) -> dict:
        ending = _ENDING_READ.search(argv[2]) if argv[:2] == ["sh", "-c"] else None
        if ending is not None:
            # A runner whose process is gone has written its ending in its log,
            # as a real one does, so a launch reads that and stops instead of
            # waiting out the whole startup window for a ping that cannot come.
            if getattr(self.channel, "alive", True):
                return {"exit": 0, "stdout": ""}
            # What the runner wrote of its process ending: a test that cares
            # says it (``ending``), as dev's runner log does.
            said = getattr(self.channel, "ending", None)
            said = said or "the session's process is gone"
            return {"exit": 0, "stdout": f"{ending[1]}\n{said}"}
        if argv != ["python3", "-"] or not stdin or "payload=json.loads(" not in stdin:
            return {"exit": 0, "stdout": ""}
        # A launch program (pi's, Codex's): the seat it starts is named by its
        # state directory, and it answers as the runner it leaves running.
        line = next(line for line in stdin.splitlines() if line.startswith("payload="))
        payload = json.loads(
            ast.literal_eval(line.removeprefix("payload=json.loads(")[:-1])
        )
        handle = self.channel.at(payload["state"])
        if handle is None:
            return {"exit": 1, "stderr": f"no seat at {payload['state']}"}
        await self.channel.open(handle.session, handle.agent_handle, payload["config"])
        status = await self.channel.started(handle)
        status.setdefault("contract", payload["config"].get("contract", ""))
        return {"exit": 0, "stdout": json.dumps(status)}


class _Screens:
    """The screen machinery, as a test opens its seats."""

    def __init__(self, channel: "SeatChannel") -> None:
        self.channel = channel

    async def _ensure_screen(
        self, *, topic_id, agent_handle, launch, **kwargs: object
    ) -> SimpleNamespace:
        self.channel.screens.append(kwargs)
        session, _state = self.channel.seats[(topic_id, agent_handle)]
        await self.channel.open(session, agent_handle, launch)
        return SimpleNamespace(sid=f"screen-{topic_id}-{agent_handle}")

    async def restore_screens(self, scopes: list) -> list:
        return []


class SeatChannel:
    """A machine pool whose runners are the test's."""

    name = "test-seats"
    device = "test-device"
    #: The conversation a seat's runner names when its ping does not.
    conversation = "session"
    deferred_work = False
    builds_model_env = False
    _session_factory = None

    def __init__(self, *, harness: str = CLAUDE_CODE, **policy: float) -> None:
        # (conversation, acting agent) → the session, and the
        # state directory its runner is reached at.
        self.seats: dict[tuple[uuid.UUID, str], tuple[SessionRef, str]] = {}
        #: What each screen was asked to be brought up with, in order.
        self.screens: list[dict] = []
        self.epochs: dict[SeatKey, int] = {}
        self.policy = policy
        self.root = Path(tempfile.mkdtemp(prefix="test-seats-"))
        # Removed when the test process exits, not when this channel goes: a test
        # standing in for a restarted backend hands this root to the next one.
        atexit.register(shutil.rmtree, self.root, True)
        self.runtime = self.next_process(harness)

    def next_process(self, harness: str | None = None) -> RoomSessions:
        """The room's sessions as a (next) backend process holds them: a
        session core that has started and read nothing, over the same runners
        and the same mirrors."""
        self.host = SessionHost(
            _Hub(self),
            screens=_Screens(self),
            mirrors=self.root / "mirrors",
            register=self.register,
            hand=self.hand,
        )

        async def api(host: str) -> str:
            # The platform as the test's host reaches it: no database to ask.
            return "http://platform.test"

        self.host._api = api
        self.runtime = RoomSessions(
            self, harness or self.runtime.harness, self.host, **self.policy
        )
        return self.runtime

    @property
    def root(self) -> Path:
        """Where the backend's mirrors of the runners live. A test standing in
        for a restarted backend hands the next channel the same one."""
        return self._root

    @root.setter
    def root(self, value: Path) -> None:
        self._root = value
        if hasattr(self, "host"):
            self.host._mirrors = value / "mirrors"

    # --- what a test supplies -----------------------------------------------

    async def register(self, key: SeatKey) -> int:
        """Issue a seat's runner its worker epoch: where the running test keeps
        the rooms' books in its database (``EPOCHS``), there, as production
        issues it (`session_state.register`), so what the runner reports can
        be applied there; with no database, by a counter per seat."""
        if EPOCHS.sessions is not None:
            return await session_state.register(EPOCHS.sessions, key)
        self.epochs[key] = self.epochs.get(key, 0) + 1
        return self.epochs[key]

    async def hand(
        self, harness: str, conversation: str, input_id: uuid.UUID, epoch: int
    ) -> None:
        """Record which process an input is handed to, in the books the epochs
        come from (``register``): the test's database when it keeps one, as
        production records it (`delivery.receipts.hand_to`); with no
        database there are no books to record it in."""
        if EPOCHS.sessions is None:
            return
        async with EPOCHS.sessions() as session:
            await hand_to(
                session,
                harness=harness,
                native_session_id=conversation,
                input_id=input_id,
                epoch=epoch,
            )
            await session.commit()

    async def open(self, session: SessionRef, agent: str, launch) -> None:
        """Have the seat's runner running; ``launch`` is what it is started
        with (the system prompt, the conversation it resumes)."""
        raise NotImplementedError

    async def call(self, handle: SimpleNamespace, method: str, params: dict) -> dict:
        """One call to a seat's runner: ``handle`` names its ``session``, its
        ``agent_handle`` and its ``state``."""
        raise NotImplementedError

    async def started(self, handle: SimpleNamespace) -> dict:
        """What a seat's runner answers its launch with: its ``ping``, naming
        the conversation and saying it holds reads."""
        status = await self.call(handle, "ping", {})
        conversation = status.get("session_id") or self.conversation
        return {
            "session_id": conversation,
            "thread_id": conversation,
            "capabilities": [LONG_POLL],
            **status,
        }

    def at(self, state: str) -> SimpleNamespace | None:
        """The seat whose runner is reached at ``state``: its ``session`` and
        ``agent_handle``, as ``call`` is handed them."""
        for (_topic, agent), (session, placed) in self.seats.items():
            if placed == state:
                return SimpleNamespace(session=session, agent_handle=agent, state=state)
        return None

    def resume_token(self, seat: tuple[uuid.UUID, str]) -> str | None:
        """The conversation a seat's row resumes, as a restarted backend finds
        it; a runner's terminal answer has to name it before it counts."""
        return None

    # --- the room's placement -----------------------------------------------

    def available(self) -> bool:
        return True

    async def precheck(self, session: SessionRef, *, needs_place: bool) -> Placement:
        return Placement(
            self.device, 0, session.agent_handle or "cheese", False, needs_place
        )

    @asynccontextmanager
    async def prepare_session(
        self, *, session, token, env, precheck, runtime_factory, scratch=False
    ) -> AsyncIterator[PreparedSession]:
        agent = token_agent_handle(token) or precheck.agent_handle
        placed = runtime_factory(session.topic_id)
        self.seats[(session.conversation_id, agent)] = (session, placed["state"])
        yield PreparedSession(
            device_id=self.device,
            agent_user_id=0,
            agent_handle=agent,
            project_id=session.project_id,
            topic_id=session.topic_id,
            token=token,
            env={
                **(env or {}),
                "CHEESE_RESOURCE_ID": str(session.topic_id),
                "CHEESE_EXECUTION_TARGET": json.dumps({"kind": "test"}),
            },
        )

    async def let_go(self, sessions, *, placed_before) -> None:
        """What a real channel records on the placement; nothing here."""

    async def placed(self, harness: str, device_id: str | None = None) -> list[Placed]:
        if harness != self.runtime.harness:
            return []
        return [
            Placed(
                session,
                self.device,
                state,
                # What the room's machine is kept under: the room's, also for a
                # task's or a 支线's session (`CHEESE_RESOURCE_ID` above).
                str(session.topic_id),
                agent,
                self.resume_token((topic, agent)),
            )
            for (topic, agent), (session, state) in self.seats.items()
        ]
