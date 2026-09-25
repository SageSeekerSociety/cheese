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

import uuid
from typing import TYPE_CHECKING, Protocol

from app.core.config import settings
from app.domain.agent.harness import CODEX, HARNESSES, PI, AgentRuntime, runtime_for

if TYPE_CHECKING:
    from app.domain.agent.device_provider import DeviceChannel
    from app.domain.agent.harness import (
        ActivityConsumer,
        EventConsumer,
        ReceiptConsumer,
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
        expected_work_id: uuid.UUID | None = None,
    ) -> bool:
        """Inject text into the session already running on this topic, if this
        backend has one. False = "nothing live here" — the caller queues instead.

        A RUNTIME operation, declared here too because what the pool holds is a
        runtime wrapping a channel and the hot path asks the pool. Spelled out
        rather than duck-typed so a backend that cannot take an injection has to
        say so, which is what stops the pool from silently skipping one that
        could.
        """
        ...


class ComputePool:
    """Which backend a turn lands on — a machine AND a harness.

    Two axes, because they are two questions. WHICH MACHINE is the topic's
    ``compute_profile``: someone's enrolled laptop, a leased Cloud box. WHAT
    RUNS THERE is the agent type's ``harness``. They were
    one key for as long as one harness existed, and a registry keyed by machine
    alone cannot hold a second one — two runtimes over the same transport would
    collide on the same name.

    Caps-matching + project quota + overflow queue land when there is more than
    one backend per pair; today the pair is looked up directly.
    """

    def __init__(self, backends: list[ComputeProvider], default_name: str):
        from app.domain.agent.harness import deployment_harness

        # Every backend runs a harness. Checked HERE, once, at wiring time: the
        # turn path then reads `runtime_for` as an answer rather than as a
        # question, and a backend that forgot half the contract is a startup
        # failure instead of a turn that silently does nothing.
        self._backends = {
            (backend.name, runtime_for(backend).harness): backend
            for backend in backends
        }
        # 部署跑的那个骨架，在装配时解析一次：一个配错名字的部署在这里就起不来，
        # 而不是等到某一轮才发现自己跑的是另一个东西（结论 28）。
        self._default = (default_name, deployment_harness())
        if self._default not in self._backends:
            raise ValueError(f"default backend {self._default!r} not registered")
        self._owners: dict[uuid.UUID, AgentRuntime] = {}

    async def activate(self, session: "SessionRef", runtime: AgentRuntime) -> None:
        """Park other harnesses before giving the room to the selected one."""
        for previous in self._runtimes():
            if previous is not runtime and previous.holds(session.topic_id):
                await previous.interrupt(session)
                await previous.close(session)
        self._owners[session.topic_id] = runtime

    def default(self) -> ComputeProvider:
        return self._backends[self._default]

    def _runtimes(self) -> list[AgentRuntime]:
        """Every harness in the pool.

        The pool is keyed by machine and holds objects that answer both
        questions; the calls below are addressed to the harness half. This is
        where the guarantee bought at wiring time — every backend runs one — is
        spent, so the callers read as statements rather than as questions.
        """
        return [runtime_for(backend) for backend in self._backends.values()]

    def machines(self) -> set[str]:
        """Which machine pools this deployment offers, whatever runs on them."""
        return {name for name, _ in self._backends}

    async def deliver(
        self,
        topic_id: uuid.UUID,
        text: str,
        images: list[dict] | None = None,
        *,
        expected_work_id: uuid.UUID | None = None,
    ) -> bool:
        """Deliver to the owner selected when starting or recovering the work."""
        owner = self._owners.get(topic_id)
        candidates = (
            [owner]
            if owner
            else [runtime for runtime in self._runtimes() if runtime.holds(topic_id)]
        )
        if len(candidates) > 1:
            raise RuntimeError("Room has multiple live harnesses without a work owner")
        for backend in candidates:
            kwargs = {}
            if images:
                kwargs["images"] = images
            if expected_work_id is not None:
                kwargs["expected_work_id"] = expected_work_id
            delivered = await backend.deliver(topic_id, text, **kwargs)
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
        for backend in self._backends.values():
            recover = getattr(backend, "recover_native_tools", None)
            if recover is None:
                continue
            runtime = runtime_for(backend)
            if self._owners.get(topic_id) is runtime or runtime.holds(topic_id):
                return await recover(topic_id, agent_handle)
        return False

    def bind_events(
        self,
        consumer: "EventConsumer",
        activity: "ActivityConsumer | None" = None,
    ) -> None:
        """Give every runtime the room-side persistence and activity owners."""
        for runtime in self._runtimes():
            runtime.bind_events(consumer)
            if activity is not None:
                runtime.bind_activity(activity)

    def bind_receipts(self, consumer: "ReceiptConsumer") -> None:
        """Give every runtime the owner of prompt receipts — the consumed-stamp
        side of #539 decision A."""
        for runtime in self._runtimes():
            runtime.bind_receipts(consumer)

    def bind_unread_probe(self, probe: "UnreadProbe") -> None:
        """Give every runtime a way to ask whether anything it was handed is
        still unread — the other half of the same bookkeeping."""
        for runtime in self._runtimes():
            runtime.bind_unread_probe(probe)

    def session_controls(self, topic_id: uuid.UUID) -> "SessionControls | None":
        """The runtime whose live session in this room takes controls, if any."""
        from app.domain.agent.harness import SessionControls

        owner = self._owners.get(topic_id)
        candidates = [owner] if owner else self._runtimes()
        for runtime in candidates:
            if isinstance(runtime, SessionControls) and runtime.holds(topic_id):
                return runtime
        return None

    def work_in_flight(self, topic_id: uuid.UUID) -> uuid.UUID | None:
        """The work a live session in this room is running, if one is."""
        for runtime in self._runtimes():
            work = getattr(runtime, "work", {}).get(topic_id)
            if work is not None:
                return work
        return None

    def holds(self, topic_id: uuid.UUID) -> bool:
        """Does any backend still hold a live session for this topic?"""
        return any(runtime.holds(topic_id) for runtime in self._runtimes())

    async def recover_sessions(
        self, device_id: str | None = None
    ) -> list["SessionRef"]:
        """Listen again to sessions that survived this process."""
        recovered: list[SessionRef] = []
        for runtime in self._runtimes():
            sessions = await runtime.recover(device_id)
            for session in sessions:
                self._owners[session.topic_id] = runtime
            recovered.extend(sessions)
        return recovered

    async def replay(self, session: "SessionRef", *, known_texts: set[str]) -> None:
        """Land what a recovered session produced while nobody listened."""
        for runtime in self._runtimes():
            await runtime.replay(session, known_texts=known_texts)

    def platform_work(self, provider_id: str | None = None) -> ComputeProvider:
        """The backend for work the PLATFORM starts — the activity digest, the
        heartbeat patrol, the project summary.

        No agent type stands behind these, so there is no harness to honour and
        nothing to refuse: they run on whatever the machine runs. Never None,
        unlike ``select`` — a caller with no type to satisfy always has an
        answer, and falling back to the default machine is a better one than
        crashing on a wiring gap.
        """
        return self.select(provider_id=provider_id) or self.default()

    def has(self, provider_id: str) -> bool:
        return provider_id in self.machines()

    def select(
        self,
        *,
        provider_id: str | None = None,
        harness: str | None = None,
        env_spec: dict | None = None,
    ) -> ComputeProvider | None:
        """Pick the backend for this turn (execution-architecture v4 会话级选择).

        ``provider_id`` is the machine a topic/project chose (resolved upstream
        from ``topic.compute_profile`` → project sticky); a machine this
        deployment does not have falls back to the default one, so a stored
        selection that was retired never breaks a turn.

        ``harness`` is what the agent's TYPE asks to be run by, and it does NOT
        fall back. A type that names a harness this deployment does not run on
        that machine gets None — running something else would answer as an agent
        nobody configured, which is worse than not answering. None asks for the
        deployment's default harness.

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
    from app.domain.agent import place
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

    # 进这张表的每一条通道，下面都要被 `CentralChannel` 包一次、可能再被
    # `PiChannel` 包一次，而这两个包装读的是设备传输自己的 `_hub` 与
    # `_session_factory`。所以 `DeviceChannel` 在这里不是一条判断，是那两个包装本来
    # 就要的东西写出来：原来标成 `Channel` 的那个签名兑现不了——真递一条别的
    # `Channel` 进来，`CentralChannel(c)` 当场 AttributeError。
    #
    # 「这条通道上挂不挂得住 pi」是另一回事，在下面问能力位：那是一个会变的事实。
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
    backends: list[ComputeProvider] = [
        ClaudeCodeRuntime(ClaudeCodeChannel(CentralChannel(c)), **policy)
        for c in channels
    ]
    # 挂谁，由注册表说（结论 43）。一个骨架答不出四条硬性要求就不在 `HARNESSES`
    # 里，而「不在注册表里」如果只是矩阵上少一列，它照样是个活调用点：
    # `recover_sessions` 进程重启后会把它的旧会话恢复回来并写进 `_owners`，
    # `bind_events` 照样把房间侧的持久化交给它，`deliver` 在没有 owner 的时候照样
    # 按 `holds()` 找到它。所以判据落在装配这一步：注册表是唯一的那一处，什么时候
    # 答得出四条、什么时候写回 `HARNESSES`，这里不用跟着改。
    if CODEX in HARNESSES:
        backends.extend(
            CodexRuntime(CodexChannel(CentralChannel(c), executor_launch), **policy)
            for c in channels
        )
    # pi is the one backend NOT wrapped in CentralChannel: it runs on the
    # machine that holds the workspace, so there is no second machine to assign
    # and no executor to route its tools through. See pi/channel.py.
    #
    # 所以这里问的是地点的能力位 `HANDS_HERE`，不是通道的类。按类问过一次：
    # `isinstance(c, DeviceChannel)` 读起来像一条排除规则，而这个池里装得进来的两
    # 条通道都继承 `DeviceChannel`，它恒为真——**今天它排除的是空集**，换成能力位
    # 也不会少挂一个 backend。换的是判据的形状：pi 挂不挂得住，取决于手在不在跑会
    # 话的那台机器上（一个会变的事实），不取决于通道的类（一个不会变的事实）。多
    # 一条手在别处的通道进这个池的那天，它声明 `hands_here = False` 就够，这一行不
    # 用跟着改——`tests/unit/test_compute_pool.py` 的 `Elsewhere` 钉的就是这一句。
    if PI in HARNESSES:
        backends.extend(
            PiRuntime(PiChannel(c), **policy)
            for c in channels
            if place.HANDS_HERE in c.capabilities()
        )
    return ComputePool(backends, default_name)
