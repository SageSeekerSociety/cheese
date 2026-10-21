"""ComputePool: the compute side of the two-pool model (design v2 R2 / v3).

Symmetric to AIPool (profiles.py). The pool answers WHERE a turn runs — which
machine pool — and WHAT runs there — which harness. Each backend it holds is a
room's sessions of one harness over one machine pool (`room/sessions/`),
started, spoken to and read on the session core.

Every backend in the pool keeps a live session. There used to be a second
shape — start a subprocess, stream what it says, exit — and every piece of
salvage machinery in the platform came from its one property: whoever held the
iterator owned the turn, so the turn died when that process did. The rooms it
ran are gone; what remains is the shape that can be reconnected to.
"""

import contextlib
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import (
    CLAUDE_CODE,
    HARNESSES,
    Capability,
    deployment_harnesses,
    harness_for,
    harness_name,
    harness_on,
)
from app.domain.agent.session_state import reports_lifecycle

if TYPE_CHECKING:
    from app.domain.agent.harness import SessionRef
    from app.domain.agent.room.reads import RoomReader
    from app.domain.agent.room.sessions import (
        MemoryConsumer,
        QuietListener,
        RoomSessions,
        UnreadProbe,
    )
    from app.domain.agent.session_host.contract import Image
    from app.domain.agent.session_host.host import SessionHost
    from app.domain.delivery.input_identity import InputRegistrar


