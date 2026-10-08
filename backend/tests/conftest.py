"""Test fixtures.

DB-backed tests run on real PostgreSQL (the merged models need PG-native
JSONB/Sequence/ENUM that sqlite can't build; the schema is the alembic migrations).
FULL xdist isolation: every worker gets its OWN databases, so shared sequences /
reference rows / data never race across workers. Two DBs per worker because the
two harnesses can't share one:
  * ``cheesex_test[_<worker>]``    — the integration harness (per-test transactional
    rollback on a session-long connection); the app engines bind here.
  * ``cheesex_test[_<worker>]_c``  — client / python_client (cleared before each
    test, plus a real session factory: ChatService spins up its own sessions and
    background turns COMMIT, which rollback can't isolate; clearing would also
    block on the integration harness's open transaction, hence a separate DB).
A stub agent keeps tests off the live model.
"""

import asyncio
import functools
import inspect
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, QueuePool

# Strip inherited git env. When the suite runs from the pre-commit HOOK it executes
# DURING `git commit`, which exports GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE for
# the hook. The workspace tests (and the app's git ops) spawn `git` subprocesses;
# those vars take precedence over `git -C <tmprepo>` and would hijack them onto the
# MAIN repo — green when run directly, red only under the hook. Clear them so tests
# always get a clean, cwd-driven git context.
for _k in [k for k in os.environ if k.startswith("GIT_")]:
    del os.environ[_k]

# Strip an inherited room session, for the same reason. An agent working on this
# repository runs the suite on the machine its room was launched on, so pytest
# starts with that room's CHEESE_API (the live deployment) and a real token.
# Every launcher test that builds its screen from os.environ then runs the real
# launcher against the real platform, and a launcher that sees a platform
# installs from it: the document toolchain and the harness binaries, ~110MB per
# test, into a throwaway tmp home, detached so it outlives the test. Tests that
# need a platform stand up their own. CHEESE_CI_* / CHEESE_TEST_* configure the
# suite itself and stay.
for _k in [
    k
    for k in os.environ
    if k.startswith("CHEESE_") and not k.startswith(("CHEESE_CI_", "CHEESE_TEST_"))
]:
    del os.environ[_k]

# Client construction validates credentials before the mocked transport is used.
# Keep the suite hermetic instead of depending on a developer or CI secret.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", "test-anthropic-token")
# Cloud compute has no default price, and without one no cloud sandbox or VM
# starts (usage/compute.py). The suite prices both, the VM at its default size;
# the tests of an unset price unset it.
os.environ["CLOUD_SANDBOX_CREDITS_PER_HOUR"] = "12"
os.environ["CLOUD_VM_CREDITS_PER_HOUR"] = '{"4c8g": 30}'

# Bind the app engine (app.core.db — the single pool; app.db.session re-exports
# it) to THIS worker's integration DB — must happen before any app import (the
# engine is built from settings.database_url at import time). -------------------
from app.core.config import settings  # noqa: E402
from tests import isolation  # noqa: E402
from tests.support.hang import HANG_S  # noqa: E402

_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")  # "gw0"… or "" (serial)
# The runner slot this run is on, empty everywhere but a pool machine with more
# than one. Two runs sharing a machine otherwise share every name below, and the
# harness drops its databases WITH (FORCE) — see tests/isolation.py.
_SLOT = os.environ.get("CHEESE_CI_SLOT", "")
_INTG_DB_NAME, _CLIENT_DB_NAME = isolation.database_names(_XDIST_WORKER, _SLOT)
# Postgres server root (no database). Defaults to the TEST server the repo-root
# docker-compose.yml publishes on :5433 (separate from the dev database on
# :5432, so a test run never touches what you were developing against). CI (and
# any other host) overrides it via TEST_PG_BASE so the per-worker databases are
# provisioned over the network instead of `docker exec`.
_PG_BASE = os.environ.get(
    "TEST_PG_BASE", "postgresql+asyncpg://postgres:postgres@localhost:5433"
)
settings.database_url = f"{_PG_BASE}/{_INTG_DB_NAME}"
# Redis needs the same per-worker split as Postgres. Its keys are scoped by
# user id (the #357 attempt budgets and lockouts), and user ids
# restart from 1 in every worker's own database — so on ONE shared Redis, gw0's
# user 5 and gw1's user 5 are the same account. A 15-minute lockout earned by
# one worker would then land on an unrelated test in another, at whatever rate
# the two id sequences happen to align: the flakiest possible failure. Redis
# ships numbered databases; one per worker keeps them apart, and a slot takes a
# block of them so two runs sharing a machine cannot land on the same index.
_REDIS_BASE = re.sub(r"/\d*$", "", os.environ.get("REDIS_URL", settings.redis_url))
_REDIS_DB = isolation.redis_database(
    _XDIST_WORKER, int(os.environ.get("CHEESE_CI_REDIS_BASE_DB", "0"))
)
settings.redis_url = f"{_REDIS_BASE}/{_REDIS_DB}"
os.environ["REDIS_URL"] = settings.redis_url
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", f"{_PG_BASE}/{_CLIENT_DB_NAME}")

import app.models  # noqa: F401, E402  (registers all tables on Base.metadata)
from app.api.deps import get_broker, get_chat_service, get_work_runner  # noqa: E402
from app.core.db import Base, get_db  # noqa: E402
from app.core.db import engine as app_engine  # noqa: E402
from app.core.redis import get_redis_client  # noqa: E402
from app.core.sandbox_auth import SANDBOX_TOKEN  # noqa: E402
from app.domain.agent.central_provider import Placed  # noqa: E402
from app.domain.agent.chat import ChatService  # noqa: E402
from app.domain.agent.compute import ComputePool  # noqa: E402
from app.domain.agent.device_hub import DeviceCallError  # noqa: E402
from app.domain.agent.harness import CLAUDE_CODE, SessionRef  # noqa: E402
from app.domain.agent.harness.claude_code.journal import (  # noqa: E402
    Journal as ClaudeJournal,
)
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL  # noqa: E402
from app.domain.agent.harness.claude_code.runner import Runner  # noqa: E402
from app.main import app  # noqa: E402
from tests.support.collab import install as install_collab  # noqa: E402
from tests.support.seat_channel import SeatChannel  # noqa: E402

# Tests exercise the real authz enforcement regardless of the dev .env (which
# ships it OFF for the conservative dogfood rollout): the .env value must not
# decide test behavior.
settings.authz_enforce_topic_access = True
# The `client` fixture enters lifespan, which starts every periodic job the
# platform runs (app/core/background.py). Two of them would act on the test's
# own data behind its back: the email drain claims whatever a notification test
# queued and dead-letters it after three tries, and the deadline sweep flips a
# membership to FAILED. Zero is the same "not on this box" switch a deployment
# uses.
settings.notification_email_drain_interval_s = 0
# 摘要 job 同理：它会在测试背后按周期给攒够的人发信。测试自己调入口去发。
settings.notification_digest_interval_s = 0
settings.task_deadline_sweep_interval_s = 0
# The queued-message sweep starts turns for messages a test may be holding
# back on purpose; tests that want it run it themselves.
settings.queued_message_sweep_interval_s = 0
# Request limits stay on, set far above anything a test does. Their rate state
# lives in Redis, which no test resets, and across a suite the same handles
# ("alice") and the same test client address are reused far faster than any
# one browser, so at the shipped numbers a test's outcome would turn on how many
# requests ran before it. `tests/integration/test_request_limits.py` puts the
# shipped numbers back.
settings.request_rate_per_s = 1_000_000.0
settings.request_rate_burst = 1_000_000_000
settings.request_concurrency = 1_000_000


def _production_test_engine(url: str):
    """Build the same bounded pool shape the application uses in production."""
    return create_async_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_s,
        pool_pre_ping=settings.db_pool_pre_ping,
    )


#: Every stub channel alive in this process, so teardown can ask each whether a
#: session of its still has records nobody has landed.
_CHANNELS: "weakref.WeakSet[StubChannel]" = weakref.WeakSet()


def _topics_with_pending_records() -> set[str]:
    """Topics whose session said something the room has not landed yet.

    The broker's active mark describes a turn lifecycle, not work currently
    running in this process: a turn the session started for itself stays active
    until a later ``result``, and once everything it said has landed there is
    nothing teardown can wait for.
    """
    pending = set()
    for channel in list(_CHANNELS):
        for (topic_id, _), session in list(channel.sessions.items()):
            if channel.unlanded(topic_id, session):
                pending.add(str(topic_id))
    return pending


def wait_work_idle() -> None:
    """Block until background turns (e.g. a new task's kickoff)
    finish: they run on the TestClient portal loop and write to this worker's DB —
    if a turn is still writing when the next test clears it, the test flakes.
    Returns as soon as they're idle; the generous ceiling only matters under heavy
    parallel/external load, when a turn can take much longer than usual.

    A broker lifecycle with no runner task and no record waiting to land is not
    work teardown can drain. Waiting on idle lifecycles and giving up silently
    combined once to hide the suite's largest single cost: a test that stranded
    a turn paid the whole ceiling here and left no trace but a slower run.
    """
    runner = get_work_runner()
    for _ in range(3000):  # ~30s ceiling; returns early the instant turns drain
        moving = len(runner._tasks) or len(_topics_with_pending_records())
        if not moving:
            return
        time.sleep(0.01)
    print(
        f"\n[wait_work_idle] 等满 30 秒还有 {runner.active_work_count()} 份工作没收尾，"
        "本条测试为此付了 30 秒。留下的："
        f"{dict(runner._broker._active)}",
        file=sys.stderr,
    )


def retire_topic(client: TestClient, topic_id) -> None:
    """Put down a turn the test left running on purpose.

    A few tests drive a session that never reports its ``result`` — that IS the
    scenario (a host that vanished mid-turn). The turn then stays in flight,
    correctly, and every one of them pays ``wait_work_idle``'s full ceiling on
    the way out for a turn nobody is waiting on any more.

    The reader lives on the TestClient's portal loop, so it has to be stopped
    there rather than on whatever loop the test itself ran on.
    """
    topic = uuid.UUID(str(topic_id))

    async def retire() -> None:
        for channel in list(_CHANNELS):
            for key in [seat for seat in channel.sessions if seat[0] == topic]:
                channel.sessions.pop(key, None)
            for seat in list(channel.runtime.tasks):
                if seat[0] == topic:
                    await channel.runtime._detach(seat)

    client.portal.call(retire)


