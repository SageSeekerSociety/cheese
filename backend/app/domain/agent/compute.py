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
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from app.core.config import settings

if TYPE_CHECKING:
    from app.domain.agent.harness.claude_code import (
        Channel,
        HookActivityConsumer,
        HookEventConsumer,
        TopicSubscription,
    )


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

        A RUNTIME operation (it is on ``AgentRuntime`` too) that still hangs off
        the provider, because the pool holds providers and every provider today
        is its own runtime. It moves when that stops being true. Declared here
        rather than duck-typed so a backend that cannot take an injection has to
        say so, which is what stops the pool from silently skipping one that
        could.
        """
        ...

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None: ...


class ComputePool:
    """A pool of compute providers + per-turn selection (design §3 / v3).

    Mirror image of AIPool (profiles.ProfileRegistry): a registry with a default
    that is always available. Caps-matching + project quota + overflow queue land
    when there is more than one provider; today the default is returned directly.
    """

    def __init__(self, providers: list[ComputeProvider], default_name: str):
        from app.domain.agent.harness import runtime_for

        self._providers = {p.name: p for p in providers}
        if default_name not in self._providers:
            raise ValueError(f"default provider {default_name!r} not registered")
        # Every provider runs a harness. Checked HERE, once, at wiring time:
        # the turn path then reads `runtime_for` as an answer rather than as a
        # question, and a backend that forgot half the contract is a startup
        # failure instead of a turn that silently does nothing.
        for provider in providers:
            runtime_for(provider)
        self._default_name = default_name

    def default(self) -> ComputeProvider:
        return self._providers[self._default_name]

    def tmux_activity_status(self, topic_id: uuid.UUID) -> dict | None:
        """turn 活跃度检测: `cheese status`'s idle-suspect signal, read from
        whichever tmux provider is in this pool (at most one — see
        `build_compute_pool`). None when there's no tmux provider in the pool,
        or no turn currently monitored for this topic (not running, or running
        on a different backend)."""
        from app.domain.agent.tmux_provider import TmuxChannel

        for provider in self._providers.values():
            channel = getattr(provider, "channel", None)
            if isinstance(channel, TmuxChannel):
                return channel.activity_status(topic_id)
        return None

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """Inject text into whichever session is currently running on this
        topic. Asks every runtime rather than resolving the topic's configured
        one: only one that HAS a live screen for this exact topic can answer
        True, so the first True is the right one — and it needs no DB read on
        the hot path where a human is waiting.

        Every provider is asked rather than only the ones that keep a session:
        answering False is cheap, and a pool that decided in advance who COULD
        answer would be deciding it from the class rather than from whether
        there is a live screen — which is the thing actually being asked."""
        for provider in self._providers.values():
            delivered = (
                await provider.deliver(topic_id, text, images=images)
                if images
                else await provider.deliver(topic_id, text)
            )
            if delivered:
                return True
        return False

    def bind_hook_event_consumer(
        self,
        consumer: "HookEventConsumer",
        activity_consumer: "HookActivityConsumer | None" = None,
    ) -> None:
        """Give hooks providers the room-side persistence and activity owners."""
        from app.domain.agent.harness.claude_code import ClaudeCodeRuntime

        for provider in self._providers.values():
            if isinstance(provider, ClaudeCodeRuntime):
                provider.bind_event_consumer(consumer)
                if activity_consumer is not None:
                    provider.bind_activity_consumer(activity_consumer)

    def bind_prompt_receipt_consumer(
        self, consumer: Callable[[uuid.UUID, str], Awaitable[None]]
    ) -> None:
        """Give hooks providers the owner of UserPromptSubmit receipts — the
        consumed-stamp side of #539 decision A."""
        from app.domain.agent.harness.claude_code import ClaudeCodeRuntime

        for provider in self._providers.values():
            if isinstance(provider, ClaudeCodeRuntime):
                provider.bind_receipt_consumer(consumer)

    def has_live_screen(self, topic_id: uuid.UUID) -> bool:
        """Does any provider in this pool still hold a screen for this topic?
        See `ClaudeCodeRuntime.has_live_screen`."""
        from app.domain.agent.harness.claude_code import ClaudeCodeRuntime

        return any(
            provider.has_live_screen(topic_id)
            for provider in self._providers.values()
            if isinstance(provider, ClaudeCodeRuntime)
        )

    async def recover_hook_subscriptions(
        self, device_id: str | None = None
    ) -> list["TopicSubscription"]:
        """Recover subscriptions for screens that survived this process."""
        from app.domain.agent.harness.claude_code import ClaudeCodeRuntime

        recovered: list[TopicSubscription] = []
        for provider in self._providers.values():
            if isinstance(provider, ClaudeCodeRuntime):
                recovered.extend(await provider.recover_subscriptions(device_id))
        return recovered

    def has(self, provider_id: str) -> bool:
        return provider_id in self._providers

    def select(
        self, *, provider_id: str | None = None, env_spec: dict | None = None
    ) -> ComputeProvider:
        """Pick a provider for this turn (execution-architecture v4 会话级选择).

        ``provider_id`` is the compute a topic/project chose (resolved upstream from
        ``topic.compute_profile`` → project sticky). A registered id routes the turn
        to that provider; an unknown / None id falls back to the pool default (which
        is always available) — so a stored selection that isn't deployed here never
        breaks a turn. caps/quota/queue routing arrives with ``env_spec`` (design §3
        pick_provider, v2 R9)."""
        if provider_id is not None and provider_id in self._providers:
            return self._providers[provider_id]
        return self.default()


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
