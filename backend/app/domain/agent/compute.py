"""ComputePool: the compute side of the two-pool model (design v2 R2 / v3).

Symmetric to AIPool (profiles.py). A ComputeProvider answers WHERE a turn runs:
it builds the machine (container + worktree + session + cheese env) and
checkpoints the workspace afterwards. What runs there is an ``AgentRuntime``.

Every provider in the pool keeps a live session. There used to be a second
shape — start a subprocess, stream what it says, exit — and every piece of
salvage machinery in the platform came from its one property: whoever held the
iterator owned the turn, so the turn died when that process did. The rooms it
ran are gone; what remains is the shape that can be reconnected to.
"""

import contextlib
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from app.core.config import settings
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import (
    CLAUDE_CODE,
    CODEX,
    HARNESSES,
    PI,
    AgentRuntime,
    Capability,
    runtime_for,
)

if TYPE_CHECKING:
    from app.domain.agent.device_provider import DeviceChannel
    from app.domain.agent.harness import (
        InputRegistrar,
        MemoryConsumer,
        RoomReader,
        SessionControls,
        SessionRef,
        UnreadProbe,
    )


class ComputeProvider(Protocol):
    """Where a turn runs: a machine with a workspace on it.

    NOT how a turn runs — that is an ``AgentRuntime``. Which machine and what
    runs on it were one switch for as long as the only harness we drive was also
    the only thing that knew how to reach its own machine; separating the
    questions is what lets a second harness run on the machines the first one
    uses.

    What the pool holds is a runtime WRAPPING a channel, and the runtime answers
    this protocol by forwarding to the channel it is driving. So the two halves
    are separate objects now, not just separate contracts. That forwarding is
    why the three facts below are read-only: a backend is ASKED which machine it
    is and what reaches it, and the answer comes from somewhere else.
    """

    @property
    def name(self) -> str: ...

    # 图片输入: whether the turn's user message actually carries `images=`. It is
    # a capability, not a preference — the prompt wording branches on it
    # (chat.prompt_line). Before this existed, `images=` was accepted by every
    # provider and silently dropped by some, while the prompt kept telling 芝士
    # "图片内容已附在本条消息里" on all of them. An agent that reads that promise
    # and sees nothing does not error — it invents what the image said, which is
    # worse than saying "我没收到图". A backend that drops images MUST say False
    # here rather than leave the prompt lying for it.
    @property
    def embeds_images(self) -> bool: ...

    # Does a turn here have to wait for a machine to be created first? The turn
    # path branches on it — 「机器正在创建」 with the prompt held — instead of on
    # the backend's class, which is what lets a second leased-machine backend
    # get the same waiting room without the platform learning its name.
    @property
    def provisions_machine(self) -> bool: ...

    @property
    def deferred_work(self) -> bool: ...

    # Does this backend assemble its machine's model environment itself? The
    # platform then sends the model CHOICE and nothing else, and the turn's
    # supply route is the deployment's rather than the profile's. Asked instead
    # of the backend's NAME because the answer is a property of the transport,
    # and a name is only ever the list of transports that had it on the day it
    # was written — see ``Channel.builds_model_env``.
    @property
    def builds_model_env(self) -> bool: ...

    def available(self) -> bool: ...

    async def prepare_topic(
        self, *, project_id: uuid.UUID, topic_id: uuid.UUID, actor: object | None
    ) -> tuple[bool, str]:
        """Get the machine ready, and say whether it is. Only asked of a backend
        that declares ``provisions_machine``; everyone else's machine is already
        there."""
        ...

    async def deliver(
        self,
        topic_id: uuid.UUID,
        text: str,
        images: list[dict] | None = None,
        *,
        register_input: "InputRegistrar",
        expected_work_id: uuid.UUID | None = None,
        agent_handle: str | None = None,
        owes_reply: bool = False,
    ) -> bool:
        """Inject text into the session already running on this topic, if this
        backend has one. False = "nothing live here" — the caller queues instead.

        A RUNTIME operation, declared here too because what the pool holds is a
        runtime wrapping a channel and the hot path asks the pool. Spelled out
        rather than duck-typed so a backend that cannot take an injection has to
        say so, which is what stops the pool from silently skipping one that
        could.

        ``agent_handle`` names the seat the words belong to; a room seats one
        session per agent, and without it only an unambiguous room may be
        delivered to.
        """
        ...