class ScriptedSession(Runner):
    """A Claude Code runner whose Claude Code is a script.

    Everything the runner does with what it reads is the real thing — the work
    stamps, the turn boundaries, the receipts, the journal the backend mirrors.
    Only the process is missing: what would have been written to its stdin is
    handed to the channel, which answers with the records a session would have
    printed (``StubChannel.emit_turn``).
    """

    def __init__(
        self, state: Path, channel: "StubChannel", topic_id, session_id, agent
    ):
        super().__init__(state, idle_exit_s=0)
        # A real runner is its own process on the session machine, so the fsync
        # behind each journal commit holds nobody else up. This one runs on the
        # backend's own event loop, where every commit's fsync stops the whole
        # room: on a CI disk busy with the other workers' writes, one of them
        # held the loop for 5 s and a turn missed its test's wait. Durability
        # across a power cut is not what any test here asks of this journal.
        self.journal.connection.execute("PRAGMA synchronous=OFF")
        # The loop that owns this runner, and the thread it runs on: a test
        # scripting a record from its own thread has it played there.
        self.loop = asyncio.get_running_loop()
        self.thread = threading.get_ident()
        self.channel, self.topic_id = channel, topic_id
        self.session_id = session_id
        self.actor = agent
        self.journal.remember("session_id", session_id)
        self.journal.remember(
            "owner", json.dumps({"harness": CLAUDE_CODE, "agent_handle": agent})
        )
        self.written: list[dict] = []

    async def _write(self, message: dict) -> None:
        self.written.append(message)
        if message.get("type") == "control_request":
            request = message["request"]
            future = self.controls[message["request_id"]]
            future.set_result(
                {
                    "subtype": "success",
                    "request_id": message["request_id"],
                    "response": self.channel.controlled(self.topic_id, request),
                }
            )
            if request.get("subtype") == "interrupt" and self.working:
                self.observe(
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "session_id": self.session_id,
                        "uuid": str(uuid.uuid4()),
                    }
                )
            return
        if message.get("type") == "user":
            asyncio.get_running_loop().call_soon(self._arrive, message)

    def _arrive(self, message: dict) -> None:
        # A script that raises would otherwise leave its turn open until the
        # test times out, with the traceback lost in the loop's log. The turn
        # fails instead, naming what broke.
        try:
            self.channel.arrive(self.topic_id, message, agent=self.actor)
        except Exception as exc:  # noqa: BLE001 — surfaced as the turn's failure
            logging.getLogger(__name__).exception("the scripted session raised")
            if not self.working:
                self.observe(
                    {
                        "type": "command_lifecycle",
                        "command_uuid": message["uuid"],
                        "state": "started",
                        "session_id": self.session_id,
                        "uuid": str(uuid.uuid4()),
                    }
                )
            self.observe(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "result": f"the scripted session raised: {exc!r}",
                    "session_id": self.session_id,
                    "uuid": str(uuid.uuid4()),
                }
            )

    def alive(self) -> bool:
        return self.channel.alive

    def ended(self) -> bool:
        return not self.channel.alive

    # A runner whose host the test took away (``StubChannel.drop_session``) is
    # going as far as a read it holds is concerned: that read is answered.
    @property
    def closing(self) -> bool:
        if self._closing or not hasattr(self, "channel"):
            return self._closing
        return self not in self.channel.sessions.values()

    @closing.setter
    def closing(self, value: bool) -> None:
        self._closing = value

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "ping":
            gone = (self.topic_id, self.actor, self.session_id) in self.channel.gone
            return {
                "session_id": self.session_id,
                "input_protocol": INPUT_PROTOCOL,
                "working": self.working,
                "work_id": self.work if self.working else None,
                "tasks": dict(self.tasks),
                "alive": self.channel.alive and not gone,
                "capabilities": list(self.capabilities),
            }
        return await super().dispatch(method, params)


class StubChannel(SeatChannel):
    """A machine pool with no machine behind it.

    The turn flow tests exercise is the one production runs: the room places
    the session, the session core starts it and hands it the prompt, and what
    the session says comes back later through the seat's reading, not through
    the caller's iterator. So this stub supplies the only things a real one
    supplies — the placement, and the session's stream-json records — and every
    layer above (stamping, translation, attribution, receipts, turn close) is
    the real one.

    The default turn is the vocabulary of a one-message turn: the session takes
    the input (its echo is the receipt that stamps the message consumed), says
    something, and ends.
    """

    name = "stub-session"
    device = "stub-device"
    #: The id a new session's runner is started with (``--session-id``): known
    #: before the process has written anything, which is why the runtime can
    #: announce it the moment the session is opened.
    new_session_id = "sess-test-1"

    def __init__(self, **policy: float) -> None:
        # ``policy`` is the room's liveness settings (no_progress_s,
        # unread_grace_s, hard_ceiling_s), so a test about a session that stops
        # does not have to wait the production half hour for it.
        super().__init__(**policy)
        # (topic_id, agent_handle, session_id) triples the TEST has determined
        # gone (FB-56 legacy③): the same optional per-conversation observation
        # the real channels report from their I/O boundary. Nothing lands here
        # by filtering a session list — a fixture states a deletion by name,
        # and a channel without the probe means "unknown", never "dead".
        self.gone: set[tuple[uuid.UUID, str, str]] = set()
        # One runner per SEAT: a room with several agents seated runs their
        # sessions side by side, each with its own journal and mirror — the
        # single-agent suite never notices because its rooms have one seat.
        self.sessions: dict[tuple[uuid.UUID, str], ScriptedSession] = {}
        self.last_system_prompt: str | None = None
        self.last_resume_session_id: str | None = None
        self.last_prompt: str | None = None
        self.reply = "Hello world"
        self._alive = True
        # Fired the moment the transport actually writes, so a test can assert
        # what did (and did not) happen before the session was reached.
        self.on_start: Callable[[], None] | None = None
        self.calls: dict[str, str] = {}
        _CHANNELS.add(self)

    @property
    def told(self) -> str:
        """What the session last heard: its system prompt and the message it was
        sent. Project state rides the message (a new conversation's first one
        carries it whole), so the system prompt alone is not what it knows."""
        return f"{self.last_system_prompt or ''}\n\n{self.last_prompt or ''}"

    @property
    def alive(self) -> bool:
        """Whether the scripted sessions' agent processes are still there."""
        return self._alive

    @alive.setter
    def alive(self, value: bool) -> None:
        # The agent process ending is something a runner hears at once
        # (``Runner._announce_exit``): a read it holds is answered.
        self._alive = value
        if not value:
            for session in self.sessions.values():
                session.announce()

    # --- the channel -------------------------------------------------------

    def available(self) -> bool:
        return True

    async def open(self, session: SessionRef, agent: str, launch) -> None:
        """The seat's runner, started the first time the seat starts."""
        self.last_system_prompt = launch.system_prompt
        self.last_resume_session_id = launch.resume_session_id
        key = (session.conversation_id, agent)
        runner = self.sessions.get(key)
        if runner is None:
            runner = ScriptedSession(
                self.root / str(session.conversation_id) / agent / "runner",
                self,
                session.conversation_id,
                launch.resume_session_id or self.new_session_id,
                agent,
            )
            self.sessions[key] = runner
        runner.placement = self.seats[key]
        runner.project_id = session.project_id
        runner.session_agent = session.agent_handle

    def at(self, state: str) -> SimpleNamespace | None:
        """The seat whose runner is at ``state``: kept on the runner, so a
        channel standing in for a restarted backend that took over the
        runners (``sessions``) reaches them where they are."""
        if state.startswith("$HOME/.cheese/gone/"):
            *_, agent, session_id = state.split("/")
            return SimpleNamespace(agent_handle=agent, gone=session_id)
        for runner in self.sessions.values():
            session, placed = runner.placement
            if placed == state:
                return SimpleNamespace(
                    session=session,
                    agent_handle=runner.actor,
                    session_id=runner.session_id,
                    state=state,
                    runner=runner,
                )
        return None

    def _session_for(
        self, topic_id: uuid.UUID, agent: str | None = None
    ) -> "ScriptedSession":
        """The runner a scripted record belongs to. Single-seat rooms (the
        whole suite but the parallel-turn tests) leave ``agent`` out and get
        the only runner there is; a room with several seats must say which."""
        if agent is not None:
            return self.sessions[(topic_id, agent)]
        mine = [
            session
            for (topic, _), session in self.sessions.items()
            if topic == topic_id
        ]
        if len(mine) != 1:
            raise KeyError(
                f"{len(mine)} stub sessions for topic {topic_id}; name the agent"
            )
        return mine[0]

    def drop_session(
        self, topic_id: uuid.UUID, agent: str | None = None
    ) -> "ScriptedSession":
        """Remove a seat's runner (a host that vanished) and hand it back.

        A read the backend holds there is answered, as a runner going away
        answers it; every call after that finds nothing there.
        """
        session = self._session_for(topic_id, agent)
        dropped = self.sessions.pop((topic_id, session.actor))
        dropped.announce()  # ``ScriptedSession.closing``
        return dropped

    async def call(self, handle: SimpleNamespace, method: str, params: dict) -> dict:
        """One call to a seat's runner, as the session host relays it. A
        runner declared gone answers only that its process is."""
        if gone := getattr(handle, "gone", None):
            if method != "ping":
                raise DeviceCallError("the runner is gone")
            return {"alive": False, "session_id": gone}
        return await handle.runner.dispatch(method, params)

    def report_gone(
        self, topic_id: uuid.UUID, agent_handle: str, session_id: str
    ) -> None:
        """Declare one conversation terminated — the fixture's own exact
        death evidence, consumed like a real channel's bound terminal answer."""
        self.gone.add((topic_id, agent_handle, session_id))

    def report_back(
        self, topic_id: uuid.UUID, agent_handle: str, session_id: str
    ) -> None:
        """Retract a declaration: the conversation is here again."""
        self.gone.discard((topic_id, agent_handle, session_id))

    async def let_go(self, sessions, *, placed_before) -> None:
        """What a real channel records on the placement; nothing here."""

    async def placed(self, harness: str, device_id: str | None = None) -> list[Placed]:
        """Every seat whose runner is here — what a restarted backend reads
        again. A conversation the test declared gone (``report_gone``) is placed
        with the resume token its runner's terminal answer names; one whose
        runner is not here at all answers that its process is gone."""
        if harness != CLAUDE_CODE:
            return []
        found = []
        seen = set()
        for (topic_id, _), runner in self.sessions.items():
            session, state = runner.placement
            key = (topic_id, session.agent_handle, runner.session_id)
            seen.add(key)
            found.append(
                Placed(
                    session,
                    self.device,
                    state,
                    # Where it was placed: its room's machine, which a 支线's
                    # session shares (`SeatChannel.prepare_session`).
                    str(session.topic_id),
                    runner.actor,
                    runner.session_id if key in self.gone else None,
                )
            )
        for topic_id, handle, session_id in self.gone - seen:
            found.append(
                Placed(
                    SessionRef(uuid.UUID(int=0), topic_id, handle, harness=CLAUDE_CODE),
                    self.device,
                    f"$HOME/.cheese/gone/{topic_id}/{handle}/{session_id}",
                    str(topic_id),
                    handle,
                    session_id,
                )
            )
        return found

    def controlled(self, topic_id: uuid.UUID, request: dict) -> dict:
        """What the scripted session answers a control with."""
        return {}

    def unlanded(self, topic_id: uuid.UUID, session: ScriptedSession) -> bool:
        # Its own connection: this is asked from the test's thread, and the
        # runner's belongs to the portal loop's.
        journal = ClaudeJournal(session.state / "records.sqlite")
        try:
            written = journal.connection.execute(
                "SELECT MAX(sequence) FROM records"
            ).fetchone()[0]
        finally:
            journal.close()
        if not written:
            return False
        mirror = self.mirror(topic_id, session.actor)
        if mirror is None or not mirror.exists():
            return True
        journal = ClaudeJournal(mirror)
        try:
            return int(journal.recall("landed") or 0) < written
        finally:
            journal.close()

    def mirror(self, topic_id: uuid.UUID, agent: str) -> Path | None:
        """Where the backend mirrors the seat's journal."""
        runner = self.sessions.get((topic_id, agent))
        if runner is None:
            return None
        return self._mirror_of(runner.placement[0], agent)

    # --- what the session prints ------------------------------------------

    def arrive(
        self, topic_id: uuid.UUID, message: dict, *, agent: str | None = None
    ) -> None:
        """The session read an input: it is queued, then the turn runs."""
        content = message["message"]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        self.last_prompt = text
        if self.on_start is not None:
            self.on_start()
        self.record(
            topic_id,
            agent=agent,
            type="command_lifecycle",
            command_uuid=message["uuid"],
            state="queued",
        )
        self.emit_turn(topic_id, text, self.reply, agent=agent)

    def emit_turn(
        self, topic_id: uuid.UUID, prompt: str, reply: str, *, agent: str | None = None
    ) -> None:
        """The records a session prints for one input it answered.

        Override this to script a different turn — a tool call between two
        messages, a subagent, silence. What must not change is the frame: the
        session takes the input, and ends. ``stops`` in particular is not
        optional: it is what closes the turn and publishes ``done``.
        """
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.says(topic_id, reply, agent=agent)
        self.stops(topic_id, reply, agent=agent)

    # --- the records, one method each --------------------------------------

    def record(
        self, topic_id: uuid.UUID, *, agent: str | None = None, **record: object
    ) -> None:
        """One stream-json record, as the session printed it.

        Played on the runner's own loop, whichever thread the test scripts it
        from — the runner's journal belongs to that loop's thread, and the
        read the backend holds there is answered with it, so it lands without
        being asked.
        """
        session = self._session_for(topic_id, agent)
        record.setdefault("uuid", str(uuid.uuid4()))
        record.setdefault("session_id", session.session_id)

        def play() -> None:
            session.observe(dict(record))

        if threading.get_ident() == session.thread:
            play()
            return

        async def played() -> None:
            play()

        asyncio.run_coroutine_threadsafe(played(), session.loop).result(timeout=10)

    def starts(
        self,
        topic_id: uuid.UUID,
        session_id: str | None = None,
        *,
        agent: str | None = None,
    ) -> None:
        extra = {"session_id": session_id} if session_id else {}
        self.record(topic_id, agent=agent, type="system", subtype="init", **extra)

    def acknowledges(
        self, topic_id: uuid.UUID, prompt: str, *, agent: str | None = None
    ) -> None:
        """The session takes the input: its turn starts and it echoes it back.

        The echo is the receipt that stamps an injected message consumed. A
        session that never echoes leaves every mid-turn delivery pending, and
        pending messages are replayed (宁可重复不可丢失)."""
        session = self._session_for(topic_id, agent)
        identifier = next(
            (
                message["uuid"]
                for message in reversed(session.written)
                if message.get("type") == "user"
                and _said(message) == prompt
                and message["uuid"] in session.sent
            ),
            None,
        )
        if identifier is None:
            return
        self.record(
            topic_id,
            agent=agent,
            type="command_lifecycle",
            command_uuid=identifier,
            state="started",
        )
        self.record(
            topic_id,
            agent=agent,
            type="user",
            uuid=identifier,
            isReplay=True,
            parent_tool_use_id=None,
            message={"role": "user", "content": prompt},
        )

    def says(
        self,
        topic_id: uuid.UUID,
        text: str,
        *,
        agent: str | None = None,
        **extra: object,
    ) -> None:
        self.record(
            topic_id,
            agent=agent,
            type="assistant",
            parent_tool_use_id=None,
            message={"role": "assistant", "content": [{"type": "text", "text": text}]},
            **extra,
        )

    def uses(
        self,
        topic_id: uuid.UUID,
        name: str,
        *,
        eid: str | None = None,
        parent: str | None = None,
        agent: str | None = None,
        **tool_input: object,
    ) -> str:
        """A tool call; returns its id, which is what a result names."""
        call = eid or f"toolu_{uuid.uuid4().hex[:12]}"
        self.calls[name] = call
        self.record(
            topic_id,
            agent=agent,
            type="assistant",
            parent_tool_use_id=parent,
            message={
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": call,
                        "name": name,
                        "input": dict(tool_input),
                    }
                ],
            },
        )
        return call

    def returns(
        self,
        topic_id: uuid.UUID,
        name: str,
        response: object,
        *,
        error: bool = False,
        call: str | None = None,
        parent: str | None = None,
        agent: str | None = None,
    ) -> None:
        """The result of the last call to ``name`` (or of ``call``)."""
        text = response if isinstance(response, str) else json.dumps(response)
        self.record(
            topic_id,
            agent=agent,
            type="user",
            parent_tool_use_id=parent,
            message={
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call or self.calls[name],
                        "content": text,
                        "is_error": error,
                    }
                ],
            },
            tool_use_result={"content": [{"type": "text", "text": text}]},
        )

    def stops(
        self,
        topic_id: uuid.UUID,
        text: str,
        session_id: str | None = None,
        *,
        agent: str | None = None,
        **extra: object,
    ) -> None:
        self.record(
            topic_id,
            agent=agent,
            type="result",
            subtype="success",
            is_error=False,
            result=text,
            **({"session_id": session_id} if session_id else {}),
            **extra,
        )


