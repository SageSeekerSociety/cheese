"""The harness contract as something that runs, and the fixtures both languages read.

Two things live here.

``ContractHarness`` is the smallest thing that is an ``AgentRuntime``: it
declares no capability at all — no pane, no subagents, no partial output, no
gateway — and does nothing but keep the six verbs' promises. It exists because
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
class _Held:
    """One conversation this runtime is holding."""

    ref: SessionRef
    said: list[str] = field(default_factory=list)
    landed: int = 0


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
                record=text,
                age_s=0.0,
            )
            for index, text in enumerate(held.said)
            if index >= held.landed
        ]

    def unread(self) -> Sequence[HarnessEvent]:
        return list(self._entries)

    def assemble(self, entry: HarnessEvent) -> Sequence[AgentEvent]:
        return [AgentMessage(str(entry.record), eid=entry.eid, eids=(entry.eid,))]

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
        held.said.append(message)
        return True

    def backlog(self, session: SessionRef) -> ContractBacklog:
        held = self._held.get(session.topic_id) or _Held(session)
        return ContractBacklog(held)

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        return topic_id in self._held

    async def interrupt(self, session: SessionRef) -> bool:
        # The work stops; the conversation does not — the session stays held
        # and the next send continues it, which is the whole of 「interrupt is
        # weaker than close」. There is no work to stop in a runtime with no
        # model behind it, so what this verb has to keep is what it does NOT
        # touch.
        return session.topic_id in self._held

    async def close(self, session: SessionRef) -> None:
        self._held.pop(session.topic_id, None)

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
