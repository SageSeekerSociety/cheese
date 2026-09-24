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
from app.domain.agent.harness import deployment_harness
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.handles import CHEESE_HANDLE
from tests.conftest import StubChannel, drain_hooks, stub_compute
from tests.integration.conftest import post_project

SESSION_ID = "sess-exit-path"


class _Screen(StubChannel):
    """A session started as SESSION_ID, which then ends the turn the way this
    test wants it to end."""

    new_session_id = SESSION_ID

    def __init__(self, *, mode: str) -> None:
        super().__init__()
        self._mode = mode

    async def call(self, handle, method: str, params: dict) -> dict:
        if method == "send" and self._mode == "raise":
            # The session was up and had said who it is; the write of this
            # input then failed.
            self.starts(handle.session.topic_id, session_id=SESSION_ID)
            raise RuntimeError("provider exploded mid-write")
        return await super().call(handle, method, params)

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        self.starts(topic_id, session_id=SESSION_ID)
        self.acknowledges(topic_id, prompt)
        if self._mode == "normal":
            self.stops(topic_id, "done", SESSION_ID)
        elif self._mode == "provider_error":
            self.record(
                topic_id,
                type="result",
                subtype="success",
                is_error=True,
                result="API Error: 500",
                session_id=SESSION_ID,
            )
        # `hang`: the session keeps working; nothing more is said yet.


def _seed_topic(client) -> str:
    pid = post_project(client, json={"name": "P"}).json()["data"]["id"]
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
            uuid.UUID(topic_id), CHEESE_HANDLE, harness=deployment_harness()
        )


def _run_turn(client, tmp_path, topic_id: str, mode: str) -> None:
    """Drive one turn to completion (or to its failure) on the client DB."""
    screen = _Screen(mode=mode)
    chat = ChatService(
        session_factory=client.test_request_factory,
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
        # records to be consumed is what makes this a test of the write and not
        # of the ordering.
        await drain_hooks(screen, uuid.UUID(topic_id))
        if mode == "hang":
            # Asserted after this returns; the session finishing afterwards
            # changes nothing about what was committed before it did.
            screen.stops(uuid.UUID(topic_id), "done", SESSION_ID)
            await drain_hooks(screen, uuid.UUID(topic_id))

    async def _drain(agen) -> None:
        async for _ in agen:
            pass

    client.portal.call(_go)


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
    stored = client.portal.call(
        _stored_session_id, client.test_request_factory, topic_id
    )
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

    import app.models  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.domain.agent.chat import ChatService
    from app.domain.agent.compute import ComputePool
    from tests.conftest import StubChannel

    DSN, TOPIC, MARKER, WS, SID = sys.argv[1:6]

    class A(StubChannel):
        new_session_id = SID

        def emit_turn(self, topic_id, prompt, reply):
            self.starts(topic_id, session_id=SID)

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
        # The init record above is consumed off the subscription, so wait for
        # the pointer to actually be in the DB before saying "ready": the marker
        # is what tells the parent it may kill us, and killing us early would
        # test nothing.
        from app.domain.agent.harness import deployment_harness
        from app.domain.agent_session.services import AgentSessionService
        from app.domain.identity.handles import CHEESE_HANDLE

        for _ in range(500):
            async with factory() as s:
                token = await AgentSessionService(s).resume_token(
                    uuid.UUID(TOPIC), CHEESE_HANDLE, harness=deployment_harness()
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

    stored = client.portal.call(
        _stored_session_id, client.test_request_factory, topic_id
    )
    assert stored == SESSION_ID, (
        "被 SIGKILL 的轮次没留下会话指针——重发/重新 @ 会开一个全新会话，"
        "芝士将不记得自己已经做过什么"
    )


class _SilentScreen(StubChannel):
    """A session that never comes up — the one case where there genuinely is
    no session to point at."""

    async def ensure(self, session, opening):
        raise RuntimeError("the session never started")


def test_no_session_announced_leaves_the_pointer_null(client, tmp_path):
    """The complement of every assertion above, and the reason they are not
    vacuous: the pointer is written because a session was ANNOUNCED, not
    because the turn happened to touch the topic row. A turn whose session never
    started must leave NULL — there is nothing to resume, and a pointer to
    a session that does not exist would send the next turn to `--resume` a
    transcript that was never written."""
    topic_id = _seed_topic(client)
    chat = ChatService(
        session_factory=client.test_request_factory,
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

    client.portal.call(_go)
    assert (
        client.portal.call(_stored_session_id, client.test_request_factory, topic_id)
        is None
    )
