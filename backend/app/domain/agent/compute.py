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

if TYPE_CHECKING:
    from app.domain.agent.harness import (
        ActivityConsumer,
        Backlog,
        EventConsumer,
        ReceiptConsumer,
        SessionRef,
    )
    from app.domain.agent.harness.claude_code import Channel


class ComputeProvider(Protocol):
    """Where a turn runs: a machine with a workspace on it, and a way to
    snapshot that workspace afterwards.

    NOT how a turn runs — that is an ``AgentRuntime``. Which machine and what
    runs on it were one switch for as long as the only harness we drive was also
    the only thing that knew how to reach its own machine; separating the
    questions is what lets a second harness run on the machines the first one
    uses.

    What the pool holds is a runtime WRAPPING a channel, and the runtime answers
    this protocol by forwarding to the channel it is driving. So the two halves
    are separate objects now, not just separate contracts.
    """

    name: str

    # 图片输入: whether the turn's user message actually carries `images=`. It is
    # a capability, not a preference — the prompt wording branches on it
    # (chat._prompt_line). Before this existed, `images=` was accepted by every
    # provider and silently dropped by some, while the prompt kept telling 芝士
    # "图片内容已附在本条消息里" on all of them. An agent that reads that promise
    # and sees nothing does not error — it invents what the image said, which is
    # worse than saying "我没收到图". A backend that drops images MUST say False
    # here rather than leave the prompt lying for it.
    embeds_images: bool

    # Does a turn here have to wait for a machine to be created first? The turn
    # path branches on it — 「机器正在创建」 with the prompt held — instead of on
    # the backend's class, which is what lets a second leased-machine backend
    # get the same waiting room without the platform learning its name.
    provisions_machine: bool

    def available(self) -> bool: ...

    async def prepare_topic(
        self, *, project_id: uuid.UUID, topic_id: uuid.UUID, actor: object | None
    ) -> tuple[bool, str]:
        """Get the machine ready, and say whether it is. Only asked of a backend
        that declares ``provisions_machine``; everyone else's machine is already
        there."""
        ...

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
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

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None: ...


class ComputePool:
    """Which backend a turn lands on — a machine AND a harness.

    Two axes, because they are two questions. WHICH MACHINE is the topic's
    ``compute_profile``: a container on this host, someone's enrolled laptop, a
    leased Cloud box. WHAT RUNS THERE is the agent type's ``harness``. They were
    one key for as long as one harness existed, and a registry keyed by machine
    alone cannot hold a second one — two runtimes over the same transport would
    collide on the same name.

    Caps-matching + project quota + overflow queue land when there is more than
    one backend per pair; today the pair is looked up directly.
    """

    def __init__(self, backends: list[ComputeProvider], default_name: str):
        from app.domain.agent.harness import DEFAULT_HARNESS, runtime_for

        # Every backend runs a harness. Checked HERE, once, at wiring time: the
        # turn path then reads `runtime_for` as an answer rather than as a
        # question, and a backend that forgot half the contract is a startup
        # failure instead of a turn that silently does nothing.
        self._backends = {
            (backend.name, runtime_for(backend).harness): backend
            for backend in backends
        }
        self._default = (default_name, DEFAULT_HARNESS)
        if self._default not in self._backends:
            raise ValueError(f"default backend {self._default!r} not registered")

    def default(self) -> ComputeProvider:
        return self._backends[self._default]

    def machines(self) -> set[str]:
        """Which machine pools this deployment offers, whatever runs on them."""
        return {name for name, _ in self._backends}

    def tmux_activity_status(self, topic_id: uuid.UUID) -> dict | None:
        """turn 活跃度检测: `cheese status`'s idle-suspect signal, read from
        whichever tmux backend is in this pool. None when there is no tmux
        backend, or no turn currently monitored for this topic (not running, or
        running on a different machine)."""
        from app.domain.agent.tmux_provider import TmuxChannel

        for backend in self._backends.values():
            channel = getattr(backend, "channel", None)
            if isinstance(channel, TmuxChannel):
                return channel.activity_status(topic_id)
        return None

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """Inject text into whichever session is currently running on this
        topic. Asks every backend rather than resolving the topic's configured
        one: only one that HAS a live session for this exact topic can answer
        True, so the first True is the right one — and it needs no DB read on
        the hot path where a human is waiting.

        Every backend is asked rather than only the ones that keep a session:
        answering False is cheap, and a pool that decided in advance who COULD
        answer would be deciding it from the class rather than from whether
        there is a live session — which is the thing actually being asked."""
        for backend in self._backends.values():
            delivered = (
                await backend.deliver(topic_id, text, images=images)
                if images
                else await backend.deliver(topic_id, text)
            )
            if delivered:
                return True
        return False

    def bind_events(
        self,
        consumer: "EventConsumer",
        activity: "ActivityConsumer | None" = None,
    ) -> None:
        """Give every runtime the room-side persistence and activity owners."""
        for backend in self._backends.values():
            backend.bind_events(consumer)
            if activity is not None:
                backend.bind_activity(activity)

    def bind_receipts(self, consumer: "ReceiptConsumer") -> None:
        """Give every runtime the owner of prompt receipts — the consumed-stamp
        side of #539 decision A."""
        for backend in self._backends.values():
            backend.bind_receipts(consumer)

    def holds(self, topic_id: uuid.UUID) -> bool:
        """Does any backend still hold a live session for this topic?"""
        return any(backend.holds(topic_id) for backend in self._backends.values())

    async def recover_sessions(
        self, device_id: str | None = None
    ) -> list["SessionRef"]:
        """Listen again to sessions that survived this process."""
        recovered: list[SessionRef] = []
        for backend in self._backends.values():
            recovered.extend(await backend.recover(device_id))
        return recovered

    def backlog(self, session: "SessionRef") -> "Backlog":
        """The unread tail for this session, from whichever backend kept it.

        A session's records live with the HARNESS that made them, and this call
        does not know which one ran the topic — resolving that means a DB read
        the reconcile path does not have in hand. Asking is cheap and
        unambiguous instead: at most one harness has anything to hand over for a
        given session, so the first non-empty answer is the right one. When
        nobody has anything the reader is empty either way, and the caller still
        gets one to close the pass with.
        """
        readers = [backend.backlog(session) for backend in self._backends.values()]
        for reader in readers:
            if reader.unread():
                return reader
        return readers[0]

    async def replay(self, session: "SessionRef", *, known_texts: set[str]) -> None:
        """Land what a recovered session produced while nobody listened."""
        for backend in self._backends.values():
            await backend.replay(session, known_texts=known_texts)

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


