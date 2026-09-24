"""The harness contract as something that runs, and the fixtures both languages read.

Two things live here.

``ContractHarness`` is the smallest thing that is an ``AgentRuntime``: it
declares no capability at all — no pane, no partial output, no gateway — and
does nothing but keep the six verbs' promises, plus the four hard requirements
every harness owes (``harness.SubagentRequirement``, 结论 43). Subagents are on
the second list rather than the first: they are not a capability a harness may
decline, so the minimal runtime has them too, and what it declines is only what
a difference code can still name. It exists because
those promises are today only prose in ``harness/__init__.py``'s docstrings
("reading never consumes", "interrupt is weaker than close"), and prose is not
something a new harness can be held to. A scenario in
``backend/tests/fixtures/harness-contract/`` is played against this, so what a
harness has to do is written down as steps rather than as adjectives.

``fixtures()`` and ``vocabulary()`` read that directory. The same files are read
by ``backend/tests/extension/harness-contract.test.ts`` over in Node, which is
the point of them being files: the backend and the extension pi loads never
import each other, and a rule both are held to is the only thing that can stop
them drifting apart quietly. Neither reader can be made green by editing the
other.

The fixtures are written by hand, not generated. A fixture generated from the
implementation agrees with it by construction and goes on agreeing after the
implementation breaks — that is exactly what ``test_harness_prompt_contract.py``
was doing, and why this set replaces it.
"""

import json
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.agent.harness import HarnessEvent, Opening, SessionRef
from app.domain.agent.service import AgentEvent, AgentMessage
from tests.support.fake_subagent import FakeSubagent

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "harness-contract"
VOCABULARY = "vocabulary.json"


def vocabulary() -> dict[str, Any]:
    """The closed word list every scenario is written in."""
    return json.loads((FIXTURE_DIR / VOCABULARY).read_text(encoding="utf-8"))


def fixtures() -> list[dict[str, Any]]:
    """Every committed scenario, ordered by file name (the Node side's order)."""
    return [
        {"name": path.name, **json.loads(path.read_text(encoding="utf-8"))}
        for path in sorted(FIXTURE_DIR.glob("*.json"))
        if path.name != VOCABULARY
    ]


@dataclass
class _Said:
    """One thing this session said, and which sub-thread said it.

    An empty label is the session's own thread — the same answer the platform
    reads off a main thread's records, where the label is absent rather than
    blank.
    """

    text: str
    thread_label: str = ""


@dataclass
class _Held:
    """One conversation this runtime is holding, workers included."""

    ref: SessionRef
    said: list[_Said] = field(default_factory=list)
    landed: int = 0
    #: 这条会话里的子线程。和会话同生同死（结论 43），所以住在这里而不是在
    #: runtime 上另立一张表：会话没了它们也没了，不需要第二处去清。
    workers: list[FakeSubagent] = field(default_factory=list)
    #: 送进这条会话、还没被父线程处理的消息。人对卡的操作走的就是这条路。
    delivered: list[str] = field(default_factory=list)

    def say(self, text: str, thread_label: str = "") -> None:
        self.said.append(_Said(text, thread_label))

    def stop_workers(self) -> None:
        for worker in self.workers:
            worker.stop()


class ContractBacklog:
    """The unread tail of a ``ContractHarness`` session.

    Nothing is ever half-arrived here: a harness that reports partial output
    has to answer ``unfinished`` and ``give_up`` with something, and the minimal
    one has no partial output to report. That is a capability it declines, not
    a corner it cuts.
    """

    def __init__(self, held: _Held):
        self._held = held
        # A snapshot, as the protocol says: landing things during a pass must
        # not change what this pass was handed.
        self._entries = [
            HarnessEvent(
                key=f"{index:019d}",
                eid=f"contract:{index}",
                record=said,
                age_s=0.0,
            )
            for index, said in enumerate(held.said)
            if index >= held.landed
        ]

    def unread(self) -> Sequence[HarnessEvent]:
        return list(self._entries)

    def assemble(self, entry: HarnessEvent) -> Sequence[AgentEvent]:
        said = entry.record
        assert isinstance(said, _Said)
        return [
            AgentMessage(
                said.text,
                eid=entry.eid,
                eids=(entry.eid,),
                # 子线程说的话带着它的标识出来，主线程说的不带（None 就是答案，
                # 不是一个待补的空）。
                thread_label=said.thread_label or None,
            )
        ]

    def unfinished(self) -> set[str]:
        return set()

    def give_up(self) -> Sequence[AgentMessage]:
        return []

    def landed(self, *, through: str) -> None:
        self._held.landed = max(self._held.landed, int(through) + 1)

    def forget(self, *, older_than_s: float) -> None:
        return None


