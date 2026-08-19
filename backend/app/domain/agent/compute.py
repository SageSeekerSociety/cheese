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
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.core.config import settings
from app.domain.agent.service import (
    AgentEvent,
)

if TYPE_CHECKING:
    from app.domain.agent.harness.claude_code import (
        HookActivityConsumer,
        HookEventConsumer,
        TopicSubscription,
    )

class ComputeProvider(Protocol):
    """Where a turn runs: a machine with a workspace on it, and a way to
    snapshot that workspace afterwards.

    NOT how a turn runs. That is an ``AgentRuntime`` for a backend that keeps a
    session alive, and a ``TurnStream`` for one that starts a process per turn —
    two shapes because the platform genuinely has both, not because either is
    provisional. Which machine and what runs on it were one switch for as long
    as the only harness we drive was also the only thing that knew how to reach
    its own machine; separating the questions here is what lets a second harness
    run on the machines the first one uses.

    The classes still answer both today — a hooks provider provisions AND drives
    Claude Code — so nothing about this shrink moves code. It stops the CONTRACT
    from conflating them, which is what the composition split needs in place
    before it can begin.
    """

    name: str

    # 图片输入: whether THIS provider actually embeds `images=` into the turn's
    # user message. It is a capability, not a preference — the prompt wording
    # branches on it (chat._prompt_line). Before this existed, `images=` was
    # accepted by every provider and silently dropped by the hooks-driven ones,
    # while the prompt kept telling 芝士 "图片内容已附在本条消息里" on all of
    # them. An agent that reads that promise and sees nothing does not error —
    # it invents what the image said, which is worse than saying "我没收到图".
    # Default True keeps the SDK/relay contract; a backend that drops images
    # MUST override it to False rather than leave the prompt lying for it.
    embeds_images: bool

    def available(self) -> bool: ...

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


@runtime_checkable
class TurnStream(Protocol):
    """A backend that starts something, streams what it says, and is done.

    The other shape a turn can have, and the older one: no session to ensure,
    nothing to send into afterwards, no log to read from a cursor. Whoever holds
    the iterator owns the turn, and when that process dies the turn dies with it
    — which is the property ``AgentRuntime`` exists to not have.
    """

    def run_turn(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None,
        prompt: str,
        system_prompt: str,
        resume_session_id: str | None,
        model: str | None = None,
        env: dict[str, str] | None = None,
        memory_scope: str | None = None,
        owner: str | None = None,
        turn_id: uuid.UUID | None = None,
        sandbox_image: str | None = None,
        images: list[dict] | None = None,
    ) -> AsyncIterator[AgentEvent]: ...


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

    @classmethod
    def tmux(
        cls, *, image: str, idle_suspect_s: float, hard_ceiling_s: float
    ) -> "ComputePool":
        """Interactive/tmux backend (AGENT_BACKEND=tmux): drives `claude` in a
        tmux session and streams events from Claude Code HTTP hooks."""
        from app.domain.agent.tmux_provider import TmuxHooksProvider

        provider = TmuxHooksProvider(
            image=image, idle_suspect_s=idle_suspect_s, hard_ceiling_s=hard_ceiling_s
        )
        return cls([provider], provider.name)

    @classmethod
    def device(cls, *, idle_suspect_s: float, hard_ceiling_s: float) -> "ComputePool":
        """Self-hosted / BYO backend (AGENT_BACKEND=device, P3): runs the turn on a
        user's own enrolled machine via the frozen link.Msg channel, streaming events
        from Claude Code hooks — same contract, execution relocated to the device.

        The two-layer timeout is the SAME policy the local tmux backend runs
        (turn 活跃度检测): `idle_suspect_s` then a `_confirm_alive` process-tree
        probe, `hard_ceiling_s` as the backstop."""
        from app.domain.agent.device_provider import DeviceProvider

        provider = DeviceProvider(
            idle_suspect_s=idle_suspect_s, hard_ceiling_s=hard_ceiling_s
        )
        return cls([provider], provider.name)

    def tmux_activity_status(self, topic_id: uuid.UUID) -> dict | None:
        """turn 活跃度检测: `cheese status`'s idle-suspect signal, read from
        whichever tmux provider is in this pool (at most one — see
        `build_compute_pool`). None when there's no tmux provider in the pool,
        or no turn currently monitored for this topic (not running, or running
        on a different backend)."""
        from app.domain.agent.tmux_provider import TmuxHooksProvider

        for provider in self._providers.values():
            if isinstance(provider, TmuxHooksProvider):
                return provider.activity_status(topic_id)
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
        from app.domain.agent.harness.claude_code import (
            HooksSessionProvider,
        )

        for provider in self._providers.values():
            if isinstance(provider, HooksSessionProvider):
                provider.bind_event_consumer(consumer)
                if activity_consumer is not None:
                    provider.bind_activity_consumer(activity_consumer)

    def bind_prompt_receipt_consumer(
        self, consumer: Callable[[uuid.UUID, str], Awaitable[None]]
    ) -> None:
        """Give hooks providers the owner of UserPromptSubmit receipts — the
        consumed-stamp side of #539 decision A."""
        from app.domain.agent.harness.claude_code import (
            HooksSessionProvider,
        )

        for provider in self._providers.values():
            if isinstance(provider, HooksSessionProvider):
                provider.bind_receipt_consumer(consumer)

    def has_live_screen(self, topic_id: uuid.UUID) -> bool:
        """Does any provider in this pool still hold a screen for this topic?
        See `HooksSessionProvider.has_live_screen`."""
        from app.domain.agent.harness.claude_code import (
            HooksSessionProvider,
        )

        return any(
            provider.has_live_screen(topic_id)
            for provider in self._providers.values()
            if isinstance(provider, HooksSessionProvider)
        )

    async def recover_hook_subscriptions(
        self, device_id: str | None = None
    ) -> list["TopicSubscription"]:
        """Recover subscriptions for screens that survived this process."""
        from app.domain.agent.harness.claude_code import (
            HooksSessionProvider,
        )

        recovered: list[TopicSubscription] = []
        for provider in self._providers.values():
            if isinstance(provider, HooksSessionProvider):
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


