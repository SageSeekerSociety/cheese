"""每一条退出路径上，会话指针都必须已经落库 (④ 超时重跑, 上半).

The property under test is stated as an invariant rather than a happy path,
because the bug it replaces was precisely a path nobody enumerated:

    Once 芝士 has announced its session id, ``topic.session_id`` is committed —
    no matter how the turn ends.

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
import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid

import pytest
from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.service import (
    AgentResult,
    AgentService,
    AgentSessionInfo,
)
from app.domain.topic.models import Topic

SESSION_ID = "sess-exit-path"


class _Agent(AgentService):
    """Announces its session id (as the hooks backends do on SessionStart) and
    then ends the turn the way this test wants it to end."""

    def __init__(self, *, mode: str) -> None:
        super().__init__(model="stub")
        self._mode = mode

    async def stream_reply(self, **_):
        yield AgentSessionInfo(session_id=SESSION_ID)
        if self._mode == "raise":
            raise RuntimeError("provider exploded mid-stream")
        if self._mode == "hang":
            await asyncio.sleep(3600)  # cancelled from outside
        yield AgentResult(
            text="done",
            session_id=SESSION_ID,
            is_error=(self._mode == "provider_error"),
        )


def _seed_topic(client) -> str:
    pid = client.post("/projects", json={"name": "P"}).json()["data"]["id"]
    return client.post("/topics", json={"project_id": pid, "title": "退出路径"}).json()[
        "data"
    ]["id"]


async def _stored_session_id(factory, topic_id: str) -> str | None:
    """Read the pointer back through a FRESH session — a value that is only
    visible inside the writer's own transaction is not persisted."""
    async with factory() as s:
        topic = (
            await s.execute(select(Topic).where(Topic.id == uuid.UUID(topic_id)))
        ).scalar_one()
        return topic.session_id


def _run_turn(client, tmp_path, topic_id: str, mode: str) -> None:
    """Drive one turn to completion (or to its failure) on the client DB."""
    chat = ChatService(
        session_factory=client.test_factory,
        agent=_Agent(mode=mode),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    async def _go() -> None:
        agen = chat.converse(
            topic_id=uuid.UUID(topic_id), author="u", content="做事", summon=True
        )
        if mode == "hang":
            # What the wall-clock ceiling does to a wedged turn: cancel it.
            task = asyncio.ensure_future(_drain(agen))
            await asyncio.sleep(0.5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return
        try:
            await _drain(agen)
        except RuntimeError:
            pass  # case 3 — the turn dies, the assertion is about the pointer

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
    from app.domain.agent.service import AgentService, AgentSessionInfo

    DSN, TOPIC, MARKER, WS, SID = sys.argv[1:6]

    class A(AgentService):
        def __init__(self):
            super().__init__(model="stub")

        async def stream_reply(self, **_):
            yield AgentSessionInfo(session_id=SID)
            # The consumer fully handles the event above — including committing
            # the pointer — before it asks this generator for the next one. So
            # the marker landing PROVES the write already happened; the parent
            # can kill us the instant it appears.
            open(MARKER, "w").write("ready")
            await asyncio.sleep(3600)

    async def main():
        engine = create_async_engine(DSN, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        chat = ChatService(
            session_factory=factory,
            agent=A(),
            base_system_prompt="你是芝士。",
            workspace_root=WS,
        )
        async for _ in chat.converse(
            topic_id=uuid.UUID(TOPIC), author="u", content="做事", summon=True
        ):
            pass

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


class _SilentAgent(AgentService):
    """A provider that dies before announcing anything — the one case where
    there genuinely is no session to point at."""

    def __init__(self) -> None:
        super().__init__(model="stub")

    async def stream_reply(self, **_):
        raise RuntimeError("died before SessionStart")
        yield  # pragma: no cover — makes this an async generator


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
        agent=_SilentAgent(),
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
