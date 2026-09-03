"""每一条退出路径上，会话指针都必须已经落库 (④ 超时重跑, 上半).

The property under test is stated as an invariant rather than a happy path,
because the bug it replaces was precisely a path nobody enumerated:

    Once 芝士 has announced its session id, its ``agent_sessions`` row is
    committed — no matter how the turn ends.

Before the fix the pointer was written at turn end (``_converse_impl``'s tx2) or
by the failure handlers, so it held for a clean finish and for exceptions that
the process lived long enough to handle — and silently failed for the one exit
that skips all Python: a hard kill. That produced the incident this file exists
for: a 7-minute, 132-message turn was killed by a platform restart, the pointer
was never written, the auto-resume had nothing to ``--resume``, and 芝士 came
back with no memory of what it had already done.

So every exit is enumerated here and asserted one by one:

  1. 正常结束            — the turn runs to its AgentResult
  2. provider 报错        — AgentResult(is_error=True)
  3. 异常中断            — the provider raises mid-stream
  4. 取消                — the turn is cancelled (what a timeout does)
  5. 硬杀 (SIGKILL)      — a REAL process, really killed; no handler runs

Case 5 spawns a subprocess on purpose. Mocking a SIGKILL would test the mock:
the whole point is that no ``finally``, no ``except``, and no ``asyncio.shield``
gets to run, and only an actual signal to an actual process proves that.
"""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.handles import CHEESE_HANDLE
from tests.conftest import StubChannel, drain_hooks, stub_compute

SESSION_ID = "sess-exit-path"


class _Screen(StubChannel):
    """Announces its session id on SessionStart and then ends the turn the way
    this test wants it to end."""

    def __init__(self, *, mode: str) -> None:
        # Squeezed watchdog: the `provider_error` case is a session that goes
        # quiet, and that is what the watchdog is for. This screen announces
        # itself first, so `delivered` is true, and the ceiling rather than the
        # delivery deadline is what ends that turn.
        #
        # The ceiling this number becomes is the OUTER one, in runtime.py: a
        # backend's `hard_ceiling_s` is sent out as a `turn_ceiling` frame and
        # the outer wall-clock wrap is what acts on it first. Sub-second values
        # used to race the setup path there, because that clock started before
        # the screen was reached: the deadline was spent before there was
        # anything to watch, the monitor saw an expired ceiling on its first
        # pass, and the verdict reached nobody, so the socket read blocked until
        # pytest-timeout killed the run. Same trap cost test_replay_visibility
        # ~40% of its CI runs. `ceiling_deadline` now measures from
        # `prompt_delivered` and refuses a moment already past, so setup is out
        # of the race; this stays a few seconds anyway, because what it has to
        # outlast is the monitor's own polling, not the setup.
        super().__init__(idle_suspect_s=0.2, hard_ceiling_s=3.0, delivery_timeout_s=0.2)
        self._mode = mode

    async def send_prompt(
        self, screen: uuid.UUID, prompt: str, images: list[dict] | None = None
    ) -> bool:
        del images
        self.last_prompt = prompt
        if self.on_start is not None:
            self.on_start()
        self.starts(screen, session_id=SESSION_ID)
        if self._mode == "raise":
            raise RuntimeError("provider exploded mid-write")
        if self._mode != "provider_error":
            asyncio.get_running_loop().call_soon(self.stops, screen, "done", SESSION_ID)
        # `provider_error` / `hang`: nothing more is ever said.
        return True


def _seed_topic(client) -> str:
    pid = client.post("/projects", json={"name": "P"}).json()["data"]["id"]
    return client.post("/topics", json={"project_id": pid, "title": "退出路径"}).json()[
        "data"
    ]["id"]


async def _stored_session_id(factory, topic_id: str) -> str | None:
    """Read the pointer back through a FRESH session — a value that is only
    visible inside the writer's own transaction is not persisted.

    A room's conversation belongs to the agent having it; these topics are all
    served by the project's implicit 芝士, so that is the key to read under."""
    async with factory() as s:
        return await AgentSessionService(s).resume_token(
            uuid.UUID(topic_id), CHEESE_HANDLE
        )