class ComputePool:
    """Which backend a turn lands on: one per harness.

    Every session runs on the session host and reaches its machine through the
    one executor channel, whichever machine that is — a person's enrolled
    computer or one the platform owns. Which machine is the session's own
    environment, settled when it first needs hands (``machine/session_work``),
    so nothing here is keyed by machine. What runs is the agent type's
    ``harness``.
    """

    def __init__(
        self,
        backends: "list[RoomSessions]",
        owned: "dict[tuple[str, bool], RoomSessions] | None" = None,
    ):

        self._backends = {backend.harness: backend for backend in backends}
        # A member's own coding agent runs where its model connection is,
        # whatever the room chose (`owner_provider`): on its owner's machine,
        # or on the session host for one its owner's account holds. These are
        # kept apart from the machine pools a room chooses among, keyed by
        # harness and whether the account holds the connection.
        self._owned = dict(owned or {})
        self._harnesses: list[RoomSessions] = [  # type: ignore[assignment]
            *self._backends.values(),
            *self._owned.values(),
        ]
        # 部署列的骨架，在装配时解析一次：一个配错名字的部署在这里就起不来，而
        # 不是等到某一轮才发现自己跑的是另一个东西（结论 28）。没说骨架的平台工作
        # 落在偏好的第一个上。
        self._default = deployment_harnesses()[0]
        if self._default not in self._backends:
            raise ValueError(f"default backend {self._default!r} not registered")
        # 归属按座位记（topic, agent_handle）：一间房坐着几个 agent，各有各的
        # 会话和它的属主 runtime。按房间记的那个版本里，B 的轮次一 activate 就
        # 把 A 的会话 interrupt+close 掉——多 agent 同房间在这一层就不可能。
        self._owners: dict[tuple[uuid.UUID, str], RoomSessions] = {}

    async def activate(self, session: "SessionRef", runtime: "RoomSessions") -> None:
        """Park other harnesses before giving this seat to the selected one.

        Only THIS seat's previous tenants are parked: another agent's session
        in the same room is a different conversation on the same machine, and
        taking a seat must not evict the neighbour.
        """
        for previous in self._runtimes():
            if previous is not runtime and previous.holds(
                session.topic_id, session.agent_handle
            ):
                # A runner that let its idle session go is still on record
                # here, and there is nothing left of it to stop. Parking is
                # about taking the seat back, and it is taken back either way.
                with contextlib.suppress(DeviceCallError, DeviceOffline):
                    await previous.interrupt(session)
                await previous.close(session)
        self._owners[(session.topic_id, session.agent_handle)] = runtime

    async def dismiss(self, topic_id: uuid.UUID, agent_handle: str) -> None:
        """Take away the work this agent is doing in this room, on whichever
        harness holds its seat; what it wrote so far stays. For an agent no
        longer in the room: whatever it goes on doing, the room turns away."""
        for runtime in self._runtimes():
            held = runtime.holding((topic_id, agent_handle))
            if held is not None:
                with contextlib.suppress(DeviceCallError, DeviceOffline):
                    await runtime.interrupt(held)

    async def stop_work(self, conversation_id: uuid.UUID) -> None:
        """Take away the work every teammate is doing in this conversation;
        what they wrote so far stays. A seat with nothing in flight is left
        alone."""
        for runtime in self._runtimes():
            for seat in [seat for seat in runtime.work if seat[0] == conversation_id]:
                held = runtime.holding(seat)
                if held is not None:
                    with contextlib.suppress(DeviceCallError, DeviceOffline):
                        await runtime.interrupt(held)

    def working_conversations(self) -> set[uuid.UUID]:
        """Every conversation a seat this process holds is working in."""
        return {
            seat[0]
            for runtime in self._runtimes()
            for seat in runtime.work
            if runtime.holding(seat) is not None
        }

    def default(self) -> "RoomSessions":
        return self._backends[self._default]

    def _runtimes(self) -> "list[RoomSessions]":
        """Every harness's sessions in the pool, on every machine pool."""
        return self._harnesses

    async def steer(
        self,
        topic_id: uuid.UUID,
        text: str,
        images: "list[Image] | None" = None,
        *,
        register_input: "InputRegistrar",
        agent_handle: str | None = None,
        owes_reply: bool = False,
    ) -> bool:
        """Words for the seat whose owner was selected when its work started
        or was recovered.

        ``agent_handle`` aims the words at one seat. Without it the seats of
        the room are tried one by one — a seat whose session is not working
        says False and the next seat is asked.
        """
        candidates = [
            runtime
            for (topic, agent), runtime in self._owners.items()
            if topic == topic_id and (agent_handle is None or agent == agent_handle)
        ] or [
            runtime
            for runtime in self._runtimes()
            if runtime.holds(topic_id, agent_handle)
        ]
        for backend in candidates:
            delivered = await backend.steer(
                topic_id,
                text,
                images=images,
                register_input=register_input,
                agent_handle=agent_handle,
                owes_reply=owes_reply,
            )
            if delivered:
                return True
        return False

    async def recover_native_tools(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Ask the machine that owns this room to put this agent's platform
        tools back; without an agent, the one that answers for the room.

        Only a backend that can lose them answers; everything else says no, so
        the caller needs no test for which machine a room is on.
        """
        seat = (topic_id, agent_handle or "")
        for backend in self._runtimes():
            recover = getattr(backend, "recover_native_tools", None)
            if recover is None:
                continue
            runtime = backend
            if self._owners.get(seat) is runtime or runtime.holds(
                topic_id, agent_handle
            ):
                return await recover(topic_id, agent_handle)
        return False

    def seat_state(self, topic_id, agent_handle) -> str:
        """ "live" / "dead" / "unknown" for the seat, by whichever runtime
        can answer (FB-56 legacy③). A runtime without the probe cannot tell
        a watched death from an unanswered question — it says "unknown" by
        not having one, which is the safe answer."""
        seat = (topic_id, agent_handle)
        for runtime in self._runtimes():
            probe = getattr(runtime, "seat_state", None)
            if probe is None:
                continue
            state = probe(seat)
            if state != "unknown":
                return state
        return "unknown"

    def dead_conversations(self, topic_id, agent_handle) -> set[str]:
        """The conversations on this seat some runtime watched die
        (FB-56 legacy③) — conversation-scoped, never the seat's flag."""
        seat = (topic_id, agent_handle)
        found: set[str] = set()
        for runtime in self._runtimes():
            probe = getattr(runtime, "dead_conversations", None)
            if probe is not None:
                found.update(probe(seat))
        return found

    def terminal_conversations(self, topic_id, agent_handle) -> set[str]:
        """The conversations on this seat an authority's own per-conversation
        terminal answer named dead this recover round (FB-56 legacy③) —
        never a complement of somebody else's success."""
        seat = (topic_id, agent_handle)
        found: set[str] = set()
        for runtime in self._runtimes():
            found.update(
                conversation
                for pair_seat, conversation in getattr(
                    runtime, "terminal_conversations", set()
                )
                if pair_seat == seat
            )
        return found

    def found_conversations(self, topic_id, agent_handle) -> set[str]:
        """The conversations on this seat recovery actually reached and
        re-attached (FB-56 legacy③)."""
        seat = (topic_id, agent_handle)
        found: set[str] = set()
        for runtime in self._runtimes():
            found.update(
                conversation
                for pair_seat, conversation in getattr(
                    runtime, "found_conversations", set()
                )
                if pair_seat == seat
            )
        return found

    def report_to(
        self,
        reader: "RoomReader",
        *,
        unread: "UnreadProbe",
        memory: "MemoryConsumer",
        quiet: "QuietListener | None" = None,
    ) -> None:
        """Hand every harness's sessions the room's books: where the room hears
        what its sessions say and do (``RoomReader``), where it says what it
        sent that is still unread, where 「记忆该对账了」 goes — before an
        input and after a turn, for every harness: one whose sessions keep no
        memory files answers ``memory`` with None — and who brings a seat that
        went quiet up to date."""
        for runtime in self._runtimes():
            runtime.report_to(reader, unread=unread, memory=memory, quiet=quiet)

    def seat_runtime(
        self, topic_id: uuid.UUID, agent_handle: str
    ) -> "RoomSessions | None":
        """The backend holding this seat's session, if this process holds it."""
        return self._owners.get((topic_id, agent_handle))

    async def memory(self, session: "SessionRef", request: dict) -> dict | None:
        """Relay a memory reconciliation to whichever runtime holds this seat.

        ``None`` means «这个座位现在没有能对账的会话» — no live session, or a
        harness whose sessions keep no memory files. Both are ordinary answers,
        not failures.
        """
        owner = self._owners.get((session.topic_id, session.agent_handle))
        candidates = (
            [owner]
            if owner is not None
            else [
                runtime
                for runtime in self._runtimes()
                if runtime.holds(session.conversation_id, session.agent_handle)
            ]
        )
        for runtime in candidates:
            answer = await runtime.memory(session, request)
            if answer is not None:
                return answer
        return None

    def session_controls(self, topic_id: uuid.UUID) -> "RoomSessions | None":
        """The sessions whose live session in this room takes controls, if any.

        A harness with no control channel is still a harness: the room then
        simply shows less of it."""
        candidates = [
            runtime for (topic, _), runtime in self._owners.items() if topic == topic_id
        ] or self._runtimes()
        for runtime in candidates:
            if runtime.controls and runtime.holds(topic_id):
                return runtime
        return None

    def working_seats(self, topic_id: uuid.UUID) -> set[tuple[str, str]]:
        """Every seat whose session in this room is at work, as its own handle
        and the handle it acts under — the seats this process holds."""
        return {
            seat
            for runtime in self._runtimes()
            for seat in runtime.working_seats(topic_id)
        }

    def working_seat(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> tuple[str, str] | None:
        """The seat whose session in this room is at work, as its own handle
        and the handle it acts under: the one ``agent_handle`` names (by
        either), or, unnamed, the room's only one. None when there is none —
        or, unnamed, when several are, since picking one would be a guess.
        Only a seat this process holds: words can be put only into a session
        this process reads."""
        seats = {
            seat
            for runtime in self._runtimes()
            for seat in runtime.working_seats(topic_id, agent_handle)
        }
        return seats.pop() if len(seats) == 1 else None

    def work_in_flight(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> uuid.UUID | None:
        """The work a live session in this room is running, if one is.

        With the agent named the answer is that seat's; without it the room
        answers only when a single seat is working — two working teammates
        have two answers and picking one would be a guess.
        """
        works = {
            work
            for runtime in self._runtimes()
            if (work := runtime.work_in_flight(topic_id, agent_handle)) is not None
        }
        return works.pop() if len(works) == 1 else None

    def draining(self, topic_id: uuid.UUID, agent_handle: str) -> bool:
        """Is this seat's runner another release's, finishing the work it was
        doing? A message for the seat waits until it has been replaced."""
        return any(
            runtime.draining(topic_id, agent_handle) for runtime in self._runtimes()
        )

    def tell_draining(self, topic_id: uuid.UUID, agent_handle: str) -> bool:
        """Whether the room is to be told now that a message waits for this
        seat's drain: once per drain."""
        return any(
            runtime.tell_draining(topic_id, agent_handle)
            for runtime in self._runtimes()
        )

    def holds(self, topic_id: uuid.UUID, agent_handle: str | None = None) -> bool:
        """Does any backend still hold a live session for this topic — for
        this agent's seat in it, when one is named?"""
        return any(
            runtime.holds(topic_id, agent_handle) for runtime in self._runtimes()
        )

    async def recover_sessions(
        self, device_id: str | None = None
    ) -> list["SessionRef"]:
        """Listen again to sessions that survived this process."""
        recovered: list[SessionRef] = []
        for runtime in self._runtimes():
            sessions = await runtime.recover(device_id)
            for session in sessions:
                self._owners[(session.topic_id, session.agent_handle)] = runtime
            recovered.extend(sessions)
        return recovered

    async def stop_listening(self) -> None:
        """Stop reading every session, in every harness."""
        for runtime in self._runtimes():
            await runtime.stop_listening()
        self._owners.clear()

    async def replay(self, session: "SessionRef") -> None:
        """Land what a recovered session produced while nobody listened."""
        for runtime in self._runtimes():
            await runtime.replay(session)

    def platform_work(self) -> "RoomSessions":
        """The backend for work the PLATFORM starts — the memory
        consolidation (dream).

        No agent type stands behind it, so there is no harness to honour and
        nothing to refuse: it runs on whatever the machine runs. Never None,
        unlike ``select`` — a caller with no type to satisfy always has an
        answer, and falling back to the default machine is a better one than
        crashing on a wiring gap.
        """
        return self.default()

    def owned(self, harness: str, *, on_account: bool) -> "RoomSessions | None":
        """The backend for a member's own agent of ``harness``, never the
        room's choice: on the owner's machine, or, ``on_account``, on the
        session host with the connection its owner's account holds."""
        return self._owned.get((harness, on_account))

    def choose(
        self, project_settings: Mapping[str, Any] | None
    ) -> "tuple[str, RoomSessions | None]":
        """The harness a room's turn runs, and its backend.

        ``harness_on`` walks the project's order over what this deployment
        runs. When none of it runs here, the answer is the project's first pick
        with no backend: the turn says that one is not deployed.
        """

        chosen = harness_on(
            project_settings, lambda name: self.select(harness=name) is not None
        )
        if chosen is None:
            return harness_for(project_settings), None
        return chosen, self.select(harness=chosen)

    def select(self, *, harness: str | None = None) -> "RoomSessions | None":
        """The backend for ``harness``: the one already chosen for this turn
        (``harness_on`` walks the project's order over this pool). It does NOT
        fall back: a name this deployment does not run gets None. None asks for
        the deployment's first preference."""

        return self._backends.get(harness_name(harness))


def build_compute_pool(host: "SessionHost | None" = None) -> ComputePool:
    """Build the ComputePool from settings: one backend per harness that can
    forward its tools, over the one executor channel, plus a member's own
    Claude Code where its model connection is (#2991)."""
    # deferred-import: breaks the cycle agent.central_provider -> agent.compute
    from app.domain.agent.central_provider import CentralChannel

    # deferred-import: breaks the cycle agent.device_provider -> agent.compute
    from app.domain.agent.device_provider import DeviceChannel

    # deferred-import: breaks the cycle agent.owner_provider -> agent.compute
    from app.domain.agent.owner_provider import AccountChannel, OwnerChannel

    # deferred-import: breaks the cycle room.sessions -> agent.compute
    from app.domain.agent.room.sessions import RoomSessions

    # deferred-import: breaks the cycle session_host.host -> agent.compute
    from app.domain.agent.session_host.host import SessionHost

    # The room ends stuck work on its own clocks only for a harness whose
    # runner does not (``Harness.reports_lifecycle``): one that reports in the
    # lifecycle protocol is handed the same two limits when it is registered
    # (`session_host.starting.register`) and ends it there.
    def policy(name: str) -> dict:
        own = not reports_lifecycle(name)
        return {
            "hard_ceiling_s": settings.agent_turn_hard_ceiling_s,
            "no_progress_s": settings.agent_no_progress_s if own else 0.0,
            "unread_grace_s": settings.agent_unread_grace_s if own else 0.0,
        }

    # 一个骨架挂不挂得上一条通道，看它能不能把工具送到那条通道的手上。被
    # `CentralChannel` 包起来的，会话在中心机、手在执行机，所以只挂声明了
    # `Capability.REMOTE_EXECUTION` 的骨架。房间的一轮按这张池子挑骨架
    # （`harness_on`），所以「这个场景要远端执行」的判据就落在这里。
    def forwards(name: str) -> bool:
        return name in HARNESSES and (
            Capability.REMOTE_EXECUTION in HARNESSES[name].capabilities
        )

    # 挂谁，由注册表说（结论 43）。一个骨架没注册就不在 `HARNESSES` 里，而「不在
    # 注册表里」如果只是矩阵上少一列，它照样是个活调用点：
    # `recover_sessions` 进程重启后会把它的旧会话恢复回来并写进 `_owners`，
    # `report_to` 照样把房间的耳朵交给它，`steer` 在没有 owner 的时候照样按
    # `holds()` 找到它。所以判据落在装配这一步：注册表是唯一的那一处，什么时候
    # 答得出四条、什么时候写回 `HARNESSES`，这里不用跟着改。
    host = host or SessionHost()
    backends: list[RoomSessions] = [
        RoomSessions(CentralChannel(DeviceChannel()), harness, host, **policy(harness))
        for harness in HARNESSES
        if forwards(harness)
    ]
    # A member's own Claude Code (#2991): its session on the owner's machine
    # holding its login, or on the session host with a connection its owner's
    # account holds. Its hands are its environment's either way, reached over
    # the device transport.
    owned = {
        (CLAUDE_CODE, False): RoomSessions(
            OwnerChannel(DeviceChannel()), CLAUDE_CODE, host, **policy(CLAUDE_CODE)
        ),
        (CLAUDE_CODE, True): RoomSessions(
            AccountChannel(DeviceChannel()), CLAUDE_CODE, host, **policy(CLAUDE_CODE)
        ),
    }
    return ComputePool(backends, owned)
