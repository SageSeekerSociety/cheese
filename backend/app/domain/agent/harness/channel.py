"""Transport operations shared by harness implementations."""

import asyncio
import uuid
from typing import Protocol

from app.domain.agent.harness.launch import MachinePlan
from app.domain.agent.platform_failures import TURN_TIMEOUT_MESSAGE


class ActivityClock(Protocol):
    def touch(self, at: float) -> None: ...


class ScreenSetupError(Exception):
    """A backend couldn't bring the screen to a prompt-ready state (no Docker /
    no online device / not ready in time). Its message becomes the work error
    result — the ONE place setup failures turn into an ``AgentResult``.

    ``failure_code`` is set when the platform already knows WHICH failure this
    is (`platform_failures`). Left None for the setup failures it has no
    classification for, which then land as an unnamed turn error — the same
    place they landed before, but by omission rather than by a sentence not
    matching."""

    def __init__(self, message: str, *, failure_code: str | None = None) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class Channel:
    """Reach a session host, prepare its workspace, and transport input.

    Implementations report delivery and liveness; the harness owns event
    translation, provider requests, and session state. Startup and input delivery
    require an implementation. Optional operations report their availability.
    """

    # WHICH machine pool this is: what a topic's ``compute_profile`` stores and
    # what the market board lists. Deliberately not the runtime's ``harness`` —
    # this says which machine, that says what runs on it.
    name: str = "channel"

    # 图片输入: whether the bytes actually reach the session on this transport.
    # The runtime @-mentions the path and Claude Code resolves it into a native
    # image block — true for a local screen that already shares the worktree and
    # for a device that stages the file first, and a transport where neither
    # holds MUST say False rather than let the prompt promise an image 芝士
    # cannot see.
    embeds_images: bool = True

    async def stage_images(
        self, screen: object, images: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Put these images where the screen can open them, as
        ``(reachable, unreachable)``.

        The default answers for every transport whose screen already shares the
        worktree the upload was written to: all of them, nothing lost.

        Splitting this out of ``send_prompt`` is the point. When staging lived
        inside the send, a transport that could not stage — an older connector
        that does not know the file frame, a machine that is briefly
        unreachable — raised out of the send and took the ENTIRE message with
        it, text included. Measured 2026-08-23: a message with one screenshot
        left no trace in the room at all, while plain-text messages around it
        arrived normally. An image that cannot be delivered must cost the image.
        """
        del screen
        return list(images), []

    # Does a turn here have to wait for a machine to be created first? The turn
    # path branches on it (``ChatService`` shows 「机器正在创建」 and holds the
    # prompt) rather than on the channel's class, so a second leased-machine
    # transport gets the same waiting room without the platform learning its
    # name.
    provisions_machine: bool = False

    # Does this channel assemble the machine's model environment itself? True
    # means the platform sends the model CHOICE and nothing else: no base_url,
    # no provider credential, no alias pins — the channel builds them where the
    # machine is, and the turn's supply route is the deployment's (the metering
    # proxy under a subscription, /llm otherwise). False means the channel is
    # handed the resolved profile env instead.
    #
    # A capability, not a name. The turn path decided this by NAME twice, and
    # each time the name aged into a list of the channels that happened to have
    # the trick on the day it was written: the next channel to learn it — or to
    # inherit it wholesale — was never added. Nothing failed loudly when that
    # happened. The turn ran, on the wrong supply's meter and with a --model
    # flag the supply does not serve, and only the invoice said so.
    #
    # Any channel whose machine is not this process MUST say True. Saying False
    # ships it ``profile.full_env()`` — a base_url only this box can resolve and
    # a real provider key — onto hardware over a network, which is the leak the
    # split exists to prevent.
    builds_model_env: bool = False

    # How big the machine behind this channel is, when we are the ones who set
    # it. None means the channel genuinely does not know — an enrolled machine
    # belongs to someone else — and the prompt then says nothing rather than
    # inventing a limit the agent would plan around.
    sandbox_memory_mb: int | None = None
    sandbox_cores: int | None = None

    # Copy only. Said when a turn arrives with no topic, and when one times out
    # — a timeout is classified by the code the result carries, so a channel may
    # word these however it likes.
    needs_topic_message: str = "本轮需要话题上下文"
    timeout_message: str = TURN_TIMEOUT_MESSAGE

    def available(self) -> bool:
        return True

    async def prepare_topic(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        actor: object | None,
    ) -> tuple[bool, str]:
        """Get the machine ready before the turn counts a delivery attempt.

        Only asked of a channel that declares ``provisions_machine``. The
        default is the answer for every transport whose machine is already
        there: ready, nothing to say about it.
        """
        del project_id, topic_id, actor
        return True, ""

    async def discover(
        self, device_id: str | None = None
    ) -> list[tuple[uuid.UUID, uuid.UUID, object | None, str | None]]:
        """Screens of ours that survived this process, as
        ``(project_id, topic_id, screen, running)``. ``screen`` is None when the
        channel knows the topic is still out there but cannot hand back a handle
        for it yet (the device transport reattaches on the next turn).

        ``running`` is the tag the screen was started with, handed back unread:
        one machine can host sessions of more than one harness, and only the
        runtime knows which tag is its own. None means this channel cannot tell
        — and a channel that cannot tell cannot host two harnesses at once,
        because nothing is left to stop one from claiming the other's screens.

        The runtime subscribes to what this returns. A channel that answers
        nothing simply has nothing that outlives the backend.
        """
        del device_id
        return []

    def topics_on_device(self, device_id: str) -> list[uuid.UUID]:
        """Topics whose screen lives on the device that just went away."""
        del device_id
        return []

    def forget_topic(self, topic_id: uuid.UUID) -> None:
        """Drop whatever this channel remembers about a topic being torn down."""
        del topic_id

    async def precheck(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> object:
        """Cheap fail-fast checks that run BEFORE the token is minted and the
        hook queue is claimed — a turn that cannot run at all must never touch
        the router. Raise ``ScreenSetupError`` to end the turn with a clean
        error result. The return value is handed to ``ensure_ready`` as
        ``precheck`` so a channel doesn't resolve twice (the device channel
        resolves its pinned device here)."""
        return None

    async def ensure_ready(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        token: str,
        env: dict[str, str] | None,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
        launch: MachinePlan,
        precheck: object,
    ) -> object:
        """Bring the topic's screen to a prompt-ready state; raise
        ``ScreenSetupError`` if it can't be. Implemented by every channel.

        ``launch`` is what to run. A channel does not build it and does not read
        it: it says where this screen keeps its state and what its cwd is, and
        performs the launch that comes back. Which harness that turns out to be
        is the caller's business — a channel that decided could only ever host
        the one.

        It is a launch-time input, not a per-prompt one. The system prompt
        inside it reaches the session through a file read exactly once at exec,
        so a screen that is merely reused keeps the one it was started with, and
        the plan matters only on the call that turns out to be a cold start."""
        raise NotImplementedError

    async def send_prompt(self, screen: object, prompt: str) -> bool | None:
        """Deliver the turn's prompt to the ready screen.

        Returns the screen's readiness at delivery time when the transport can
        know it (the device connector answers ``{ready: bool}``): ``False``
        means the prompt is HELD until the session can take it — worth a visible
        line in the room instead of silence (#445). ``None`` = unknown."""
        raise NotImplementedError

    async def start_activity_monitor(
        self, screen: object, tracker: ActivityClock
    ) -> asyncio.Task | None:
        """Optional background activity signal alongside hook arrivals — a long
        tool call between hooks must still count as "alive". Return a task that
        keeps ``tracker`` touched; ``run_turn`` cancels it when the turn ends.

        Default: no extra signal, activity is judged from hook arrivals alone —
        correct for the device channel today (TODO: an equivalent remote
        activity probe, e.g. ``device_hub`` screen bytes, is future work; see
        ``DeviceChannel``)."""
        return None

    async def send_interrupt(self, screen: object) -> bool:
        """Stop whatever this screen is doing, without saying anything. False =
        this transport has no way to.

        Default: no. Escape is a KEY, and a transport that can put a prompt into
        a session cannot necessarily press one — so a channel that has not said
        it can must answer no rather than raise, or the runtime's ``interrupt``
        turns a missing capability into a crash.
        """
        del screen
        return False

    async def confirm_alive(self, screen: object) -> bool:
        """Called (repeatedly, while idle persists) once the idle-suspect
        threshold is crossed, to confirm the screen isn't actually dead before
        treating the idle window as fatal. Default: assume alive — no cheap
        probe exists at this level. A channel that can ask its transport
        cheaply overrides this."""
        return True