def _said(message: dict) -> str:
    content = message["message"]["content"]
    return content if isinstance(content, str) else content[0]["text"]


# Captured at import: a test that squeezes a production wait patches
# `asyncio.sleep` on the shared module, and a poller using the patched one
# never yields — it spins its whole budget without letting the reader run.
_REAL_SLEEP = asyncio.sleep


async def drain_hooks(screen: StubChannel, topic_id: uuid.UUID) -> None:
    """Land everything this screen's session has printed so far.

    Narrower than `settle_turn`, and the right one when the turn is not going
    to end: it asks whether what the session already said has landed, not
    whether the session is done saying things.
    """
    for _ in range(2000):
        if not any(
            screen.unlanded(topic, session)
            for (topic, _), session in list(screen.sessions.items())
            if topic == topic_id
        ):
            return
        await _REAL_SLEEP(0.01)
    raise AssertionError(f"what the session on {topic_id} said never landed")


async def settle_turn(service, topic_id, *, tries: int = 2000) -> None:
    """Wait until what the session said has landed and the turn closed.

    `converse()` returns as soon as the prompt is in the session — the reply
    arrives later, through the runtime's reader. A test that drives the service
    directly (rather than through the runner and a socket) has to wait for
    that, the same way a room does.
    """
    for _ in range(tries):
        if not any(t == topic_id for t, _ in service._hook_work) and not any(
            str(topic_id) == pending for pending in _topics_with_pending_records()
        ):
            return
        await _REAL_SLEEP(0.01)
    raise AssertionError(
        f"turn on {topic_id} never closed; open work: {list(service._hook_work)}"
    )


async def close_topic_subscriptions(service, topic_id) -> None:
    """Explicitly stop the runtimes' readers at a test boundary."""
    for runtime in service._compute._runtimes():
        for seat in list(getattr(runtime, "tasks", {})):
            if seat[0] == topic_id:
                await runtime._detach(seat)


async def finish_turn(service, topic_id) -> None:
    """Settle a turn, then release its readers at a test boundary."""
    await settle_turn(service, topic_id)
    await close_topic_subscriptions(service, topic_id)


def stub_compute(channel: StubChannel | None = None) -> ComputePool:
    """A pool holding one screen, for the many tests that build a ChatService
    by hand. Pass the channel when the test asserts against it."""
    screen = channel or StubChannel()
    return ComputePool([screen.runtime], screen.name)


@pytest.fixture
def stub_hooks() -> StubChannel:
    # Per test: its readers live on the TestClient's portal loop, which goes
    # away with the client.
    return StubChannel()


@pytest.fixture
def bearer() -> Callable[[str], dict[str, str]]:
    """``Authorization`` headers proving the caller is ``handle``.

    Most 2.0 routes still accept a handle named in the body (Phase-0), but the
    ones that decide who may reach the project — writing the project roster —
    read the actor from the verified token only. Those tests need a real token.
    """

    def _headers(handle: str) -> dict[str, str]:
        # Deferred import: the shared helper lives in the integration conftest,
        # and only integration tests request this fixture. Minting here instead
        # would be a second source of truth for the same token —
        # tests/unit/test_no_adhoc_auth_helpers.py exists to stop exactly that.
        from tests.integration.conftest import session_auth_headers

        return session_auth_headers(handle)

    return _headers


@pytest.fixture(autouse=True)
def _metering_proxy_ca(monkeypatch, tmp_path_factory) -> None:
    """A machine has one launch shape, and it reaches a model only through the
    metering proxy — so a backend that cannot read the proxy's CA cannot open a
    screen at all (`screen_model_env._read_proxy_ca`, `machine/enrollment.py`).

    Autouse and here rather than in the files that noticed: opening a screen is
    a step in tests about system prompts, enrolment, work bindings and more, and
    a file that supplies the CA for itself leaves the next one to discover the
    same `ScreenSetupError` from a cause its subject never mentions. The two
    tests that are ABOUT a missing CA take it away again with their own
    monkeypatch.
    """
    ca = tmp_path_factory.mktemp("meter-ca") / "proxy-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(settings, "subscription_ca_backend_path", str(ca))


@pytest.fixture(autouse=True)
def _session_tmp_per_test(monkeypatch, tmp_path_factory) -> None:
    """A launched session keeps its temporary files under the system's
    `/var/tmp`, which outlives the test that launched it. Each test gets a
    directory of its own instead; the one test that removes a room through the
    shipped cleanup program puts the real one back."""
    from app.domain.agent import machine_launcher

    monkeypatch.setattr(
        machine_launcher, "SESSION_TMP", str(tmp_path_factory.mktemp("var-tmp"))
    )


@pytest.fixture(autouse=True)
def _forge_memory_per_test(monkeypatch) -> None:
    """What GitHub said about an installation's quota, and the answers kept for
    conditional reads, are process-wide memory (`core/forge_quota.py`,
    `core/forge_etags.py`). A refusal or an answer one test provoked must not
    reach whichever test runs next."""
    from collections import OrderedDict

    from app.core import forge_etags, forge_quota

    monkeypatch.setattr(forge_etags, "_kept", OrderedDict())
    monkeypatch.setattr(forge_quota, "_owners", {})
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})


@pytest.fixture(autouse=True)
def _redis_client_per_loop() -> Iterator[None]:
    """No test may inherit the redis client another test built.

    ``get_redis_client`` is ``@lru_cache``d, and in production that is right —
    one process, one event loop, one pool. Under pytest every test runs on a
    fresh loop, so a cached client carries the previous test's dead loop into
    this one and the first ``await`` on it raises "attached to a different
    loop". The test that pays is whichever one next touches redis, never the
    one that cached the client, so the failure arrives as an unrelated 500 in a
    file that passes in isolation.

    Autouse and here rather than in the files that noticed: a test file that
    clears the cache for itself buys its own tests immunity and leaves the leak
    for everyone downstream — which is precisely how this survived a release
    with one test exposed and fifteen around it green.
    """
    get_redis_client.cache_clear()
    yield
    get_redis_client.cache_clear()


#: An executor on a process list: its service, and the worker beside it that
#: serves the platform CLI.
_EXECUTOR_PROGRAMS = (
    "remote-execution/runtime.py serve",
    "remote-execution/cli_worker.py",
)