def build_compute_pool(cloud_channel: "Channel | None" = None) -> ComputePool:
    """Build the ComputePool from settings.

    The local transport (a container on this box) always joins, the device
    transport always joins, and Cloud joins when it is configured. A topic picks
    between them per turn, with the first turn pinning the choice; the device
    and Cloud pools are only OFFERED when they can actually run something, which
    is what keeps them opt-in without a deployment switch.

    ``agent_backend`` used to choose between this and an SDK subprocess. There
    is nothing to choose between now.
    """
    from app.domain.agent.device_provider import DeviceChannel
    from app.domain.agent.harness.claude_code import Channel, ClaudeCodeRuntime
    from app.domain.agent.tmux_provider import TmuxChannel

    def runs_claude_code(channel: Channel) -> ClaudeCodeRuntime:
        # One timeout policy, applied where the watching happens. The two-layer
        # shape (turn 活跃度检测) is `idle_suspect_s` of no hook and no liveness
        # evidence → only SUSPECTED wedged, then a `confirm_alive` probe until it
        # says dead, with `hard_ceiling_s` as the unconditional backstop. It used
        # to be a constructor argument on every transport, which is how a single
        # 900s deadline could kill a long-but-silent turn on one of them and not
        # the others.
        return ClaudeCodeRuntime(
            channel,
            idle_suspect_s=settings.agent_idle_suspect_s,
            hard_ceiling_s=settings.agent_turn_hard_ceiling_s,
        )

    channels: list[Channel] = [
        TmuxChannel(image=settings.tmux_sandbox_image),
        DeviceChannel(),
    ]
    if cloud_channel is not None:
        channels.append(cloud_channel)
    default_name = (
        DeviceChannel.name if settings.agent_backend == "device" else TmuxChannel.name
    )
    return ComputePool([runs_claude_code(c) for c in channels], default_name)


def app_preview_reachable(compute_profile: str | None) -> bool:
    """Can 运行环境预览 exist for a topic running on this compute at all?

    The feature resolves the app port a *docker container on the backend's own
    host* publishes (``workspace.app_endpoint`` → ``docker port``). That mapping
    exists only when the topic's box IS a container here. A turn running on
    someone's enrolled machine (``device``) or on a leased Cloud machine
    (``cloud`` — a DeviceChannel subclass) has no container on this host, so the
    lookup returns None for a reason that has nothing to do with the app: there
    is no path from the platform to that port, and there never was.

    Without this distinction both cases collapse into "container down", and the
    panel tells those users to @ 芝士 again to bring up a box that is not coming.
    """
    from app.domain.agent.market import compute_default_name
    from app.domain.agent.tmux_provider import TmuxChannel

    local_box = {TmuxChannel.name}
    # A topic that has an app artifact has necessarily run a turn, and the first
    # turn pins `topic.compute_profile` — so the sticky project/team chain is
    # already collapsed into it and only the deployment default is left to apply.
    return (compute_profile or compute_default_name()) in local_box