def _run_turn(client, tmp_path, topic_id: str, mode: str) -> None:
    """Drive one turn to completion (or to its failure) on the client DB."""
    screen = _Screen(mode=mode)
    chat = ChatService(
        session_factory=client.test_factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    async def _go() -> None:
        agen = chat.converse(
            topic_id=uuid.UUID(topic_id), author="u", content="做事", summon=True
        )
        reached = asyncio.Event()
        screen.on_start = reached.set
        task = asyncio.ensure_future(_drain(agen))
        if mode == "hang":
            # The caller goes away while the session keeps working — a
            # cancelled request, a closed socket, a killed wrapper. The turn is
            # not the caller's to end, so this must change nothing. Cancelled
            # only once the session HAS it: cancelling earlier would be testing
            # that a turn which never started announces nothing.
            await asyncio.wait_for(reached.wait(), 5)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        else:
            with contextlib.suppress(RuntimeError):
                await task  # 异常中断 — the assertion is about the pointer
        # The pointer is written by the SUBSCRIPTION, on its own task: the call
        # that started the turn returns long before. Waiting for the session's
        # hooks to be consumed is what makes this a test of the write and not
        # of the ordering.
        await drain_hooks(screen, uuid.UUID(topic_id))

    async def _drain(agen) -> None:
        async for _ in agen:
            pass

    asyncio.run(_go())


@pytest.mark.parametrize(
    "mode",
    ["normal", "provider_error", "raise", "hang"],
    ids=["正常结束", "provider报错", "异常中断", "取消"],
)
def test_session_pointer_committed_on_every_in_process_exit(
    client, tmp_path, mode: str
):
    topic_id = _seed_topic(client)
    _run_turn(client, tmp_path, topic_id, mode)
    stored = asyncio.run(_stored_session_id(client.test_factory, topic_id))
    assert stored == SESSION_ID, f"退出路径「{mode}」没有把会话指针落库"


# --- 5. 硬杀 -----------------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import asyncio, os, sys, uuid
    os.environ["CHEESEX_TEST_NULLPOOL"] = "1"
    os.environ.setdefault("OPENAI_API_KEY", "k")
    os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", "k")

    from app.core.config import settings
    settings.database_url = sys.argv[1]
    settings.memory_backend = "db"

    import app.models  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.domain.agent.chat import ChatService
    from app.domain.agent.compute import ComputePool
    from tests.conftest import StubChannel

    DSN, TOPIC, MARKER, WS, SID = sys.argv[1:6]

    class A(StubChannel):
        async def send_prompt(self, screen, prompt):
            self.starts(screen, session_id=SID)
            return True

    async def main():
        engine = create_async_engine(DSN, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        screen = A()
        chat = ChatService(
            session_factory=factory,
            base_system_prompt="你是芝士。",
            workspace_root=WS,
            compute=ComputePool([screen.runtime], screen.name),
        )
        async for _ in chat.converse(
            topic_id=uuid.UUID(TOPIC), author="u", content="做事", summon=True
        ):
            pass
        # The SessionStart above is consumed off the subscription, so wait for
        # the pointer to actually be in the DB before saying "ready": the marker
        # is what tells the parent it may kill us, and killing us early would
        # test nothing.
        from app.domain.agent_session.services import AgentSessionService
        from app.domain.identity.handles import CHEESE_HANDLE

        for _ in range(500):
            async with factory() as s:
                token = await AgentSessionService(s).resume_token(
                    uuid.UUID(TOPIC), CHEESE_HANDLE
                )
            if token == SID:
                break
            await asyncio.sleep(0.01)
        open(MARKER, "w").write("ready")
        await asyncio.sleep(3600)

    asyncio.run(main())
    """
)


def test_session_pointer_survives_a_real_sigkill(client, tmp_path):
    """硬杀那条要真的杀进程 — SIGKILL cannot be caught, so nothing in the turn's
    own error handling can save it. Only a pointer that was ALREADY committed
    is there afterwards."""
    from tests.conftest import TEST_DATABASE_URL

    topic_id = _seed_topic(client)
    marker = tmp_path / "ready"
    script = tmp_path / "child.py"
    script.write_text(_CHILD)

    # The child runs from tmp_path, so `backend/` has to reach it via PYTHONPATH:
    # a script's own directory is what lands on sys.path, never the cwd.
    backend_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    env = {**os.environ, "PYTHONPATH": backend_root}

    child = subprocess.Popen(
        [
            sys.executable,
            str(script),
            TEST_DATABASE_URL,
            topic_id,
            str(marker),
            str(tmp_path / "ws"),
            SESSION_ID,
        ],
        cwd=backend_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if marker.exists():
                break
            if child.poll() is not None:
                out, err = child.communicate()
                pytest.fail(
                    "child died before announcing its session:\n"
                    f"{out.decode()[-2000:]}\n{err.decode()[-2000:]}"
                )
            time.sleep(0.05)
        else:
            pytest.fail("child never reached the session announcement")

        # No grace period, no signal handler, no chance to flush anything.
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=30)
        assert child.returncode != 0
    finally:
        if child.poll() is None:  # never leak the child on a failed assert
            os.kill(child.pid, signal.SIGKILL)
            child.wait(timeout=30)

    stored = asyncio.run(_stored_session_id(client.test_factory, topic_id))
    assert stored == SESSION_ID, (
        "被 SIGKILL 的轮次没留下会话指针——续跑会开一个全新会话，"
        "芝士将不记得自己已经做过什么"
    )


class _SilentScreen(StubChannel):
    """A screen that dies before announcing anything — the one case where there
    genuinely is no session to point at."""

    async def send_prompt(
        self, screen: uuid.UUID, prompt: str, images: list[dict] | None = None
    ) -> bool:
        del screen, prompt, images
        raise RuntimeError("died before SessionStart")


def test_no_session_announced_leaves_the_pointer_null(client, tmp_path):
    """The complement of every assertion above, and the reason they are not
    vacuous: the pointer is written because a session was ANNOUNCED, not
    because the turn happened to touch the topic row. A turn that dies before
    SessionStart must leave NULL — there is nothing to resume, and a pointer to
    a session that does not exist would send the next turn to `--resume` a
    transcript that was never written."""
    topic_id = _seed_topic(client)
    chat = ChatService(
        session_factory=client.test_factory,
        compute=stub_compute(_SilentScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    async def _go() -> None:
        try:
            async for _ in chat.converse(
                topic_id=uuid.UUID(topic_id), author="u", content="做事", summon=True
            ):
                pass
        except RuntimeError:
            pass

    asyncio.run(_go())
    assert asyncio.run(_stored_session_id(client.test_factory, topic_id)) is None