def _process_list() -> list[tuple[int, str]]:
    """Every process's pid and command line.

    Read from /proc where there is one: it is the same list `ps` prints, and
    starting `ps` for it cost 13 ms per test on a hosted runner, for every
    test that has a `tmp_path`. A process that exits mid-read is skipped.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        # -ww: a long tmp path is otherwise cut at the terminal width.
        listing = subprocess.run(
            ["ps", "-A", "-ww", "-o", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
        rows = []
        for line in listing.stdout.splitlines():
            pid, _, args = line.strip().partition(" ")
            rows.append((int(pid), args))
        return rows
    rows = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        rows.append(
            (int(entry.name), raw.replace(b"\0", b" ").decode(errors="replace"))
        )
    return rows


def _executors_under(directory: Path) -> dict[int, str]:
    """Executors whose programs or state lie under `directory`, by pid."""
    root = str(directory.resolve()) + os.sep
    return {
        pid: args
        for pid, args in _process_list()
        if root in args and any(program in args for program in _EXECUTOR_PROGRAMS)
    }


#: The base temp of the worker that ran a test, kept for the check after its
#: teardown (`_end_leftover_executors`).
_EXECUTOR_BASETEMP = pytest.StashKey[Path]()


@pytest.fixture(autouse=True)
def _executor_basetemp(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Where to look for the executors a test may leave: only tests with a
    `tmp_path`, which is where every fixture that starts an executor puts
    it; a process listing per test is not free."""
    if sys.platform != "win32" and "tmp_path" in request.fixturenames:
        request.node.stash[_EXECUTOR_BASETEMP] = tmp_path_factory.getbasetemp()


def _end_leftover_executors(item: pytest.Item) -> None:
    """No executor a test started is still running once the test is over.

    An executor runs in a session of its own (`runtime.py start`), so nothing
    ends it with the test or with the xdist worker: one a teardown missed
    stays up, reparented to init, holding memory after the run has exited.
    Looked for under this worker's base temp, which holds every test's
    `tmp_path` and nothing of another worker or another run. Whatever is found
    is killed, so the rest of the run is not left with it, and the test that
    left it fails by name.

    Run from `pytest_runtest_teardown` once every fixture has torn down, not
    from a fixture: tests patch `subprocess`, `PATH` and `sys.platform`, and
    only after `monkeypatch` has undone that does the process list mean the
    process list.
    """
    basetemp = item.stash.get(_EXECUTOR_BASETEMP, None)
    if basetemp is None:
        return
    # A stop returns once the executor has let go of its lock; its exit can
    # trail that by a moment.
    deadline = time.monotonic() + 5
    while (left := _executors_under(basetemp)) and time.monotonic() < deadline:
        time.sleep(0.1)
    if not left:
        return
    for pid in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    pytest.fail(
        f"{item.nodeid} left {len(left)} executor process(es) running"
        " after its fixtures tore down (killed now):\n"
        + "\n".join(f"  pid={pid} {args}" for pid, args in left.items()),
        pytrace=False,
    )


@pytest.fixture
def stub_project_forge(monkeypatch, tmp_path):
    """Supply project creation's external forge in API lifecycle tests.

    The test Git store stands in for committed remote branches. Proposal and
    merge tests supply their own PR state; tests/forgejo exercises the real API.
    """
    from app.domain.project import forge
    from app.domain.project.models import Project, ProjectForge
    from app.domain.repository import forge_files
    from tests.support import git_store

    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "forge-store"))

    provision = forge.provision_repository
    default_branch = forge.default_branch
    remote_head = forge_files.branch_head
    remote_data = forge_files.repository_data
    remote_tokens = forge_files.tokens_for_project
    remote_status = forge_files.status_client
    remote_author_email = forge.ensure_author_email

    async def provision_repository(project_id, session, **kwargs):
        existing = await forge.binding_for_project(project_id, session)
        project = await session.get(Project, project_id)
        if (
            existing is not None
            or (project.settings or {}).get("forge_kind") == "github_app"
        ):
            return await provision(project_id, session, **kwargs)
        binding = ProjectForge(
            project_id=project_id,
            kind="forgejo",
            repo=f"project-{project_id.hex}/code",
            url=f"https://forge.test/project-{project_id.hex}/code.git",
            api_url="https://forge.test/api/v1",
            default_branch="main",
        )
        session.add(binding)
        await session.flush()
        git_store.ensure_repo(project_id)
        return binding

    async def read_default_branch(project_id, session):
        binding = await forge.binding_for_project(project_id, session)
        if binding is None:
            return await default_branch(project_id, session)
        return binding.default_branch

    async def test_repository(project_id, session):
        binding = await forge.binding_for_project(project_id, session)
        return binding is not None and binding.api_url == "https://forge.test/api/v1"

    async def ensure_author_email(project_id, session, email, **kwargs):
        if not await test_repository(project_id, session):
            await remote_author_email(project_id, session, email, **kwargs)

    async def branch_head(project_id, session, branch, **kwargs):
        if not await test_repository(project_id, session):
            return await remote_head(project_id, session, branch, **kwargs)
        repo = git_store.path(project_id)
        if not repo.exists():
            return None
        result = git_store.run(["git", "rev-parse", "--verify", branch], repo, 20, None)
        return result.stdout.strip() if result.returncode == 0 else None

    async def tokens_for_project(project_id, session):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        if not await test_repository(project_id, session):
            return await remote_tokens(project_id, session)
        return SimpleNamespace(
            installation_token=AsyncMock(return_value=("test-token", ""))
        )

    async def status_client(project_id, session):
        from types import SimpleNamespace

        if not await test_repository(project_id, session):
            return await remote_status(project_id, session)

        async def compare_status(*, base, head, **kwargs):
            repo = git_store.path(project_id)
            revisions = [
                git_store.git(repo, "rev-parse", revision).strip()
                for revision in (base, head)
            ]
            if revisions[0] == revisions[1]:
                return "identical"
            result = git_store.run(
                ["git", "merge-base", "--is-ancestor", *revisions], repo, 20, None
            )
            return "ahead" if result.returncode == 0 else "diverged"

        return SimpleNamespace(compare_status=compare_status)

    async def repository_data(project_id, session, path="", **kwargs):
        import base64
        import subprocess
        from urllib.parse import parse_qs, unquote, urlsplit

        if not await test_repository(project_id, session):
            return await remote_data(project_id, session, path, **kwargs)
        repo = git_store.path(project_id)
        route = unquote(urlsplit(path).path)
        if route.startswith("/git/commits/") and route.endswith(".diff"):
            revision = route.removeprefix("/git/commits/").removesuffix(".diff")
            return git_store.git(repo, "show", "--format=", revision)

        def commits(revision):
            return [
                {
                    "sha": sha,
                    "commit": {
                        "author": {
                            "name": git_store.git(
                                repo, "show", "-s", "--format=%an", sha
                            ).strip()
                        },
                        "message": git_store.git(
                            repo, "show", "-s", "--format=%B", sha
                        ).strip(),
                    },
                }
                for sha in git_store.git(
                    repo, "rev-list", "--max-count=50", revision
                ).splitlines()
            ]

        if route == "/commits":
            return commits(parse_qs(urlsplit(path).query)["sha"][0])
        if route.startswith("/pulls/") and route.endswith(".diff"):
            from sqlalchemy import select

            from app.domain.room_task.models import Task

            number = int(route.removeprefix("/pulls/").removesuffix(".diff"))
            task = await session.scalar(
                select(Task).where(
                    Task.project_id == project_id, Task.pr_number == number
                )
            )
            assert task is not None
            return git_store.git(
                repo, "diff", f"{task.base_branch}...{task.branch_name}"
            )
        if route.startswith("/git/trees/"):
            revision = route.removeprefix("/git/trees/")
            # As GitHub: `recursive` lists every entry below, trees included,
            # by its full path.
            recursive = ["-rt"] if "recursive" in parse_qs(urlsplit(path).query) else []
            tree = []
            for row in git_store.git(
                repo, "ls-tree", "-zl", *recursive, revision
            ).split("\0"):
                if not row:
                    continue
                metadata, name = row.split("\t", 1)
                mode, kind, oid, size = metadata.split()
                tree.append(
                    {
                        "path": name,
                        "mode": mode,
                        "type": kind,
                        "sha": oid,
                        "size": int(size) if size != "-" else 0,
                    }
                )
            return {"tree": tree, "truncated": False}
        if route.startswith("/contents/"):
            # No ref named: the default branch, as GitHub and Forgejo answer.
            name = route.removeprefix("/contents/")
            listed = git_store.git(repo, "ls-tree", "-l", "main", "--", name)
            if not listed.strip():
                return None
            metadata, _ = listed.rstrip("\n").split("\t", 1)
            mode, kind, oid, size = metadata.split()
            if kind == "tree":
                return []
            if mode == "120000":
                return {"type": "symlink", "path": name}
            data = subprocess.check_output(
                ["git", "-C", str(repo), "cat-file", "blob", oid]
            )
            return {
                "type": "file",
                "path": name,
                "size": int(size),
                "encoding": "base64",
                "content": base64.b64encode(data).decode(),
            }
        if route.startswith("/git/blobs/"):
            oid = route.removeprefix("/git/blobs/")
            data = subprocess.check_output(
                ["git", "-C", str(repo), "cat-file", "blob", oid]
            )
            return {"encoding": "base64", "content": base64.b64encode(data).decode()}
        if not route.startswith("/compare/"):
            return await remote_data(project_id, session, path, **kwargs)
        base, head = route.removeprefix("/compare/").split("...")
        statuses = {"A": "added", "D": "removed", "M": "modified"}
        return {
            "commits": commits(f"{base}..{head}"),
            "total_commits": int(
                git_store.git(repo, "rev-list", "--count", f"{base}..{head}")
            ),
            "files": [
                {"filename": name, "status": statuses[status]}
                for status, name in (
                    row.split("\t", 1)
                    for row in git_store.git(
                        repo,
                        "diff",
                        "--no-renames",
                        "--name-status",
                        f"{base}...{head}",
                    ).splitlines()
                )
            ],
        }

    monkeypatch.setattr(forge, "provision_repository", provision_repository)
    monkeypatch.setattr(forge, "ensure_author_email", ensure_author_email)
    monkeypatch.setattr(forge, "default_branch", read_default_branch)
    monkeypatch.setattr(forge_files, "default_branch", read_default_branch)
    monkeypatch.setattr(forge_files, "branch_head", branch_head)
    monkeypatch.setattr(forge_files, "repository_data", repository_data)
    monkeypatch.setattr(forge, "repository_data", repository_data)
    monkeypatch.setattr(forge_files, "tokens_for_project", tokens_for_project)
    monkeypatch.setattr(forge_files, "status_client", status_client)


