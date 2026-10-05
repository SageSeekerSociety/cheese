"""Isolated full-service recovery client for a retained native runner socket.

Discovery uses the test's saved descriptor, not production device discovery.
The worker never starts or stops the runner/native process.
"""

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.driven.runner import socket_path
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks
from tests.support.seat_channel import SeatChannel


class SocketChannel(SeatChannel):
    name = "isolated-native-socket"
    device = "isolated-device"

    def __init__(self, descriptor):
        super().__init__()
        self.descriptor = descriptor
        # The same mirrors the process before this one landed into.
        self.root = Path(descriptor["root"])
        session = SessionRef(
            uuid.UUID(descriptor["project"]),
            uuid.UUID(descriptor["topic"]),
            descriptor["session_agent"],
            harness=CLAUDE_CODE,
        )
        self.seats[(session.topic_id, descriptor["agent"])] = (
            session,
            descriptor["placed_state"],
        )
        self.handle = SimpleNamespace(
            session=session,
            agent_handle=descriptor["agent"],
            state=descriptor["state"],
            session_id=descriptor["native"],
        )
        self.calls = []
        self.input_states = []

    async def open(self, session, agent, launch):
        # The runner outlived the process before this one: it is reused as it
        # is, never started again.
        assert session == self.handle.session and agent == self.handle.agent_handle

    async def call(self, handle, method, params):
        assert handle.agent_handle == self.handle.agent_handle
        assert method not in ("interrupt", "close"), (
            "Recovery may not stop its executor"
        )
        if method in ("send", "steer"):
            status = await self.call(handle, "ping", {})
            self.input_states.append(
                {
                    "method": method,
                    "input_id": params.get("input_id"),
                    "work_id": params.get("work_id"),
                    "working": status["working"],
                    "native_work_id": status.get("work_id"),
                    "session_id": status["session_id"],
                }
            )
            assert (method == "steer") == status["working"], status
        self.calls.append(method)
        reader, writer = await asyncio.open_unix_connection(
            socket_path(Path(self.handle.state))
        )
        try:
            writer.write(
                json.dumps({"method": method, "params": params}).encode() + b"\n"
            )
            await writer.drain()
            answer = json.loads(await reader.readline())
            assert "error" not in answer, answer
            return answer["result"]
        finally:
            writer.close()
            await writer.wait_closed()


async def run(descriptor):
    engine = create_async_engine(descriptor["database"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    channel = SocketChannel(descriptor)
    chat = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=descriptor["workspace"],
        compute=ComputePool([channel.runtime], channel.name),
    )
    topic, work = uuid.UUID(descriptor["topic"]), uuid.UUID(descriptor["work"])
    try:
        assert await chat.recover_sessions() == 1
        await chat.replays_settled()
        status = await channel.call(channel.handle, "ping", {})
        assert status["pid"] == descriptor["native_pid"]
        assert not any(method in ("send", "steer") for method in channel.calls)
        busy = descriptor["mode"].endswith("busy")
        if busy:
            assert status["working"] and status["work_id"] == str(work)
            assert (topic, work) in chat._hook_work
            assert await chat.notify_running_turn(
                topic,
                "原答者已提交回答",
                blocks=tuple(uuid.UUID(x) for x in descriptor["blocks"]),
            )
            Path(descriptor["gate"]).touch()
        else:
            assert not status["working"] and not chat._hook_work
            from app.api.deps import get_work_runner
            from app.domain.delivery.addressing import Addressed, Recipient

            work_runner = get_work_runner()
            turn = work_runner.submit(
                chat,
                topic,
                author="alice",
                content="@芝士 原答者已提交回答",
                addressed=Addressed((Recipient(descriptor["agent"], "asked"),)),
            )
            async with asyncio.timeout(90):
                while work_runner.turn_pending(turn):
                    await asyncio.sleep(0.05)
        # Settled, not merely idle. In the busy HTTP case the gate-waiting Bash
        # is in the background (see below) and can outlast the work's result;
        # its notification then starts one more turn of the session's own a
        # moment later, after `working` has already gone false once. So wait
        # for nothing running in the foreground or the background, held for a
        # second: a turn row that stays open past that is the bug asserted
        # below, not a turn about to close.
        quiet_since = None
        async with asyncio.timeout(90):
            while True:
                status = await channel.call(channel.handle, "ping", {})
                if status["working"] or status["tasks"] or chat._hook_work:
                    quiet_since = None
                elif quiet_since is None:
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since >= 1.0:
                    break
                await asyncio.sleep(0.05)
        async with factory() as session:
            rows = list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.topic_id == topic)
                )
            )
            assert len(rows) == 2
            assert {row.native_session_id for row in rows} == {descriptor["native"]}
            assert all(row.echoed_at and row.settled_at for row in rows)
            assert all(
                set(row.held_block_ids) <= set(row.released_block_ids) for row in rows
            )
            assert (
                await held_blocks(
                    session,
                    project_id=uuid.UUID(descriptor["project"]),
                    topic_id=topic,
                    recipient_handle=descriptor["agent"],
                )
                == set()
            )
            for row in rows:
                ids = [uuid.UUID(x) for x in row.held_block_ids]
                assert ids
                blocks = list(
                    await session.scalars(select(Block).where(Block.id.in_(ids)))
                )
                assert len(blocks) == len(ids)
                assert {consumed_turn(block) for block in blocks} == {
                    str(row.execution_work_id)
                }
            turns = list(
                await session.scalars(
                    select(AgentTurn).where(AgentTurn.topic_id == topic)
                )
            )
            # Leave the evidence behind if anything below fails.
            print(
                "TURNS "
                + json.dumps(
                    [
                        {
                            "id": str(t.id),
                            "author": t.author,
                            "started": str(t.started_at),
                            "stopped": str(t.stopped_at),
                        }
                        for t in turns
                    ]
                ),
                file=sys.stderr,
                flush=True,
            )
            # No turn is left running: a room showing its agent at work when
            # nothing is is the bug a stray turn row would make.
            assert all(t.stopped_at for t in turns), "a turn was left running"
            if busy:
                assert {row.execution_work_id for row in rows} == {work}
                assert len(turns) == 1
                assert channel.calls.count("steer") == 1
                assert channel.calls.count("send") == 0
            else:
                assert len({row.execution_work_id for row in rows}) == 2
                assert len(turns) == 2
                assert channel.calls.count("send") == 1
                assert channel.calls.count("steer") == 0
        print(
            json.dumps(
                {
                    "pid": __import__("os").getpid(),
                    "native_pid": status["pid"],
                    "native": status["session_id"],
                    "send": channel.calls.count("send"),
                    "steer": channel.calls.count("steer"),
                    "turns": len(turns),
                    "input_states": channel.input_states,
                }
            ),
            flush=True,
        )
    finally:
        await channel.runtime.stop_listening()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run(json.loads(Path(sys.argv[1]).read_text())))