class ComputePool:
    """Which backend a turn lands on — a machine AND a harness.

    Two axes, because they are two questions. WHICH MACHINE is the room's
    compute choice (``topic.compute_config``): someone's enrolled laptop, a
    leased Cloud box. WHAT RUNS THERE is the agent type's ``harness``. They were
    one key for as long as one harness existed, and a registry keyed by machine
    alone cannot hold a second one — two runtimes over the same transport would
    collide on the same name.

    Caps-matching + project quota + overflow queue land when there is more than
    one backend per pair; today the pair is looked up directly.
    """

    def __init__(self, backends: list[ComputeProvider], default_name: str):
        from app.domain.agent.harness import deployment_harnesses

        # Every backend runs a harness. Checked HERE, once, at wiring time: the
        # turn path then reads `runtime_for` as an answer rather than as a
        # question, and a backend that forgot half the contract is a startup
        # failure instead of a turn that silently does nothing.
        self._backends = {
            (backend.name, runtime_for(backend).harness): backend
            for backend in backends
        }
        # The same objects again, typed as what they are to every call below.
        # Resolved once because `runtime_for` is an `isinstance` against a
        # runtime-checkable Protocol of some twenty members, and `holds` runs
        # it per backend per call — once per row of a board, which made it a
        # quarter of a second of pure CPU on the event loop.
        self._harnesses = [runtime_for(backend) for backend in self._backends.values()]
        # 部署列的骨架，在装配时解析一次：一个配错名字的部署在这里就起不来，而
        # 不是等到某一轮才发现自己跑的是另一个东西（结论 28）。偏好的第一个得挂在
        # 默认机器上：没说骨架的平台工作落在这一对上。
        self._default = (default_name, deployment_harnesses()[0])
        if self._default not in self._backends:
            raise ValueError(f"default backend {self._default!r} not registered")
        # 归属按座位记（topic, agent_handle）：一间房坐着几个 agent，各有各的
        # 会话和它的属主 runtime。按房间记的那个版本里，B 的轮次一 activate 就
        # 把 A 的会话 interrupt+close 掉——多 agent 同房间在这一层就不可能。
        self._owners: dict[tuple[uuid.UUID, str], AgentRuntime] = {}

    async def activate(self, session: "SessionRef", runtime: AgentRuntime) -> None:
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

    def default(self) -> ComputeProvider:
        return self._backends[self._default]

    def _runtimes(self) -> list[AgentRuntime]:
        """Every harness in the pool.

        The pool is keyed by machine and holds objects that answer both
        questions; the calls below are addressed to the harness half. This is
        where the guarantee bought at wiring time — every backend runs one — is
        spent, so the callers read as statements rather than as questions.
        """
        return self._harnesses

    def machines(self) -> set[str]:
        """Which machine pools this deployment offers, whatever runs on them."""
        return {name for name, _ in self._backends}

    async def deliver(
        self,
        topic_id: uuid.UUID,
        text: str,
        images: list[dict] | None = None,
        *,
        register_input: "InputRegistrar",
        expected_work_id: uuid.UUID | None = None,
        agent_handle: str | None = None,
        owes_reply: bool = False,
    ) -> bool:
        """Deliver to the owner selected when starting or recovering the work.

        ``agent_handle`` aims the delivery at one seat. Without it the seats
        of the room are tried one by one and the runtime answers only for the
        seat whose open work matches — a seat that is not working, or whose
        work is not the expected one, says False and the next seat is asked.
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
            delivered = await backend.deliver(
                topic_id,
                text,
                images=images,
                register_input=register_input,
                expected_work_id=expected_work_id,
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
        for backend in self._backends.values():
            recover = getattr(backend, "recover_native_tools", None)
            if recover is None:
                continue
            runtime = runtime_for(backend)
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

    def bind_reader(self, reader: "RoomReader") -> None:
        """Give every runtime the room's ear: what its sessions say and do,
        one item at a time (``RoomReader``)."""
        for runtime in self._runtimes():
            runtime.bind_reader(reader)

    def bind_unread_probe(self, probe: "UnreadProbe") -> None:
        """Give every runtime a way to ask whether anything it was handed is
        still unread — the other half of the same bookkeeping."""
        for runtime in self._runtimes():
            runtime.bind_unread_probe(probe)

    def bind_memory(self, consumer: "MemoryConsumer") -> None:
        """Give every runtime the owner of 「记忆该对账了」.

        Every runtime, not only the ones that keep memory files: a runtime that
        does not answers `memory` with None, and the callback is asked on a
        moment (an input going in, a turn ending) that every harness has.
        """
        for runtime in self._runtimes():
            runtime.bind_memory(consumer)

    async def memory(self, topic_id: uuid.UUID, request: dict) -> dict | None:
        """Relay a memory reconciliation to whichever runtime owns this room.

        ``None`` means «这个房间现在没有能对账的会话» — no live session, or a
        harness whose sessions keep no memory files. Both are ordinary answers,
        not failures.
        """
        candidates = [
            runtime for (topic, _), runtime in self._owners.items() if topic == topic_id
        ] or [runtime for runtime in self._runtimes() if runtime.holds(topic_id)]
        for runtime in candidates:
            answer = await runtime.memory(topic_id, request)
            if answer is not None:
                return answer
        return None

    def session_controls(self, topic_id: uuid.UUID) -> "SessionControls | None":
        """The runtime whose live session in this room takes controls, if any."""
        from app.domain.agent.harness import SessionControls

        candidates = [
            runtime for (topic, _), runtime in self._owners.items() if topic == topic_id
        ] or self._runtimes()
        for runtime in candidates:
            if isinstance(runtime, SessionControls) and runtime.holds(topic_id):
                return runtime
        return None

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

    async def ask_origin(self, project_id, topic_id, agent_handle):
        """Read exactly one owning runtime; ambiguity never chooses a seat."""
        candidates = [
            runtime
            for runtime in self._runtimes()
            if runtime.holds(topic_id, agent_handle)
        ]
        if len(candidates) != 1:
            return None
        reader = getattr(candidates[0], "ask_origin", None)
        return await reader(project_id, topic_id, agent_handle) if reader else None

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

    async def replay(self, session: "SessionRef", *, known_texts: set[str]) -> None:
        """Land what a recovered session produced while nobody listened."""
        for runtime in self._runtimes():
            await runtime.replay(session, known_texts=known_texts)

    def platform_work(self, provider_id: str | None = None) -> ComputeProvider:
        """The backend for work the PLATFORM starts — the memory
        consolidation (dream).

        No agent type stands behind it, so there is no harness to honour and
        nothing to refuse: it runs on whatever the machine runs. Never None,
        unlike ``select`` — a caller with no type to satisfy always has an
        answer, and falling back to the default machine is a better one than
        crashing on a wiring gap.
        """
        return self.select(provider_id=provider_id) or self.default()

    def has(self, provider_id: str) -> bool:
        return provider_id in self.machines()

    def choose(
        self, project_settings: Mapping[str, Any] | None, provider_id: str | None
    ) -> tuple[str, ComputeProvider | None]:
        """The harness a room's turn runs on this machine, and its backend.

        ``harness_on`` walks the project's order over what this machine runs.
        When none of it runs here, the answer is the project's first pick with
        no backend: the turn says that one is not deployed on this machine.
        """
        from app.domain.agent.harness import harness_for, harness_on

        chosen = harness_on(
            project_settings,
            lambda name: self.select(provider_id=provider_id, harness=name) is not None,
        )
        if chosen is None:
            return harness_for(project_settings), None
        return chosen, self.select(provider_id=provider_id, harness=chosen)

    def select(
        self,
        *,
        provider_id: str | None = None,
        harness: str | None = None,
        env_spec: dict | None = None,
    ) -> ComputeProvider | None:
        """Pick the backend for this turn (execution-architecture v4 会话级选择).

        ``provider_id`` is the machine a topic/project chose (resolved upstream
        from ``topic.compute_config`` → project default); a machine this
        deployment does not have falls back to the default one, so a stored
        selection that was retired never breaks a turn.

        ``harness`` is the one already chosen for this turn (``harness_on``
        walks the project's order over this pool), and it does NOT fall back
        here: a name this machine does not run gets None. None asks for the
        deployment's first preference.

        caps/quota/queue routing arrives with ``env_spec`` (design §3
        pick_provider, v2 R9)."""
        from app.domain.agent.harness import harness_name

        machine = provider_id if provider_id in self.machines() else self._default[0]
        return self._backends.get((machine, harness_name(harness)))


def build_compute_pool(cloud_channel: "DeviceChannel | None" = None) -> ComputePool:
    """Build the ComputePool from settings.

    Every machine here belongs to someone a person can name: the device
    transport (an enrolled machine of theirs) always joins, and Cloud joins when
    the deployment can provision one. Both are pools the 市场 catalogue lists, so
    the pool and the menu hold the same machines — a turn can only land on
    something that was on offer.

    There used to be a third, a container on the platform's own host, wired in
    unconditionally and made the pool's default. Nothing ever offered it (#358
    retired it from the catalogue), which is precisely why it kept running work:
    a machine nobody can choose is still where everything goes if it is what the
    executor falls back to. The default now comes from `compute_default_name`,
    the same answer the catalogue marks 默认.
    """
    from app.domain.agent.central_provider import CentralChannel
    from app.domain.agent.device_provider import DeviceChannel
    from app.domain.agent.harness.claude_code import (
        ClaudeCodeChannel,
        ClaudeCodeRuntime,
        executor_launch,
    )
    from app.domain.agent.harness.codex import CodexChannel, CodexRuntime
    from app.domain.agent.harness.pi.channel import PiChannel
    from app.domain.agent.harness.pi.runtime import PiRuntime
    from app.domain.agent.market import compute_default_name

    # One liveness policy for every harness (``docs/agent-liveness.md``): the
    # driven runtime ends a turn on its evidence, whichever runner produced it.
    policy = {
        "hard_ceiling_s": settings.agent_turn_hard_ceiling_s,
        "no_progress_s": settings.agent_no_progress_s,
        "unread_grace_s": settings.agent_unread_grace_s,
    }

    # 进这张表的每一条通道，下面都要被 `CentralChannel` 包一次，而这个包装读的是
    # 设备传输自己的 `_hub` 与 `_session_factory`。所以 `DeviceChannel` 在这里不是
    # 一条判断，是那个包装本来就要的东西写出来：原来标成 `Channel` 的那个签名兑现
    # 不了——真递一条别的 `Channel` 进来，`CentralChannel(c)` 当场 AttributeError。
    channels: list[DeviceChannel] = [DeviceChannel()]
    if cloud_channel is not None:
        channels.append(cloud_channel)
    # The default has to name a machine THIS pool actually holds — the pool
    # refuses one that does not, and a deployment that cannot start is worse
    # than one whose fallback is the plainer machine. `compute_default_name` is
    # the preference and the row the catalogue marks 默认; the device pool is
    # what every deployment has.
    preferred = compute_default_name(settings)
    names = {channel.name for channel in channels}
    default_name = preferred if preferred in names else DeviceChannel.name

    # 一个骨架挂不挂得上一条通道，看它能不能把工具送到那条通道的手上。被
    # `CentralChannel` 包起来的，会话在中心机、手在执行机，所以只挂声明了
    # `Capability.REMOTE_EXECUTION` 的骨架。房间的一轮按这张池子挑骨架
    # （`harness_on`），所以「这个场景要远端执行」的判据就落在这里。
    def forwards(name: str) -> bool:
        return name in HARNESSES and (
            Capability.REMOTE_EXECUTION in HARNESSES[name].capabilities
        )

    backends: list[ComputeProvider] = []
    if forwards(CLAUDE_CODE):
        backends.extend(
            ClaudeCodeRuntime(ClaudeCodeChannel(CentralChannel(c)), **policy)
            for c in channels
        )
    # 挂谁，由注册表说（结论 43）。一个骨架答不出四条硬性要求就不在 `HARNESSES`
    # 里，而「不在注册表里」如果只是矩阵上少一列，它照样是个活调用点：
    # `recover_sessions` 进程重启后会把它的旧会话恢复回来并写进 `_owners`，
    # `bind_reader` 照样把房间的耳朵交给它，`deliver` 在没有 owner 的时候照样
    # 按 `holds()` 找到它。所以判据落在装配这一步：注册表是唯一的那一处，什么时候
    # 答得出四条、什么时候写回 `HARNESSES`，这里不用跟着改。
    if forwards(CODEX):
        backends.extend(
            CodexRuntime(CodexChannel(CentralChannel(c), executor_launch), **policy)
            for c in channels
        )
    if forwards(PI):
        backends.extend(
            PiRuntime(PiChannel(CentralChannel(c), executor_launch), **policy)
            for c in channels
        )
    return ComputePool(backends, default_name)