@pytest.fixture
def client(
    _pg_schema, stub_hooks: StubChannel, tmp_path, stub_project_forge, monkeypatch
) -> Iterator[TestClient]:
    # Real PostgreSQL (not sqlite): the merged models use PG-native JSONB,
    # Sequences and ENUM types that sqlite's compiler can't render, and the schema
    # is defined by the alembic migrations (create_all can't build the pg ENUMs).
    # Requests run on TestClient's portal loop and use the production QueuePool
    # shape. Test setup/inspection still runs through a separate NullPool: the
    # synchronous test body uses short-lived asyncio.run loops, so sharing its
    # connections with the portal would cross event-loop ownership.
    engine = _production_test_engine(TEST_DATABASE_URL)
    test_factory = async_sessionmaker(engine, expire_on_commit=False)
    setup_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    setup_factory = async_sessionmaker(setup_engine, expire_on_commit=False)

    # agent-as-user baseline (P1): 芝士 is a real user with a platform agent-
    # binding — seeded by the migration in prod, re-seeded here after the clear.
    asyncio.run(_clear_client_db(seed_agent_user=True))
    get_broker().reset()  # channel ids reset with the DB; drop stale buffered frames

    async def override_get_db():
        async with test_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    chat_service = ChatService(
        session_factory=test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([stub_hooks.runtime], stub_hooks.name),
    )

    def override_get_chat_service() -> ChatService:
        # ONE instance, like production's lru_cache. A per-request instance was
        # harmless while a turn was self-contained; it is not now that the reply
        # arrives on a subscription owned by the service that started the turn.
        return chat_service

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_chat_service] = override_get_chat_service

    try:
        with TestClient(app) as c:
            # The cheese-only write routes are token-gated (app/api/write_access.py);
            # send the secret on every test request so tests exercising those
            # endpoints (note/lock/...) aren't rejected with 401.
            c.headers["X-Cheese-Token"] = SANDBOX_TOKEN
            # Expose the factory so tests can seed data (e.g. memory entries).
            c.test_factory = setup_factory  # type: ignore[attr-defined]
            # Direct business coroutines must run through ``portal.call`` with this
            # factory so they share the request loop and its production-sized pool.
            c.test_request_factory = test_factory  # type: ignore[attr-defined]
            c.test_app_engine = engine  # type: ignore[attr-defined]
            # The living document is written through the collaboration service;
            # this stands in for it (tests/support/collab.py).
            c.collab = install_collab(monkeypatch, app)  # type: ignore[attr-defined]
            try:
                yield c
            finally:
                # Drain background turns BEFORE leaving the TestClient context:
                # disposing the engine under a running kickoff turn makes flakes.
                try:
                    wait_work_idle()
                finally:
                    # Both pools were used on the portal loop. Close them before
                    # TestClient closes that loop, otherwise asyncpg connections
                    # survive into the next test and fail as attached to a
                    # different loop.
                    try:
                        c.portal.call(engine.dispose)
                    finally:
                        c.portal.call(app_engine.dispose)
    finally:
        app.dependency_overrides.clear()
        # Leaving the app hands its work over and holds the runner's turns, which
        # is right for a process that exits next. This one goes on to run tests
        # that drive the same runner without an app around it.
        get_work_runner().start_turns()
        get_work_runner().own_sessions(True)
        asyncio.run(setup_engine.dispose())


# --- PostgreSQL test-DB plumbing (per-worker, see the module docstring) --------


async def _reseed_plans(conn) -> None:
    """Put back the plans the migration seeds: every team names one, so a
    wiped ``plans`` table would leave no team creatable."""
    from sqlalchemy import insert

    from app.domain.usage.models import Plan

    await conn.execute(insert(Plan), _plans_migration().seed_rows())


@functools.cache
def _plans_migration():
    import importlib.util

    path = (
        Path(__file__).resolve().parent.parent
        / "alembic/versions/ddf13581d122_credits_are_cents.py"
    )
    spec = importlib.util.spec_from_file_location("_plans_seed", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def _clean_slate_queries() -> tuple[str, str]:
    """The two reads ``_clear_tables`` decides its work from.

    The first names every table that holds a row. The second names every
    sequence a table owns that has handed out a value since it was last reset,
    with the value it starts from: a test that inserted a row and deleted it,
    or whose transaction rolled back, leaves an empty table and a moved
    sequence, and ids have to start from 1 again all the same (Redis keys are
    scoped by user id).
    """
    names = [t.name for t in Base.metadata.sorted_tables]
    occupied = " UNION ALL ".join(
        f"SELECT '{name}' WHERE EXISTS (SELECT 1 FROM \"{name}\")" for name in names
    )
    listed = ", ".join(f"'{name}'" for name in names)
    moved_sequences = (
        "SELECT format('%I.%I', ps.schemaname, ps.sequencename), ps.start_value"
        " FROM pg_sequences ps"
        " JOIN pg_class seq ON seq.relname = ps.sequencename"
        "  AND seq.relnamespace = ps.schemaname::regnamespace"
        " JOIN pg_depend dep ON dep.classid = 'pg_class'::regclass"
        "  AND dep.objid = seq.oid AND dep.refclassid = 'pg_class'::regclass"
        "  AND dep.deptype IN ('a', 'i')"
        " JOIN pg_class tbl ON tbl.oid = dep.refobjid"
        f" WHERE ps.last_value IS NOT NULL AND tbl.relname IN ({listed})"
    )
    return occupied, moved_sequences


#: The loop and engine the harness's own work on the client database runs on,
#: made on first use. See ``_on_worker_connection``.
_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_engine = None


def _on_worker_connection(work):
    """Run ``work(engine)`` on this worker's own connection to the client
    database, and return what it returns.

    The harness's reads and writes between tests (the clear, the seeded agent
    user, the check for transactions a test left open) all go through one
    connection that lives as long as the worker, on a loop of its own. A
    backend's first read of a table loads its catalog entries: 25-45 ms on a
    fresh connection against under 1 ms on one that has read them before, and
    every engine the fixtures build is new per test. On a hosted runner a
    fresh connection per step cost 60 ms for the seed alone. The loop is the
    connection's own because the callers run on short-lived loops
    (``asyncio.run`` per step) that an asyncpg connection cannot outlive.
    ``pool_pre_ping`` reconnects it if the database was recreated underneath.
    """
    global _worker_loop, _worker_engine
    if _worker_loop is None:
        _worker_loop = asyncio.new_event_loop()
        threading.Thread(
            target=_worker_loop.run_forever, name="client-db-worker", daemon=True
        ).start()
        _worker_engine = create_async_engine(
            TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_pre_ping=True
        )
    return asyncio.run_coroutine_threadsafe(work(_worker_engine), _worker_loop)


async def _clear_client_db(*, seed_agent_user: bool = False) -> None:
    """Clear this worker's client database (see ``_clear_tables``) and, for
    the fixtures that stand in for a running app, seed the platform agent user
    the migration seeds in production."""

    async def clear(engine) -> None:
        await _clear_tables(engine)
        if seed_agent_user:
            from app.domain.identity.services import IdentityService

            async with AsyncSession(engine, expire_on_commit=False) as session:
                await IdentityService(session).ensure_agent_user()
                await session.commit()

    await asyncio.wrap_future(_on_worker_connection(clear))


async def _clear_tables(engine) -> None:
    """Put the client database back to what ``TRUNCATE <every table> RESTART
    IDENTITY`` leaves, plus the seeded plans.

    It does not run that TRUNCATE. TRUNCATE gives every table a new file
    whether or not it holds a row, and it has to name every table that
    references the ones it clears, so the seeded ``users`` alone drags in most
    of the schema: about 250 ms per test, a quarter to a half of a typical
    client test. A test writes to a handful of tables, so this deletes the
    rows of exactly those and rewinds exactly the sequences that moved. Every
    table ends up empty and every owned sequence starts over, which is what
    the TRUNCATE produced; ``_assert_clean_slate`` checks that on every call
    rather than trusting it.

    The deletes run with ``session_replication_role = replica``, which skips
    row triggers, foreign-key checks included, for this transaction only.
    TRUNCATE fires no row triggers either, and two kinds here would otherwise
    stop or change the delete: ``document_versions`` refuses every DELETE
    (its history is immutable), and deleting a task, thread or topic deletes
    its row in the conversation registry, which is cleared here anyway. The
    setting needs a superuser, which every database this suite runs on has,
    since the harness creates and drops databases.

    A session some earlier test left ``idle in transaction`` can hold a lock
    this needs, and the server's ``lock_timeout`` is 0, so it would wait
    forever. That used to surface as a 300 s pytest-timeout on the NEXT test's
    setup, then on the one after that, and the report named the victims and
    never the session holding the lock (see #693's sibling: the hang on
    ``test_runtime``/``test_work_continuation`` in CI, 2026-09-05). So the wait
    is bounded here, and when it runs out the error says who is in the way.
    """
    if not Base.metadata.sorted_tables:
        return
    occupied_sql, sequences_sql = _clean_slate_queries()
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("SET LOCAL lock_timeout = '20s'")
            occupied = {r[0] for r in await conn.exec_driver_sql(occupied_sql)}
            moved = list(await conn.exec_driver_sql(sequences_sql))
            await conn.exec_driver_sql("SET LOCAL session_replication_role = replica")
            for name in occupied:
                await conn.exec_driver_sql(f'DELETE FROM "{name}"')
            await conn.exec_driver_sql("SET LOCAL session_replication_role = DEFAULT")
            for sequence, start in moved:
                await conn.exec_driver_sql(
                    f"SELECT setval('{sequence}', {int(start)}, false)"
                )
            await _reseed_plans(conn)
    except DBAPIError as exc:
        if "lock timeout" not in str(exc).lower():
            raise
        # The connection that timed out is done for (aborted transaction), so
        # the look-around runs on a fresh one.
        async with engine.connect() as probe:
            rows = await probe.exec_driver_sql(
                "select pid, state, now()-xact_start as xact_age,"
                " now()-state_change as since_change, left(query, 200) as query"
                " from pg_stat_activity"
                " where datname = current_database() and pid <> pg_backend_pid()"
                "   and state <> 'idle'"
                " order by xact_start"
            )
            others = [dict(r._mapping) for r in rows]
        lines = "\n".join(
            f"  pid={r['pid']} state={r['state']!r} xact_age={r['xact_age']}"
            f" since_change={r['since_change']}\n    query: {r['query']}"
            for r in others
        )
        raise RuntimeError(
            "Clearing the tables waited 20 s for a lock. Another session on this"
            " worker's client database still holds one — most likely a test that"
            " left a transaction open. Sessions on the database right now:\n"
            + (lines or "  (none — the blocker went away as this was raised)")
        ) from exc
    await _assert_clean_slate(engine)


async def _assert_clean_slate(engine) -> None:
    """Fail the test about to start if a table still holds a row, or an owned
    sequence did not start over, once ``_clear_tables`` is done.

    The cleanup reads what to clear and then clears it; a writer that commits
    in between, such as a background turn the previous test left running,
    leaves rows the next test would take for its own. This makes that test
    fail by name instead.
    """
    occupied_sql, sequences_sql = _clean_slate_queries()
    async with engine.connect() as conn:
        left = {r[0] for r in await conn.exec_driver_sql(occupied_sql)} - {"plans"}
        moved = sorted(r[0] for r in await conn.exec_driver_sql(sequences_sql))
    if left or moved:
        raise RuntimeError(
            "The client database is not clean after clearing it: something"
            " wrote to it in the meantime, most likely work the previous test"
            f" left running. Tables with rows: {sorted(left)};"
            f" sequences that did not start over: {moved}"
        )


async def _admin_recreate_db(db_name: str) -> None:
    """Drop + recreate a database over the network (no `docker exec`), from the
    maintenance `postgres` DB (can't drop a DB you're connected to). FORCE closes
    any stale connection. asyncpg runs each statement in autocommit, which
    DROP/CREATE DATABASE require (they can't run inside a transaction)."""
    import asyncpg

    # asyncpg wants a plain libpq DSN, not the SQLAlchemy "+asyncpg" dialect URL.
    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


def _migration_fingerprint() -> str:
    """Identity of the migration history, so a template built from an older one
    is never reused."""
    import hashlib
    from pathlib import Path

    alembic = Path(__file__).resolve().parent.parent / "alembic"
    digest = hashlib.sha256()
    # env.py and migration_helpers.py decide what the migrations do as much as
    # the version files: a change to either must not reuse an old template.
    for path in [
        alembic / "env.py",
        alembic / "migration_helpers.py",
        *sorted((alembic / "versions").glob("*.py")),
    ]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


_TEMPLATE_DB = f"cheesex_tpl_{_migration_fingerprint()}"


async def _db_exists(db_name: str) -> bool:
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        return bool(
            await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        )
    finally:
        await conn.close()


async def _clone_db(db_name: str, template: str) -> None:
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db_name}" TEMPLATE "{template}"')
    finally:
        await conn.close()
    # Recorded only after the copy is actually made: "I have just used this
    # template" is what keeps another run's cleanup from taking it (see
    # _templates_to_drop), and a copy that failed did not use it.
    _touch_template_use(template)


