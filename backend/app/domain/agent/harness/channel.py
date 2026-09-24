"""Transport operations shared by harness implementations."""

import uuid
from typing import NamedTuple

from app.domain.agent import place
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.launch import MachinePlan
from app.domain.device.supply import Supply

# How long a session's scoped credential lives. It is baked into the process at
# launch (its CONNECT password and OAuth token are read once), so it has to
# outlast the session rather than a single turn.
SESSION_TOKEN_TTL_S = 30 * 24 * 3600


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

    # WHICH machine pool this is: what a topic's ``compute_profile`` stores and
    # what the market board lists. Deliberately not the runtime's ``harness`` —
    # this says which machine, that says what runs on it.
    name: str = "channel"

    # Does a turn here have to wait for a machine to be created first? The turn
    # path branches on it (``ChatService`` shows 「机器正在创建」 and holds the
    # prompt) rather than on the channel's class, so a second leased-machine
    # transport gets the same waiting room without the platform learning its
    # name.
    provisions_machine: bool = False
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
    # 物理事实写在这里，能力位（名字在 `place`）从它推出来，上游读能力位。
    # 直接声明一张能力表的话，它会退化成「写它那天恰好有这个本事的通道」的清单，
    # 而继承一份表的子类会连那张过期的清单一起继承走——`compute.py` 里那条恒为真
    # 的 `isinstance(c, DeviceChannel)` 就是这么来的。

    #: 这台机器是谁开的。它今天只回答一个问题：一台这样进来的机器归哪条通道认领
    #: （`owns` 就在下面）。
    supply: Supply = Supply.self_hosted

    #: 这条通道上的手，是不是就是跑会话进程的那台机器。False 表示工具要再跳一程
    #: 到执行机上去——进程和工作区不在一起的骨架才需要那一跳。
    hands_here: bool = True

    def capabilities(self) -> frozenset[str]:
        """这个地点给得出什么 —— 由上面那些物理事实推出来，不是各家自己报一份。"""
        return frozenset({place.HANDS_HERE}) if self.hands_here else frozenset()

    def owns(self, supply: Supply) -> bool:
        """一台这样进来的机器，是不是这条通道该认领的。

        两条通道把话题绑进同一张表，所以一条绑定不说是谁做的——机器说，而供给正是
        分开它们的那根轴。这里读的是同一位 `supply`，不是各通道自己写一条反过来的
        判断：反着写的那一条，在轴上多一个取值的那天就是错的。
        """
        return supply is self.supply

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