def build_compute_pool(cloud_provider: ComputeProvider | None = None) -> ComputePool:
    """Build the ComputePool from settings.

    The local transport (a container on this box) always joins, the device
    transport always joins, and Cloud joins when it is configured. A topic picks
    between them per turn, with the first turn pinning the choice; the device
    and Cloud pools are only OFFERED when they can actually run something, which
    is what keeps them opt-in without a deployment switch.

    ``agent_backend`` used to choose between this and an SDK subprocess. There
    is nothing to choose between now.
    """
    from app.domain.agent.device_provider import DeviceProvider
    from app.domain.agent.tmux_provider import TmuxHooksProvider

    local = TmuxHooksProvider(
        image=settings.tmux_sandbox_image,
        idle_suspect_s=settings.agent_idle_suspect_s,
        hard_ceiling_s=settings.agent_turn_hard_ceiling_s,
    )
    # Same two-layer timeout policy for the remote transport (turn 活跃度检测),
    # from the SAME settings — the local and remote hooks backends share one knob
    # pair, they don't drift. Replaces the old single `device_turn_timeout_s` that
    # collapsed both layers into one 900s deadline and killed long-but-silent turns.
    providers: list[ComputeProvider] = [
        local,
        DeviceProvider(
            idle_suspect_s=settings.agent_idle_suspect_s,
            hard_ceiling_s=settings.agent_turn_hard_ceiling_s,
        ),
    ]
    if cloud_provider is not None:
        providers.append(cloud_provider)
    default_name = (
        DeviceProvider.name if settings.agent_backend == "device" else local.name
    )
    return ComputePool(providers, default_name)


def app_preview_reachable(compute_profile: str | None) -> bool:
    """Can 运行环境预览 exist for a topic running on this compute at all?

    The feature resolves the app port a *docker container on the backend's own
    host* publishes (``workspace.app_endpoint`` → ``docker port``). That mapping
    exists only when the topic's box IS a container here. A turn running on
    someone's enrolled machine (``device``) or on a leased Cloud machine
    (``cloud`` — a DeviceProvider subclass) has no container on this host, so the
    lookup returns None for a reason that has nothing to do with the app: there
    is no path from the platform to that port, and there never was.

    Without this distinction both cases collapse into "container down", and the
    panel tells those users to @ 芝士 again to bring up a box that is not coming.
    """
    from app.domain.agent.market import compute_default_name
    from app.domain.agent.tmux_provider import TmuxHooksProvider

    local_box = {TmuxHooksProvider.name}
    # A topic that has an app artifact has necessarily run a turn, and the first
    # turn pins `topic.compute_profile` — so the sticky project/team chain is
    # already collapsed into it and only the deployment default is left to apply.
    return (compute_profile or compute_default_name()) in local_box