_TEMPLATE_PREFIX = "cheesex_tpl_"
# How long a template nothing has copied from is still somebody's. See
# _templates_to_drop for why this number.
_TEMPLATE_GRACE_S = 600.0


def _template_marker_path(template: str) -> Path:
    """Where a run records the last time it took a copy of a template.

    A sibling of the build lock, in the temp dir — the same place that lock
    already assumes every run on this machine can see. Postgres keeps no
    "last used" for a database, and it cannot be kept inside the template
    itself: a template with a session connected to it cannot be copied from.
    """
    return Path(tempfile.gettempdir()) / f"{template}.used"


def _touch_template_use(template: str) -> None:
    """Best effort — a template that cannot be stamped is one another run could
    decide is stale, which is the failure this section exists to end, but not
    something worth failing a session over."""
    try:
        _template_marker_path(template).touch()
    except OSError:
        pass


def _template_last_used(template: str) -> float | None:
    """When this run last copied this template, or None if it never saw one."""
    try:
        return _template_marker_path(template).stat().st_mtime
    except OSError:
        return None


def _templates_to_drop(
    existing: Iterable[str],
    own: str,
    last_used: Mapping[str, float | None],
    now: float,
    grace_s: float = _TEMPLATE_GRACE_S,
) -> list[str]:
    """Which `cheesex_tpl_*` databases a run that just built its own may DROP.

    Pure, and that is the point: this rule *is* the bug it fixes, and while it
    lived inline in an async function talking to a live server the only way to
    test it was to race that server. The caller hands over what it saw — the
    names, and when each was last copied from — and gets back a sorted list to
    act on.

    Three rules, in order:

    * Never ``own``: this run is about to copy from it.
    * Never a template copied from within ``grace_s``. The old rule was "every
      template but mine", which is only correct while one migration history
      exists. With two — two worktrees, one on a branch with a new migration —
      neither is "superseded", and each was deleting the other's, so the next
      session found it missing and rebuilt it, back and forth, and a copy that
      landed on a template the other run had just dropped died in setup with
      ``template database "cheesex_tpl_<hash>" does not exist``.
    * Everything else is stale. A name with no recorded use counts as stale:
      nothing this version builds goes unstamped, so it was built by an older
      one and cannot be placed in time. Being wrong here is the recoverable
      direction — an old-version run that still wanted it rebuilds it (see
      ``_create_and_migrate``), while keeping a template we cannot date is how
      a 3 GB tmpfs fills with abandoned copies of a 90-table schema.

    ``grace_s`` is ten minutes, which is not a tuned number but is well clear
    of what it has to cover: a pytest session copies the template once per
    worker at session start, all within seconds of each other, and the longest
    integration round here is minutes — so a template in active use is stamped
    far more recently than the grace, and only one truly idle outlives it.
    """
    doomed = []
    for name in existing:
        if name == own or not name.startswith(_TEMPLATE_PREFIX):
            continue
        used = last_used.get(name)
        if used is not None and now - used < grace_s:
            continue
        doomed.append(name)
    return sorted(doomed)


async def _drop_superseded_templates() -> None:
    """Remove the `cheesex_tpl_*` databases ``_templates_to_drop`` hands back.

    Which those are is that function's rule; this one is only the I/O around
    it: read the names the server has, read each one's last use off its marker,
    drop what the rule names.

    Never raises on a template that will not drop: one another run is copying
    from this second is not this run's problem, and the next build tries again.
    """
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        stale = await conn.fetch(
            "SELECT datname FROM pg_database WHERE starts_with(datname, $1)",
            _TEMPLATE_PREFIX,
        )
        names = [row["datname"] for row in stale]
        doomed = _templates_to_drop(
            names,
            _TEMPLATE_DB,
            {name: _template_last_used(name) for name in names},
            time.time(),
        )
        for name in doomed:
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
            except Exception:  # noqa: BLE001, PERF203 — in use is not an error here
                continue
    finally:
        await conn.close()


async def _rename_db(old: str, new: str) -> None:
    import asyncpg

    dsn = _PG_BASE.replace("+asyncpg", "") + "/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{new}" WITH (FORCE)')
        await conn.execute(f'ALTER DATABASE "{old}" RENAME TO "{new}"')
    finally:
        await conn.close()


def _ensure_template() -> bool:
    """Build (once per machine, per migration history) a migrated template other
    databases are copied from. Returns False if anything went wrong, so the
    caller can fall back to migrating directly.

    Every xdist worker used to replay the entire history into its own database:
    a dozen concurrent transactions each creating ~90 tables with their indexes
    exhausts Postgres's preallocated lock table, and the server refuses with
    "out of shared memory" — which killed every worker's migration and errored
    the whole session before a single test ran. A service container cannot be
    given a larger lock table (no way to pass server arguments), so the fix is
    to stop asking for that many locks: migrate once, then clone.

    The template is built under a temporary name and renamed on success, so its
    existence means "complete" — a run killed mid-migration leaves the failed
    build behind, not a half-migrated template that later runs would trust.

    Building a new one also drops templates of migration histories that are no
    longer current. That used to take care of itself, because the server was a
    container thrown away with the job; on a CI machine's resident Postgres they
    would accumulate instead, and its data directory is a 3 GB tmpfs. What
    "no longer current" means is ``_templates_to_drop``'s rule — deliberately
    not "every template but mine", which assumed one history per machine and
    had two concurrent runs deleting each other's template mid-clone.
    """
    import fcntl
    import tempfile
    from pathlib import Path

    lock_path = Path(tempfile.gettempdir()) / f"{_TEMPLATE_DB}.lock"
    try:
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if asyncio.run(_db_exists(_TEMPLATE_DB)):
                return True
            building = f"{_TEMPLATE_DB}_building"
            _migrate_fresh_db(building, f"{_PG_BASE}/{building}")
            asyncio.run(_rename_db(building, _TEMPLATE_DB))
            _touch_template_use(_TEMPLATE_DB)
            asyncio.run(_drop_superseded_templates())
            return True
    except Exception:  # noqa: BLE001 — fall back to the slow path, never block
        return False


def revision_template(revision: str) -> str | None:
    """A template database at an older ``revision``, built once per machine per
    migration history, or None if it could not be built.

    A data-migration test replays its migration on the schema of the revision
    before it, and walking a fresh database from nothing to that revision is
    most of the test's time. Every test of one migration asks for the same
    revision, so the walk is done once, under the same lock and build-then-
    rename rule as the head template (``_ensure_template``), and each test
    copies the result. The name carries the history's fingerprint, so a changed
    migration never reuses an old copy, and the ``cheesex_tpl_`` prefix puts it
    under the same stale-template cleanup.
    """
    import fcntl

    template = f"{_TEMPLATE_DB}_{revision}"
    lock_path = Path(tempfile.gettempdir()) / f"{template}.lock"
    try:
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not asyncio.run(_db_exists(template)):
                building = f"{template}_building"
                _migrate_fresh_db(building, f"{_PG_BASE}/{building}", revision)
                asyncio.run(_rename_db(building, template))
            _touch_template_use(template)
            return template
    except Exception:  # noqa: BLE001 — the caller migrates directly instead
        return None


def _migrate_fresh_db(db_name: str, db_url: str, revision: str = "head") -> None:
    """Drop + recreate a database and migrate it to ``revision`` (alembic)."""
    import subprocess
    import sys
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    asyncio.run(_admin_recreate_db(db_name))
    # DATABASE_URL maps to settings.database_url, which alembic/env.py reads.
    # `python -m alembic` works from any host (local venv or CI) without assuming
    # a `.venv/bin/alembic` path.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=backend_dir,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # check=True raises with only the argv, so a migration failure reached
        # the log as "returned non-zero exit status 1" and nothing else — every
        # test in the session then errors with no way to tell a wedged Postgres
        # from a genuinely broken migration. Surface alembic's own words.
        raise RuntimeError(
            f"alembic upgrade {revision} failed for {db_name}"
            f" (rc={result.returncode})\n"
            f"--- stdout ---\n{result.stdout.strip()}\n"
            f"--- stderr ---\n{result.stderr.strip()}"
        )


def _create_and_migrate(db_name: str, db_url: str) -> None:
    """This worker's database, at head — cloned from the shared template when
    one could be built, migrated directly otherwise.

    A template can also go away between the moment a clone's existence check
    passes and the moment the copy is made: another run on this machine that
    finished its own build is entitled to drop templates it finds stale, and
    the drop can land inside ``CREATE DATABASE ... TEMPLATE``. That is the
    setup error this used to hand to the test — ``template database
    "cheesex_tpl_<hash>" does not exist`` — so a failed copy rebuilds the
    template (under its lock) and tries once more. If the second copy fails
    too, the slow path is still correct, just slow.

    What is deliberately NOT here is ``DROP DATABASE ... WITH (FORCE)`` on the
    template to settle the race: forcing a copy out from under a run that is
    mid-clone breaks that run harder than the occasional rebuild does.
    """
    global _TEMPLATE_READY
    if _TEMPLATE_READY:
        try:
            asyncio.run(_clone_db(db_name, _TEMPLATE_DB))
            return
        except Exception:  # noqa: BLE001 — the slow path below is still correct
            _TEMPLATE_READY = _ensure_template()
            if _TEMPLATE_READY:
                try:
                    asyncio.run(_clone_db(db_name, _TEMPLATE_DB))
                    return
                except Exception:  # noqa: BLE001 — same reason
                    _TEMPLATE_READY = False
    _migrate_fresh_db(db_name, db_url)


# Built lazily by the first worker to reach the fixture; the rest clone it.
_TEMPLATE_READY = False


# tests/unit/ is the only tree allowed to run without a Postgres; everything
# else is DB-backed by construction. Trailing sep so a sibling like
# "tests/unittools/" can't match by prefix.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_UNIT_DIR = os.path.join(_TESTS_DIR, "unit") + os.sep
_CONTRACT_DIR = os.path.join(_TESTS_DIR, "contract") + os.sep


