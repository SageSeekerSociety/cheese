"""A recover round's death evidence is folded in one pass, not once per session.

`death_evidence.refresh` runs on every takeover and device reconnect. It
used to ask the compute pool, for every session ever given a resume token,
which conversations each runtime had found and named dead: each of those
asks walked every runtime's whole set, and the sessions were read whole to
get three columns. In production that held the event loop for up to 850 ms
(`core/loop_stalls`)."""

import asyncio
import uuid
from types import SimpleNamespace

from app.domain.agent import death_evidence
from app.domain.agent.compute import ComputePool


class CountingSet(set):
    walks = 0

    def __iter__(self):
        CountingSet.walks += 1
        return super().__iter__()


class Pool:
    """The pool's own evidence methods, over one runtime."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def _runtimes(self):
        return [self.runtime]

    def __getattr__(self, name):
        return getattr(ComputePool, name).__get__(self)


class Session:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        rows = self.rows
        pointers = [
            SimpleNamespace(conversation_id=c, agent_handle=h, resume_token=t)
            for c, h, t in rows
        ]
        return SimpleNamespace(
            all=lambda: rows,
            scalars=lambda: SimpleNamespace(all=lambda: pointers),
        )


def test_a_round_walks_each_runtimes_evidence_a_bounded_number_of_times():
    alive, dead = uuid.uuid4(), uuid.uuid4()
    others = [(uuid.uuid4(), "x", f"t{k}") for k in range(200)]
    runtime = SimpleNamespace(
        found_conversations=CountingSet({((alive, "a"), "t-alive")}),
        terminal_conversations=CountingSet({((dead, "b"), "t-dead")}),
    )
    live = SimpleNamespace(dead_sessions={(alive, "a", "t-alive")})
    session = Session([(alive, "a", "t-alive"), (dead, "b", "t-dead"), *others])
    CountingSet.walks = 0

    asyncio.run(death_evidence.refresh(session, Pool(runtime), live))

    assert live.dead_sessions == {(dead, "b", "t-dead")}
    assert CountingSet.walks <= 4, CountingSet.walks


def test_a_round_with_no_evidence_reads_nothing():
    runtime = SimpleNamespace(
        found_conversations=CountingSet(), terminal_conversations=CountingSet()
    )
    live = SimpleNamespace(dead_sessions=set())
    session = Session([(uuid.uuid4(), "x", "t")])

    asyncio.run(death_evidence.refresh(session, Pool(runtime), live))

    assert session.statements == []
    assert live.dead_sessions == set()
