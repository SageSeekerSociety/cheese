"""Transport operations shared by harness implementations."""

import logging
import uuid
from typing import NamedTuple

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.device_hub import DeviceCallError, DeviceNotReady
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.launch import MachinePlan
from app.domain.agent.platform_failures import classify_session_start
from app.domain.device.supply import Supply

# How long a session's scoped credential lives. It is baked into the process at
# launch (its CONNECT password and OAuth token are read once), so it has to
# outlast the session rather than a single turn.
SESSION_TOKEN_TTL_S = 30 * 24 * 3600


def discovery_missed(
    logger: logging.Logger, harness: str, room, device: str, failure: Exception
) -> None:
    """Note a placed session a restarted backend could not reach.

    A machine that answers that the runner is not there is a session that sat
    idle and was let go (``driven.runner``); the next message starts it again,
    so that is not worth a warning. Every deploy finds dozens of them. A machine
    that is away, not ready or not answering is.
    """
    if isinstance(failure, DeviceCallError) and not isinstance(failure, DeviceNotReady):
        logger.info(
            "%s discovery found no runner topic=%s device=%s: %s",
            harness,
            room,
            device,
            failure,
        )
    else:
        logger.warning(
            "%s discovery failed topic=%s device=%s: %s",
            harness,
            room,
            device,
            failure,
        )


def mint_session_token(project_id, topic_id, agent_handle: str) -> str:
    """The credential a session launches with, for ``agent_handle`` in the room
    ``topic_id``: the one every harness starts its agent with, and the one a
    session's executor is started again with when no turn is starting it.

    Whatever holds it may hold it for the life of the session (a process reads
    it once, an idle executor keeps it until it is next prepared), so it lasts
    that long; a shorter one expired under an executor still running, and every
    platform call made from there — its push included — was refused."""
    return mint_scoped_token(
        project_id=str(project_id),
        topic_id=str(topic_id),
        ttl_s=SESSION_TOKEN_TTL_S,
        access_scope="project",
        agent_handle=agent_handle,
    )


class ScreenSetupError(Exception):
    """A backend couldn't bring the screen to a prompt-ready state (no Docker /
    no online device / not ready in time). Its message becomes the work error
    result — the ONE place setup failures turn into an ``AgentResult``.

    ``failure_code`` is set when the platform already knows WHICH failure this
    is (`platform_failures`). Left None for the setup failures it has no
    classification for, which then land as an unnamed turn error — the same
    place they landed before, but by omission rather than by a sentence not
    matching.

    ``log`` is what the failing process itself printed, when there is such a
    text. It is shown in 现场 beside the notice and never becomes the room's
    line: the message is what the room is told."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str | None = None,
        log: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code
        self.log = log


def startup_refused(
    log: str, *, harness: str, timed_out: bool = False
) -> ScreenSetupError:
    """The refusal for a session that did not start: one sentence for the room,
    chosen from what the machine recorded, and that record for 现场."""
    failure = classify_session_start(log, harness=harness, timed_out=timed_out)
    return ScreenSetupError(failure.content, failure_code=failure.code, log=log)


class Placement(NamedTuple):
    """The session host and authenticated actor admitted for this turn.

    Direct channels can rent execution together with the session. Central
    channels set ``deferred`` for project work: this machine is the session
    host, and a tool acquires the independent work lease later.
    """

    machine: str
    agent_user_id: int
    agent_handle: str
    rented: bool
    deferred: bool = False


class Channel:
    """Reach a session host and bring a session up on it.

    What runs there, and how it is talked to afterwards, is the harness's: a
    channel places the session and performs the launch it is handed.
    """

    # WHICH machine pool this is: what a compute choice's ``profile`` names and
    # what the market board lists. Deliberately not the runtime's ``harness`` —
    # this says which machine, that says what runs on it.
    name: str = "channel"

    deferred_work: bool = False

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

    # --- 地点：这条通道租出来的那双手 (结论 24、60) ---------------------------
    #
    # 物理事实写在这里，上游读它，不读类名：按类名问的那条
    # `isinstance(c, DeviceChannel)` 读起来像一条规则，实际恒为真。

    #: 这台机器是谁开的。它今天只回答一个问题：一台这样进来的机器归哪条通道认领
    #: （`owns` 就在下面）。
    supply: Supply = Supply.self_hosted

    def owns(self, supply: Supply) -> bool:
        """一台这样进来的机器，是不是这条通道该认领的。

        两条通道把话题绑进同一张表，所以一条绑定不说是谁做的——机器说，而供给正是
        分开它们的那根轴。这里读的是同一位 `supply`，不是各通道自己写一条反过来的
        判断：反着写的那一条，在轴上多一个取值的那天就是错的。
        """
        return supply is self.supply

    def available(self) -> bool:
        return True

    async def precheck(self, session: SessionRef, *, needs_place: bool) -> object:
        """Cheap fail-fast checks that run BEFORE the token is minted — a turn
        that cannot run at all must never reach a machine. Raise
        ``ScreenSetupError`` to end the turn with a clean error result. The
        return value is handed to ``ensure_ready`` as ``precheck`` so a channel
        doesn't resolve twice (the device channel resolves its pinned device
        here).

        ``needs_place`` is this TURN's answer to 「要不要一双手」 (结论 19).
        It has no default: every caller says which kind of turn this is, so a
        new one cannot inherit 「租」 by saying nothing. Every channel that
        resolves a work machine has to answer it, because a turn that touches no
        file must not be refused for a work machine being offline (不变量 I2) —
        it runs on the session's own machine instead. This base resolves
        nothing, so it has nothing to decline."""
        del needs_place
        return None

    async def ensure_ready(
        self,
        *,
        session: SessionRef,
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