def _reaches_for_db(path: str, fixturenames: Iterable[str]) -> bool:
    """Whether a test at ``path`` with this fixture closure needs Postgres.

    Two independent reasons, because fixture names alone aren't enough: the
    integration harness binds ``settings.database_url`` directly (module import
    time), so a test there can touch the DB without naming a DB fixture. Hence
    anything outside ``tests/unit/`` is assumed DB-backed. Inside ``tests/unit/``
    we go by the fixture closure — a closure is transitive, and every DB-bound
    fixture (``client``, ``python_client``, integration's
    ``db_connection``/``db_session``…) chains to ``_pg_schema``, so requesting any
    of them shows up here.

    One function because the answer has two readers who must agree: the gate
    below, which provisions a database for the tests that need one, and
    ``_layer_of``, which sends tests that don't into a CI selection with none.
    Written twice they would drift, and the drift lands as a test asking a
    database that was never built for it.
    """
    return not path.startswith(_UNIT_DIR) or "_pg_schema" in fixturenames


def _needs_db(request: pytest.FixtureRequest) -> bool:
    """Whether this test requires the Postgres schema to be provisioned."""
    return _reaches_for_db(str(request.path), request.fixturenames)


# The three layers the suite selects. Each has its own ceiling, so a wedge or a
# slowdown names the layer it is in instead of arriving as one number for 6800
# tests.
_LAYERS = frozenset({"pure", "contract", "integration"})

# The longest a test in each layer may take, setup included, in seconds. A test
# over its ceiling is almost never doing more work: it is waiting out a real
# timer or poll interval, and the fix is to inject a shorter one, not to raise
# the number. The ceilings sit at about twice the slowest test outside
# slow_tests.txt in the runs that list was measured on, so a busy machine does
# not trip them; the targets the layers are meant to reach are far lower
# (docs/plans/2026-09-19-bugs-and-testing.md §3.2).
#
# Checked once the test has finished, and reported as that test's failure with
# its time. Not a pytest-timeout marker: its thread method ends the whole xdist
# worker, and a worker's stderr never reaches the run's log, so a test over its
# ceiling read as a worker lost for no reason. The suite-wide `timeout` in
# pyproject.toml still ends a test that never finishes.
_LAYER_CEILING_S = {"pure": 10, "contract": 15, "integration": 30}
_CEILING = pytest.StashKey[tuple[str, float]]()
_SETUP_S = pytest.StashKey[float]()

# Tests that took over 60% of their layer's ceiling in some run, so variance
# alone could push them over it. They are not checked against it until they are
# fixed, and the list only shrinks: a fixed test leaves it in the same change,
# and nothing is added to it.
_SLOW_BASELINE = Path(__file__).with_name("slow_tests.txt")


def _slow_baseline() -> frozenset[str]:
    lines = _SLOW_BASELINE.read_text().splitlines()
    return frozenset(
        line.split()[0] for line in lines if line.strip() and not line.startswith("#")
    )


def _layer_of(item: pytest.Item) -> str:
    """Which layer ``item`` runs in.

    ``pure`` is the layer that runs on a machine with no Postgres, so what
    decides it is ``_reaches_for_db`` — the same question the gate asks before
    building a database, asked of the same fixture closure. A file under
    ``tests/unit/`` that does reach for one — the turn log lives in Postgres, so
    the runner's tests do — is a database test wherever it sits, and runs in the
    integration selection with a database under it.
    """
    path = os.fspath(item.path) if item.path is not None else ""
    if path.startswith(_CONTRACT_DIR):
        return "contract"
    # getattr: only a Function has a fixture closure, and a collected node that
    # has none has not asked for a database either.
    closure = getattr(item, "fixturenames", ())
    # One call decides both halves of "pure": outside ``tests/unit/`` this is
    # true whatever the closure holds, so the tree is checked by the same
    # predicate that checks the fixtures.
    if not _reaches_for_db(path, closure):
        return "pure"
    return "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give every collected test exactly one layer marker, and that layer's
    time ceiling unless the test is in the slow baseline.

    The CI selections are the whole suite only if every test carries one and
    only one of the three markers: a test carrying none runs in no selection
    and is reported nowhere — green CI over code nothing checked — and one
    carrying two is counted, and timed, twice. Neither can be seen in a passing
    run.

    So a test that declares a layer of its own aborts the collection here
    rather than being warned about, and a test that declares none has one
    added. There is no third branch: ``_layer_of`` is total — contract, pure,
    or integration as the fallback — so "nothing to add" cannot arise.

    Derived here rather than written on each test: a marker on the test is a
    second declaration of what its fixture list already says, and the two drift
    the moment a test grows a database and nobody moves its marker.
    """
    misfiled = []
    slow = _slow_baseline()
    for item in items:
        declared = {m.name for m in item.iter_markers()} & _LAYERS
        if declared:
            misfiled.append(
                f"  {item.nodeid}\n"
                f"    carries {sorted(declared)} already — the layer is derived"
                f" from the fixture closure, so remove the marker"
            )
            continue
        layer = _layer_of(item)
        item.add_marker(layer)
        if item.nodeid not in slow:
            item.stash[_CEILING] = (layer, _LAYER_CEILING_S[layer])
    if misfiled:
        raise pytest.UsageError(
            "These tests declare their own layer:\n" + "\n".join(misfiled)
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Iterator[pytest.TestReport]:
    """Fail a passing test that took longer than its layer's ceiling."""
    report = yield
    if call.when == "setup":
        item.stash[_SETUP_S] = call.duration
        return report
    limit = item.stash.get(_CEILING, None)
    if call.when != "call" or limit is None or not report.passed:
        return report
    layer, ceiling = limit
    took = item.stash.get(_SETUP_S, 0.0) + call.duration
    if took > ceiling:
        report.outcome = "failed"
        report.longrepr = (
            f"took {took:.1f} s with setup, over the {layer} layer's {ceiling:g} s"
            " ceiling (_LAYER_CEILING_S in tests/conftest.py). Find what it waits"
            " for and give it a shorter timer or interval rather than adding it to"
            " slow_tests.txt."
        )
    return report


async def _terminate_open_transactions(engine) -> list[dict]:
    """Sessions left ``idle in transaction`` on the client database, each
    terminated after being recorded. Runs on the worker's own connection
    (``_on_worker_connection``), whose loop no test owns, so it can see and end
    them regardless of which loop created them; that connection is excluded,
    and is never inside a transaction between tests anyway."""
    async with engine.connect() as conn:
        rows = (
            await conn.exec_driver_sql(
                "select pid, now()-xact_start as xact_age, left(query, 200) as query"
                " from pg_stat_activity"
                " where datname = current_database()"
                "   and backend_type = 'client backend'"
                "   and state = 'idle in transaction'"
                "   and pid <> pg_backend_pid()"
            )
        ).mappings()
        found = [dict(r) for r in rows]
        for r in found:
            await conn.exec_driver_sql(f"select pg_terminate_backend({int(r['pid'])})")
        await conn.commit()
        return found


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item):
    """After a client-DB test has torn down, no session of ours may still be
    inside a transaction on that database.

    One left open can hold row locks the next test's clear needs (see
    ``_clear_tables``), and it is invisible from there: the report names the
    test that waited, never the one that leaked. Checking at the leaker's own
    teardown is what pins it, so a leak fails HERE, on the test that made it,
    with the statement it was running. The session is terminated too, so the
    rest of the run is not held hostage to a bug already reported.

    Only the client database (``_c``): it is cleared between tests, so any open
    transaction there once the test is over is a leak by definition. The
    integration database uses a session-long connection with per-test rollback,
    where a transaction between tests can be the harness itself. ``client``,
    ``python_client`` and ``db_factory`` all sit on this same database (all
    three build their engine on ``TEST_DATABASE_URL``), so all three name it
    here — ``db_factory``'s own teardown check (see the fixture) is meant to
    catch a leak first, by cancelling the task that holds it; this is the
    backstop for whatever gets past that.

    Cost: one query on the worker's own connection per client-DB test.

    Executors a test left running are looked for here too
    (`_end_leftover_executors`).
    """
    yield
    try:
        _fail_on_open_transactions(item)
    finally:
        _end_leftover_executors(item)