class ContractHarness:
    """An ``AgentRuntime`` that declares nothing and keeps the six verbs."""

    harness = "contract"

    def __init__(self) -> None:
        self._held: dict[uuid.UUID, _Held] = {}

    # --- the six verbs ------------------------------------------------------

    async def ensure(
        self, session: SessionRef, opening: Opening, *, work_id: uuid.UUID | None = None
    ) -> object:
        held = self._held.get(session.topic_id)
        if held is None:
            held = self._held[session.topic_id] = _Held(session)
        return held

    async def send(
        self,
        session: SessionRef,
        message: str,
        opening: Opening,
        *,
        work_id: uuid.UUID,
        on_mark: Callable[[uuid.UUID], None],
        images: list[dict] | None = None,
    ) -> bool | None:
        held = await self.ensure(session, opening)
        assert isinstance(held, _Held)
        held.say(message)
        return True

    def backlog(self, session: SessionRef) -> ContractBacklog:
        held = self._held.get(session.topic_id) or _Held(session)
        return ContractBacklog(held)

    async def deliver(
        self,
        topic_id: uuid.UUID,
        text: str,
        images: list[dict] | None = None,
        *,
        expected_work_id: uuid.UUID | None = None,
    ) -> bool:
        held = self._held.get(topic_id)
        if held is None:
            return False
        # 送到**父**线程，不是送给某条子线程：人对卡的操作投递给父线程执行
        # （结论 43），改子线程指令的是那个父线程自己。
        held.delivered.append(text)
        return True

    async def interrupt(self, session: SessionRef) -> bool:
        # The work stops; the conversation does not — the session stays held
        # and the next send continues it, which is the whole of 「interrupt is
        # weaker than close」. There is no work to stop in a runtime with no
        # model behind it, so what this verb has to keep is what it does NOT
        # touch.
        held = self._held.get(session.topic_id)
        if held is None:
            return False
        # 停的是父线程，它的子线程跟着停——停住了父线程而 worker 还在往房间里写，
        # 时间线上看不出和没停有什么分别。
        held.stop_workers()
        return True

    async def close(self, session: SessionRef) -> None:
        held = self._held.pop(session.topic_id, None)
        if held is not None:
            # 同生同死（结论 43）：会话没了，它起的子线程一个都不剩。
            held.stop_workers()

    # --- 四条硬性要求里 agent 那一侧的动作 -----------------------------------

    def spawn(
        self, session: SessionRef, *, label: str, model: str, instruction: str
    ) -> FakeSubagent:
        """起一条子线程。

        演的是 **agent 的那次工具调用**，不是契约上的第七个动词：平台这一侧没有
        「派活」的路径（结论 43），所以这个方法不在 ``AgentRuntime`` 上。
        """
        held = self._held.get(session.topic_id)
        if held is None:
            raise LookupError("起子 agent 的是会话里的父线程，这条会话还没起")
        worker = FakeSubagent(
            label=label, model=model, instruction=instruction, say=held.say
        )
        held.workers.append(worker)
        return worker

    def workers(self, session: SessionRef) -> list[FakeSubagent]:
        """这条会话里起过的子线程。"""
        held = self._held.get(session.topic_id)
        return list(held.workers) if held else []

    def delivered(self, session: SessionRef) -> list[str]:
        """送进这条会话的父线程、等着它处理的消息。"""
        held = self._held.get(session.topic_id)
        return list(held.delivered) if held else []

    # --- the rest of the protocol -------------------------------------------

    @property
    def hard_ceiling_s(self) -> float:
        return 600.0

    async def run_turn(self, **_: Any) -> AsyncIterator[AgentEvent]:
        # Declared because the protocol declares it. A runtime with no model
        # behind it has no turn to iterate, and saying so is the honest answer.
        raise NotImplementedError("the contract harness runs no model")
        yield  # pragma: no cover - makes this an async generator

    # The protocol asks a runtime to accept these four; it does not ask it to
    # keep them. Nothing here ever produces an event, an activity ping, a
    # receipt or an unread count, so a field holding the consumer would be
    # state with no reader — the kind of thing this set exists to delete.
    def bind_events(self, consumer: Any) -> None:
        return None

    def bind_activity(self, consumer: Any) -> None:
        return None

    def bind_receipts(self, consumer: Any) -> None:
        return None

    def bind_unread_probe(self, probe: Any) -> None:
        return None

    def holds(self, topic_id: uuid.UUID) -> bool:
        return topic_id in self._held

    async def recover(self, device_id: str | None = None) -> list[SessionRef]:
        return [held.ref for held in self._held.values()]

    async def replay(self, session: SessionRef, *, known_texts: set[str]) -> None:
        return None