def _fail_on_open_transactions(item: pytest.Item) -> None:
    names = getattr(item, "fixturenames", ())
    # A pure test has no database behind it, so it cannot have leaked a
    # transaction on one — and the name check below cannot tell that on its own:
    # three files define their own local ``client``, a fake HTTP client or a
    # four-route FastAPI app, which shadows the fixture this hook is named after
    # and matches here all the same. That cost every one of them a connection to
    # the maintenance database per test, and in the pure CI selection, which has
    # no database reachable at all, it was an error at teardown on a test that
    # had passed.
    if item.get_closest_marker("pure") is not None:
        return
    if not ({"client", "python_client", "db_factory"} & set(names)):
        return
    leaked = _on_worker_connection(_terminate_open_transactions).result(timeout=60)
    if leaked:
        details = "\n".join(
            f"  pid={r['pid']} open for {r['xact_age']}\n"
            f"    last statement: {r['query']}"
            for r in leaked
        )
        pytest.fail(
            f"{item.nodeid} left {len(leaked)} transaction(s) open on"
            f" {_CLIENT_DB_NAME} after its fixtures tore down (terminated now)."
            " Whatever opened them never committed, rolled back, or closed —"
            " usually a session held by a task that outlived the test's event"
            " loop:\n" + details,
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _pg_schema_gate(request: pytest.FixtureRequest) -> None:
    """Provision the DB schema for the tests that need it — and only those.

    ``_pg_schema`` used to be ``autouse=True`` at session scope, which meant a
    host with no Postgres could not run *any* test, including the great majority
    of unit files that never touch a database: the session fixture errored during
    setup and took the whole run down with it. Gating it per-test keeps behaviour
    identical for DB-backed tests (still built once per session — ``_pg_schema``
    is still session-scoped) while letting every unit test that asks for nothing
    run with no server at all. A unit test that DOES name a DB fixture (the turn
    log lives in Postgres, so the runner's tests do) pays for one; the rest
    still do not.
    """
    if _needs_db(request):
        request.getfixturevalue("_pg_schema")
        drop_connections_left_in_the_app_pool()
        explicit_anyio = request.node.get_closest_marker("anyio") is not None
        if explicit_anyio or inspect.iscoroutinefunction(request.function):
            request.getfixturevalue("_app_engine_on_test_loop")


@pytest.fixture(scope="session")
def _pg_schema():
    """Create + migrate THIS worker's two dedicated databases once per session:
    the integration DB (settings.database_url, bound by the app engines) and the
    client/python_client DB (TEST_DATABASE_URL). Reached via the ``_pg_schema_gate``
    autouse fixture above so the integration harness — which binds to
    settings.database_url — always finds a ready schema too. Both are per-worker,
    so nothing races across xdist workers. Talks to the Postgres server at
    TEST_PG_BASE (local docker pg :5433 by default; CI overrides it).
    """
    global _TEMPLATE_READY
    _TEMPLATE_READY = _ensure_template()
    _create_and_migrate(_INTG_DB_NAME, settings.database_url)
    _create_and_migrate(_CLIENT_DB_NAME, TEST_DATABASE_URL)
    yield


def _is_anyio_runner_plumbing(task: asyncio.Task) -> bool:
    """Whether ``task`` is anyio's own pytest-runner machinery, not test work.

    Every async fixture step (this teardown included) is driven through
    ``anyio.pytest_plugin``'s ``TestRunner._call_in_runner_task``: the caller
    wraps itself in a task via ``run_until_complete`` and suspends on
    ``await future``, and that future only resolves once THIS very coroutine
    returns — so that caller task is always still "pending" by construction
    at the exact moment this check runs, for every fixture and test, leak or
    not. It is identified by where its code lives (anyio's own package),
    not by name, since it is the same bound method regardless of which
    fixture or test it is currently ferrying.
    """
    code = getattr(task.get_coro(), "cr_code", None)
    filename = getattr(code, "co_filename", "") or ""
    return f"{os.sep}anyio{os.sep}" in filename


async def _fail_on_background_work(label: str) -> None:
    """Refuse to let a test return while something it started is still running.

    A test that submits work onto a runner (``AgentWorkRunner.submit``) and
    returns without waiting for it races the per-test event loop's own
    teardown: whatever task is still going gets frozen mid-await the moment
    the loop closes under it — mid a DB transaction, most dangerously, holding
    a lock the next test's clear then waits on (see
    ``pytest_runtest_teardown`` above, which is the backstop for whatever gets
    past this).

    So: wait briefly (a turn's tail is milliseconds), and if anything is still
    pending, cancel it — cancelling on the still-live loop is what makes the
    leak impossible, since an ``async with session_factory()`` that gets
    cancelled rolls back and closes right here rather than freezing — then
    fail loudly naming every offending coroutine, instead of letting the next
    test silently inherit the lock.
    """
    current = asyncio.current_task()
    pending = {
        t
        for t in asyncio.all_tasks()
        if t is not current and not t.done() and not _is_anyio_runner_plumbing(t)
    }
    if not pending:
        return
    _, still_pending = await asyncio.wait(pending, timeout=HANG_S)
    if not still_pending:
        return
    offenders = sorted(
        f"{getattr(t.get_coro(), '__qualname__', None)}<{t.get_name()}>"
        for t in still_pending
    )
    for t in still_pending:
        t.cancel()
    # Give the cancellation itself a moment to actually unwind (rollback +
    # close) before the caller's next teardown step (disposing the engine
    # these tasks' sessions borrow connections from).
    await asyncio.wait(still_pending, timeout=2.0)
    pytest.fail(
        f"{label} returned with background work still running: "
        + ", ".join(offenders)
        + ". A test must not return while a runner's turn task is still"
        " going — await `runner.drain()` before returning.",
        pytrace=False,
    )


@pytest.fixture
async def db_factory(_pg_schema):
    """A truncated database and a session factory over it — no app around it.

    ``client`` builds a whole TestClient to arrive at one of these. A test that
    only seeds and reads rows should not pay for an ASGI app to do it, and
    saying so in the fixture list is also how ``_pg_schema_gate`` learns this
    test needs a database at all.
    """
    engine = _production_test_engine(TEST_DATABASE_URL)
    await _clear_client_db()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        try:
            await _fail_on_background_work("db_factory")
        finally:
            await engine.dispose()


@pytest.fixture
async def business_db_factory(db_factory, stub_project_forge):
    """Production-pooled direct business fixture with the client baseline.

    Unlike the lower-level ``db_factory``, these tests expect the same forge
    substitute, broker reset, and platform-agent identity that ``client``
    installs. Keep this separate so live Forgejo and empty-database contract
    tests retain their deliberately narrower fixture.
    """
    from app.domain.identity.services import IdentityService

    get_broker().reset()
    async with db_factory() as session:
        await IdentityService(session).ensure_agent_user()
        await session.commit()
    return db_factory


def drop_connections_left_in_the_app_pool() -> None:
    """Start with an application pool that holds nothing an earlier test left.

    A test that reaches app code from a loop of its own (``asyncio.run`` in a
    sync test, a fixture's one-off loop) returns its connection to the pool
    still bound to that loop, and the loop closes when the test does. The next
    borrower on another loop (a script's ``main`` on the session portal) is
    handed that connection: its pre-ping fails with "attached to a different
    loop" and closing it fails with "Event loop is closed". Which test comes
    before which is up to the scheduler, so the failure lands on whoever is
    next. Those connections cannot be closed any more (their loop is gone), so
    they are dropped, not closed: ``close=False`` swaps in an empty pool and
    leaves checked-out connections, the session-long ``db_connection`` among
    them, untouched.
    """
    app_engine.sync_engine.dispose(close=False)


@pytest.fixture
async def _app_engine_on_test_loop():
    """Close singleton connections before their test-owned event loop closes."""
    yield
    await app_engine.dispose()


@pytest.fixture
def production_app_engine(_pg_schema):
    """Expose the singleton application engine for its pool contract tests."""
    assert isinstance(app_engine.pool, QueuePool)
    return app_engine


@pytest.fixture
async def python_client(
    _pg_schema,
    stub_hooks: StubChannel,
    tmp_path,
    monkeypatch,
):
    """Async httpx client bound to the app over ASGI — the async counterpart to
    `client`. Inherited contract/route tests written against the main backend use
    it. Same postgres test DB + truncate isolation + agent seed as `client`, but
    awaitable inside anyio tests."""
    from httpx import ASGITransport, AsyncClient

    engine = _production_test_engine(TEST_DATABASE_URL)
    test_factory = async_sessionmaker(engine, expire_on_commit=False)
    await _clear_client_db(seed_agent_user=True)
    get_broker().reset()  # channel ids reset with the DB; drop stale buffered frames

    async def override_get_db():
        async with test_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    chat_service = ChatService(
        session_factory=test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([stub_hooks.runtime], stub_hooks.name),
    )

    def override_get_chat_service() -> ChatService:
        # ONE instance, like production's lru_cache. A per-request instance was
        # harmless while a turn was self-contained; it is not now that the reply
        # arrives on a subscription owned by the service that started the turn.
        return chat_service

    # ONE get_db across the whole app (app.db.session re-exports app.core.db's),
    # so a single override moves every route — cheesex and 知是 alike — onto the
    # per-worker request factory (production QueuePool, isolated DB, loop-owned).
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_chat_service] = override_get_chat_service

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Cheese-Token": SANDBOX_TOKEN},
        ) as c:
            # Expose the per-worker factory so contract tests can seed rows (e.g. a
            # real authenticated user) on the SAME DB the app reads through get_db.
            c.test_factory = test_factory  # type: ignore[attr-defined]
            c.test_app_engine = engine  # type: ignore[attr-defined]
            # The living document is written through the collaboration service;
            # this stands in for it (tests/support/collab.py).
            c.collab = install_collab(monkeypatch, app)  # type: ignore[attr-defined]
            yield c
    finally:
        app.dependency_overrides.clear()
        try:
            await _fail_on_background_work("python_client")
        finally:
            try:
                await engine.dispose()
            finally:
                await app_engine.dispose()


# --- shared seed helpers -----------------------------------------------------


def seed_space(client: TestClient, name: str = "信院") -> int:
    """Insert a 知是 Space row directly and return its int id.

    The cheesex ``POST /api/spaces`` uuid stub was retired in the fusion merge
    (unify P1b/c: one Space = main int). The task-template market still lives on
    top of a Space, so tests that need one seed it through the DB here.
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime

    from app.domain.space.models import Space

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            space = Space(
                name=name,
                intro="",
                description="",
                task_templates=[],
                created_at=now,
                updated_at=now,
            )
            session.add(space)
            await session.flush()
            holder["id"] = space.id
            await session.commit()

    _asyncio.run(_seed())
    return holder["id"]


def seed_claim(
    client: TestClient,
    task_id: int,
    *,
    handle: str | None = None,
    team_id: int | None = None,
    approved: int = 2,
) -> None:
    """A claim on ``task_id``: by ``handle`` for an individual task, by
    ``team_id`` for a team one. Pending (``approved=2``) unless told otherwise;
    ``ApproveType.APPROVED == 0``.

    A project can only be built from a task someone has claimed, so every test
    that makes one from a task claims it first — through the DB, because the
    claim itself is not what those tests are about.
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime

    from app.domain.task.models import TaskMembership
    from app.domain.user.repositories import UserRepository

    assert (handle is None) != (team_id is None), "a claim is by a person or a team"

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            member_id = team_id
            if handle is not None:
                users = UserRepository(session)
                user = await users.get_by_username(handle)
                if user is None:
                    user = await users.create_user(
                        username=handle, email=f"{handle}@example.com"
                    )
                member_id = user.id
            now = datetime.now(UTC)
            session.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=member_id,
                    is_team=team_id is not None,
                    approved=approved,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    _asyncio.run(_seed())


def seed_task_with_protocol(
    client: TestClient,
    *,
    conditions: list[dict] | None = None,
    resource_pack: dict | None = None,
    default_role: str | None = None,
    shell: str | None = None,
    teaching: dict | None = None,
    override: dict | None = None,
    space_id: int | None = None,
    space_teaching: dict | None = None,
) -> int:
    """A 项目集 carrying 机构协议 + one 赛题 under it; returns the 赛题's int id.

    Seeded through the DB because the 知是 publish flow needs an authenticated
    space admin and a filled form, and none of that is what the protocol tests
    are about. `override` populates the 赛题's own `protocol_override` (#370
    option (c)). `shell` and `override` carry the 壳 layer the same way, so a
    项目集-level 壳 and a 赛题-level one are set up in one call. `teaching` is
    the 课程级教学配置 (#8d772257), on the same row and by the same route.
    `space_teaching` is the OUTERMOST level of the same key (#944): the 空间
    default every 项目集 on the board inherits — set on the `Space` row this call
    creates, so it only applies when `space_id` is not passed.
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime

    from app.domain.space.models import Space, SpaceCategory
    from app.domain.task.models import Task

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            owning_space = space_id
            if owning_space is None:
                space = Space(
                    name=f"信院-{datetime.now(UTC).timestamp()}",
                    intro="",
                    description="",
                    task_templates=[],
                    teaching=space_teaching or {},
                    created_at=now,
                    updated_at=now,
                )
                session.add(space)
                await session.flush()
                owning_space = space.id
            category = SpaceCategory(
                space_id=owning_space,
                name="创研课 2026 秋",
                description="",
                display_order=0,
                resource_pack=resource_pack or {},
                conditions=conditions or [],
                default_role=default_role,
                shell=shell,
                teaching=teaching or {},
                created_at=now,
                updated_at=now,
            )
            session.add(category)
            await session.flush()
            task = Task(
                name="题目",
                intro="",
                description="",
                protocol_override=override,
                creator_id=1,
                space_id=owning_space,
                category_id=category.id,
                submitter_type=0,
                approved=0,
                default_deadline=0,
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            await session.flush()
            holder["id"] = task.id
            await session.commit()

    _asyncio.run(_seed())
    return holder["id"]


def seed_user(client: TestClient, handle: str) -> str:
    """Get-or-create a real 知是 User for ``handle`` and return a session token
    whose ``sub`` is the int user id (so ActorResolver resolves ``user_id``).

    The cheesex POST /api/users/login handle-login was retired in the fusion
    merge (unify P3). Flows that need a genuine logged-in human with a DB-backed
    user id (e.g. device approval binding an owner) use this instead of a bare
    handle token.
    """
    import asyncio as _asyncio

    from app.common.auth import create_access_token
    from app.domain.user.repositories import UserRepository

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            repo = UserRepository(session)
            user = await repo.get_by_username(handle)
            if user is None:
                user = await repo.create_user(
                    username=handle, email=f"{handle}@example.com"
                )
            holder["id"] = user.id
            await session.commit()

    _asyncio.run(_seed())
    return create_access_token(holder["id"], handle=handle)
