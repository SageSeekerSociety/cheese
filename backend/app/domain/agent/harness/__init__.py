"""AgentRuntime — 在一条通道上跑一个 agent，吐结构化事件。

Two questions were one question until now. *Where does this turn run* — a cloud
machine, the user's own laptop, a container next to the backend — is a
``ComputeProvider``. *What runs there* — Claude Code today, something else next
— is an ``AgentRuntime``. They were the same switch because the only harness we
drive is also the only thing that knows how to reach its own machine.

The contract's core is deliberately NOT 「跑一轮，返回一个事件迭代器」. Whoever
holds such an iterator OWNS that turn, and when that process dies the turn is
gone — which is where every piece of salvage machinery came from. Feeding and
reading are separate here:

    ensure     在不在；不在就起
    send       送一条消息进去，回一个「收到了」。不返回事件
    backlog    从游标往后读它说过什么。可重连、可续、可以有多个读者
    interrupt  停手
    close      这条会话不要了

The agent was never the fragile part: claude keeps working inside its machine
while the backend is replaced. What used to die was our BOOKKEEPING, because it
hung off an iterator that died with the process. Reading from a cursor is what
makes recovery a reconnect instead of a salvage operation.

``run_turn`` does hand back an iterator, and is safe for the same reason:
what it iterates is a session that outlives it. Dropping it loses the READING,
never the work — the agent keeps going and ``backlog`` picks the tail up again.
That is the whole difference from the shape this contract replaced, where the
process holding the iterator was the process running the turn.

``send`` is also how a person interrupts with words — a message that arrives
mid-work is not a special case here, it is one more send. ``interrupt`` is the
other thing: take the work away without saying anything.

Three verbs of this contract already had implementations under other names, and
the fourth was a hole with consequences. The platform could stop LISTENING to a
session (drop the subscription) and it could DESTROY one (kill the screen), and
between those two there was nothing — so a turn judged wedged had its backend
coroutine cancelled while the claude on the screen kept going, which is the
whole reason the orphan sweep has to go and ask whether that screen is still
alive. ``interrupt`` is that missing middle.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
)

if TYPE_CHECKING:
    from app.domain.agent.compute import ComputeProvider


# What the platform hands a runtime so the room can hear it. The vocabulary is
# ``AgentEvent`` — a message, a tool call, a result — which every harness has to
# speak anyway; nothing about these three says how the events were sensed. They
# lived in the Claude Code adapter under names starting with "Hook", which is
# how they were sensed and not what they are.
#
# (project, topic, work id, event, event id, final text already seen, unsolicited)
EventConsumer = Callable[
    [
        uuid.UUID,
        uuid.UUID,
        uuid.UUID,
        AgentEvent,
        str | None,
        bool,
        bool,
    ],
    Awaitable[None],
]

# (project, topic, work id, active) — a session started or stopped working.
ActivityConsumer = Callable[[uuid.UUID, uuid.UUID, uuid.UUID, bool], Awaitable[None]]

# (topic, prompt text) — a session CONSUMED an input we injected. Late by
# design: the write is delivery, this is the receipt.
ReceiptConsumer = Callable[[uuid.UUID, str], Awaitable[None]]

# (topic) → the loop-clock reading at which the OLDEST message we injected and
# have not seen consumed was written, or None when nothing is waiting.
#
# The receipt above answers "did this one land"; this answers "is anything still
# unanswered, and since when". A session that has stopped reading its input can
# go on producing output indefinitely, so nothing else in the liveness picture
# notices it: the hooks keep arriving and the screen stays alive. What it cannot
# do is take the next thing somebody typed, and that is a failure with a person
# on the other end of it.
UnreadProbe = Callable[[uuid.UUID], float | None]

# The harnesses this deployment can run, by name — and the ONLY place in
# ``backend/app`` where a harness name is written down (不变量 I5). Declared here
# rather than beside ``HARNESSES`` below because the resolution just under them
# needs a name before the registry exists. What each of them can be pointed at
# is further down, under 「which harness」.
# ``HARNESSES`` further down is the shorter list of what this deployment RUNS
# (结论 43): a name here buys no registration.
CLAUDE_CODE = "claude-code"
CODEX = "codex"
PI = "pi"

# 部署的设置里没写跑哪个骨架时，跑的就是这个。写在这里而不是写进
# ``core/config.py`` 的默认值，因为骨架的名字全仓只在这个文件出现（不变量 I5，
# ``tests/unit/test_harness_boundary.py`` 的字面量守卫盯着这一条）。
_UNCONFIGURED = CLAUDE_CODE

#: 项目设置里盖过部署设置的那个键（``Project.settings``）。开发者选项，界面上没
#: 有它——普通用户看不到骨架这回事（结论 28）。
HARNESS_SETTING = "harness"


def _known(name: str, source: str) -> str:
    """名字得是这个仓库有适配层的一个，否则是写错了。

    不兜底回默认值：兜底的那一版会让一个配错名字的部署安静地跑另一个骨架，而
    「跑的是哪个」正是结论 28 要求只有一个答法的那件事。

    认的是适配层的名字，不是注册表：注册表只列答得出四条硬性要求、这套部署真跑
    的骨架（结论 43），而一个项目把设置指向一个有适配层、这套部署却没注册的骨架，
    是一件轮次开始时要**在房间里说出来**的事（``chat.py`` 的「没有部署」那一
    句），不是一次配置错误。部署级的那一条另有一问（``deployment_harness``）。
    """
    if name not in (CLAUDE_CODE, CODEX, PI):
        raise ValueError(f"{source} 指定的骨架 {name!r} 没有适配层")
    return name


def deployment_harness() -> str:
    """这套部署跑的骨架（结论 28）——一条部署设置，不是谁的属性。

    设置在函数里读，不在模块顶上 import：这个文件是 codex runner 那个
    standard-library-only 归档的一部分（``codex/bundle.py``），而 ``core.config``
    带着 pydantic-settings 和它整棵依赖树，不在归档里——顶上一行 import 就是
    runner 进程起不来。runner 自己从不问这个问题，它被告知自己是谁。
    """
    from app.core.config import settings

    configured = (settings.agent_harness or "").strip()
    if not configured:
        return _UNCONFIGURED
    # 部署级的这一条要的不只是有适配层，还得注册了：装配 ``ComputePool`` 时就
    # 会解析，所以配错了是起不来，不是跑到一半才炸。
    if _known(configured, "agent_harness") not in HARNESSES:
        raise ValueError(
            f"agent_harness 指定的骨架 {configured!r} 这套部署没有；"
            f"有的是 {sorted(HARNESSES)}"
        )
    return configured


def harness_for(project_settings: Mapping[str, Any] | None) -> str:
    """这个项目跑的骨架：项目自己的设置盖过部署设置，都没说就是部署的那个。"""
    wanted = str((project_settings or {}).get(HARNESS_SETTING) or "").strip()
    if not wanted:
        return deployment_harness()
    return _known(wanted, f"项目设置 {HARNESS_SETTING}")


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Which conversation this is, to the harness holding it.

    A room hosts as many conversations as it seats agents, so a topic id does
    not name one — ``(topic, agent_handle, harness)`` does, and it is the same
    key ``agent_sessions`` is written under. Everything that resolves where a
    session runs starts from this, which is why the machines are recorded per
    session and never per room (结论 60).

    ``agent_handle`` is :attr:`ResolvedAgent.handle`, the agent's key inside its
    project — not the seat it authors under. The two differ, and reading under
    one while writing under the other hands back None rather than failing.

    It is left unset by the calls that address a PLACE rather than a
    conversation: a room's event spool and the screen it is watched in are one
    per room, so reading them names no agent. Anything that resolves where a
    session runs must fill it in — that resolution is per session and there is
    nothing on the room left to fall back to.

    ``harness`` has none of that leeway: it is keyword-only and has no default.
    跑的是哪个骨架由部署设置加项目设置答（结论 28），所以一个默认值就是第二个答
    法——而且是个够不着项目那一层的答法：它只看得见部署设置，于是一个项目盖过了
    部署的房间，ref 会带着部署那个名字去写会话行，把一条对话拆成两行。每个构造点
    都说得出一个自己知道的答案：适配器里面是 ``self.harness``（它就是那个
    runtime），轮次那一路是这一轮解析出来的那个。
    """

    project_id: uuid.UUID
    topic_id: uuid.UUID
    agent_handle: str = ""
    harness: str = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """One thing the agent did, as the harness recorded it.

    ``key`` is this event's place in the log and doubles as the cursor to read
    from next — it orders, and nothing else about it is meaningful. ``eid`` is
    the harness's own id for the event and is what makes landing it twice
    harmless.

    ``age_s`` is how long ago the harness recorded it. A reader waiting for the
    rest of a half-arrived message needs to know whether the rest is still
    coming or the session died mid-sentence, and only a real clock answers that
    — the key is a sequence number, not a time.

    ``record`` is the harness's own note of what happened, and is opaque here on
    purpose: the platform hands it back to ``Backlog.assemble`` rather than
    reading it. None means the entry could not be parsed — a corrupt record must
    not stop the log behind it, so it is reported and stepped over rather than
    skipped silently.
    """

    key: str
    eid: str
    record: object | None
    age_s: float


@dataclass(frozen=True, slots=True)
class Opening:
    """What a session is started with and cannot be told afterwards.

    Carried on every ``send`` rather than set once, because a harness that reads
    its system prompt at launch (Claude Code does) keeps whatever it started
    with — so the opening only matters on the call that turns out to be a cold
    start, and no caller can know in advance which one that is.
    """

    system_prompt: str
    resume_token: str | None = None
    model: str | None = None
    env: dict[str, str] | None = None
    memory_scope: str | None = None
    owner: str | None = None
    agent_handle: str | None = None
    # 这一轮要不要一双手？(结论 19，不变量 I2) 会话先于地点：先解析被点名的参与者、
    # 取到会话，再问这一问题，需要了才去租。False 的一轮只有对话、记忆和平台工具
    # ——仓库文件和项目命令都不在它桌上，所以它在所有执行机离线时也必须答得出来。
    #
    # 它是这一轮的属性，不是这条会话的：同一条会话可以这一轮只聊天、下一轮动文件。
    # 所以它随 ``Opening`` 每次送进来，而不落在 ``SessionRef`` 上。
    needs_place: bool = True


class SubagentRequirement(StrEnum):
    """派一条活是 agent 对骨架原生 subagent 的工具调用，不走平台（结论 43）。

    平台这一侧没有「派活」的路径：agent 先开卡，再用骨架自己的工具起一条子线程，
    hook 按线程标识归卡，结束写结论，人对卡的操作投递给父线程执行。这四条是那条
    路成立的前提，所以它们是骨架契约的**硬性要求**，不是能力位。

    能力位（``speaks_gateway`` 那几个）答的是「这个骨架做不做得到，做不到就在功能
    矩阵里填一条差异码」；硬性要求没有那一档——答得出的才进 ``HARNESSES``，答不出
    的留着代码不注册，矩阵里也就不占一列。``Difference`` 里因此不许有一条码描述这
    四项中的任何一项：一条能填进来的差异码就是一个「暂缺」，而暂缺的骨架本来就不
    该在跑。
    """

    SPAWNS_WITH_A_MODEL = "起子 agent，并指定它跑哪个模型"
    LABELS_ITS_THREAD = "子 agent 的每个事件带可归到卡的线程标识"
    PARENT_RETASKS_IT = "父线程能改它的指令"
    PARENT_STOPS_IT = "父线程能停掉它"


@runtime_checkable
class AgentRuntime(Protocol):
    """One harness, driven over whatever channel the compute side opened.

    四条硬性要求（``SubagentRequirement``，结论 43）不是这里的第七个动词，因为平台
    不起子 agent：它们是这个骨架的事实，各写一句「怎么做到的」，落在注册表那一条
    （``HARNESSES[harness].subagents``）上，和 ``carries_subscription`` 那几个事实
    同一个地方。写在这里就是同一个事实两份声明。

    平台这一侧只有两个动词参与其中，而且已经在下面了：``deliver`` 把人对卡的操作
    送进**父**会话，``interrupt`` 停的也是**父**会话——子线程的指令由起它的父线程
    自己改，子 agent 与父进程同生同死。
    """

    # WHICH harness this is — a key in ``HARNESSES``, and what an agent type's
    # ``harness`` field names. Deliberately not ``name``: the objects that
    # implement this today also carry a ``name`` that answers a different
    # question ("which machine pool"), and one attribute cannot mean both.
    harness: str

    async def ensure(
        self, session: SessionRef, opening: Opening, *, work_id: uuid.UUID | None = None
    ) -> object:
        """Is this session live? Start it if not."""
        ...

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
        """Put a message into the session. True = the transport took it.

        An ack, not an answer. What the agent does about it arrives through
        ``read`` — possibly minutes later, possibly to a different process than
        the one that sent this.

        ``work_id`` and ``on_mark`` do not belong to this contract and are
        declared anyway, because the only caller passes them and a signature
        that pretended otherwise would be a promise no second harness could
        keep. They are the platform's turn bookkeeping — a turn is still what
        the room shows and what gets billed — and they leave when a turn stops
        being how work is tracked. Same for ``ensure``'s ``work_id``.
        """
        ...

    def backlog(self, session: SessionRef) -> "Backlog":
        """What this session has said that the platform has not landed yet.

        A fresh reader each call, starting from the one cursor the harness keeps
        for the platform. Reading never consumes, so a second reader that keeps
        its own position sees the same tail — that is the whole point of the log
        being a log.
        """
        ...

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """Put text into a session that is already working, with no turn opened
        for it. True = it landed; False = there is no live session here.

        The bare form of ``send``: no opening, no bookkeeping, nothing to start.
        It is how a person's mid-turn message reaches 芝士, and how the platform
        tells a working session that the world changed under it. The two will be
        one call once a turn stops being how work is tracked; today ``send``
        opens a turn and this does not, so they are still two.

        Keyed by topic rather than by ``SessionRef`` because the caller is on the
        hot path with a person waiting and has no project id in hand — the same
        reason ``close`` is topic-keyed underneath.
        """
        ...

    async def interrupt(self, session: SessionRef) -> bool:
        """Take the work away. True = the stop signal reached the session.

        Not a message — ``send`` is how you interrupt with words. This is for
        when the platform has decided the work should not continue and has
        nothing to say about it: a turn judged wedged, a ceiling reached, a
        person pressing stop.

        Weaker than ``close``, deliberately: the session stays, its conversation
        stays, and the next ``send`` continues it. Killing the screen would take
        the conversation with it.
        """
        ...

    async def close(self, session: SessionRef) -> None:
        """Let this session go: stop listening, forget the channel."""
        ...

    @property
    def hard_ceiling_s(self) -> float:
        """How long a turn on this harness may run before it is called dead.

        A harness fact, not a machine one: how long "no output" may last before
        it means something is wrong depends on what is producing the output. The
        turn path reschedules its own outer wall clock to this rather than to a
        deployment-wide default, so a harness that thinks longer is not killed
        for it.
        """
        ...

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
        images: list[dict] | None = None,
        agent_handle: str | None = None,
        session_agent: str,
    ) -> AsyncIterator[AgentEvent]:
        """One turn, start to finish, as the events it produced.

        ``session_agent`` is the conversation's key — :attr:`ResolvedAgent.handle`
        — which is what the machines this turn runs on are recorded under. It is
        required rather than optional because a turn with no key resolves no
        place, and the errand would silently rent a second machine every time.

        ``ensure`` + ``send`` + read, for a caller that has nothing to recover
        to: the platform's OWN errands — the activity digest, the heartbeat
        patrol, the project summary — have no room waiting on them and no
        timeline to backfill, so the turn is worth exactly as much as the
        iterator that reads it.

        A room's turn does NOT go through here. It sends, and reads what comes
        back through the subscription and the backlog, because there the work
        has to survive the reader.
        """
        ...

    def bind_events(self, consumer: EventConsumer) -> None:
        """Where the room's persistence and broadcast live."""
        ...

    def bind_activity(self, consumer: ActivityConsumer) -> None:
        """Where 「这个会话在干活 / 停了」 goes."""
        ...

    def bind_receipts(self, consumer: ReceiptConsumer) -> None:
        """Where 「会话真的读到了那条消息」 goes."""
        ...

    def bind_unread_probe(self, probe: UnreadProbe) -> None:
        """Where 「还有没有消息在等着被读」 is asked."""
        ...

    def holds(self, topic_id: uuid.UUID) -> bool:
        """Is there a session here this runtime can still reach?

        This is what "the work survived" means after a backend restart: the
        coroutine waiting on the turn died with the process, the agent in the
        execution environment did not, and ``recover`` found it again.
        """
        ...

    async def recover(self, device_id: str | None = None) -> list[SessionRef]:
        """Sessions of ours that outlived this process, listening again.

        Called on the way up, and again whenever a machine reconnects. What
        they SAID while nobody was listening is ``replay``'s job — this only
        establishes that we are listening.
        """
        ...

    async def replay(self, session: SessionRef, *, known_texts: set[str]) -> None:
        """Land the tail a recovered session produced while nobody listened.

        ``known_texts`` is what the room already shows, so a message the live
        path did persist before the process died is not landed twice. The
        platform supplies it because the room is the platform's; which of the
        harness's own records are still unlanded is the harness's.
        """
        ...


@runtime_checkable
class Backlog(Protocol):
    """The unread tail of one session, as the platform needs to consume it.

    Six calls, and the split between them is the point. ``unread`` and
    ``assemble`` are the harness's — what did this agent say, and what does one
    log entry mean. Deciding what to DO about it (persist a block, broadcast a
    frame, skip a duplicate) is the platform's, and happens between the two.

    ``assemble`` returns a LIST because one entry is not one thing: a harness
    that reports partial output emits several records per message, and whether
    they add up to something whole is knowledge only it has. That is also why
    ``unfinished`` exists — the platform must not move the cursor past an entry
    whose message is still missing a piece, or the pass that completes it will
    never see what it is made of.
    """

    def unread(self) -> Sequence[HarnessEvent]:
        """Everything after the cursor, oldest first. A snapshot: landing
        things during the pass does not change what this returned."""
        ...

    def assemble(self, entry: HarnessEvent) -> Sequence[AgentEvent]:
        """What this entry means, once anything it completes is folded in.
        Empty = nothing whole yet, or nothing that could be read."""
        ...

    def unfinished(self) -> set[str]:
        """Ids of entries assembled so far whose thing is still incomplete."""
        ...

    def give_up(self) -> Sequence[AgentMessage]:
        """Hand over the incomplete pieces anyway — the session died
        mid-sentence and what arrived is better landed than lost.

        Messages, not events: a tool call is whole the moment it is reported,
        so the only thing that can be caught half-arrived is something the
        agent was still saying."""
        ...

    def landed(self, *, through: str) -> None:
        """Everything up to and including this key reached the timeline."""
        ...

    def forget(self, *, older_than_s: float) -> None:
        """Drop records older than this. Retention removes an event, never
        having been read."""
        ...


def runtime_for(provider: "ComputeProvider") -> AgentRuntime:
    """The harness behind this provider.

    Every provider has one — ``ComputePool`` refuses one that does not, so this
    is where that guarantee is stated rather than a question each caller has to
    handle a None for. It reads structurally instead of by class so the rest of
    the code can ask 「这台机器上跑的是什么」 without naming Claude Code to find
    out.

    There used to be backends with no runtime at all: a subprocess that starts a
    model, streams its output and exits has no session to ensure, nothing to
    send into afterwards, and no log to read from a cursor. That shape is gone.
    """
    if not isinstance(provider, AgentRuntime):
        raise TypeError(f"{type(provider).__name__} runs no harness")
    return provider


# --- which harness ----------------------------------------------------------
#
# The names themselves are declared at the top of this module, because
# ``SessionRef`` defaults to one and a default written as a literal is a second
# declaration of the same fact.


@dataclass(frozen=True, slots=True)
class Harness:
    """One harness, and what it can be pointed at.

    These two facts used to be recorded the other way round — every MODEL
    carried a list of the harnesses allowed to drive it — and the direction was
    backwards in a way that cost real things. It is the harness that can or
    cannot speak to something: Codex supports the models it has adapters for,
    an Anthropic subscription credential is minted for the one harness that can
    present it. A model has no opinion about any of that.

    Written the wrong way round, adding a harness meant editing the model
    catalogue, and refusing a combination produced an error about the model —
    the half the person had actually chosen on purpose.
    """

    name: str
    # What a person would call it. Not a display concern: this is the only
    # place the name a human sees is written down.
    label: str
    # 四条硬性要求（结论 43），每条一句「怎么做到的」，指得出代码在哪。一句「已支
    # 持」而指不出是哪一行做的，下一个人没有办法核，也没有办法在它失效的时候发现
    # ——和 ``capability`` 那张表里的格子同一条规矩。
    #
    # 反引号里写的是**本仓库的东西**：带 `/` 的（或者以 `.py`、`.md` 结尾的）是路
    # 径，从 `app/domain/` 起算；其余的是符号名，每个都要在同一句引的某个文件里找
    # 得到。规矩不限于代码文件——一条要求的做法写在哪儿就引哪儿，
    # `agent/skill_library/` 下那几份发给 agent 的说明也算数。
    # ``test_subagent_requirements.py`` 两样都核，而且核符号那一样要求它**参与了代
    # 码**：被定义、被赋值、被读。只核「文件里有这串字」是不够的——一张
    # ``merged.pop`` 的删除名单里也有这串字，而一张删除名单证明的恰好是这句话的反
    # 面。上游的工具名（Task、Agent）不加反引号：那不是这里能核的东西。
    #
    # 值只能是一句话。``Difference`` 是 StrEnum，填进来照样是个 ``str``，所以
    # ``__post_init__`` 认的是类型本身：硬性要求没有「这个骨架做不到」那一档。
    subagents: Mapping[SubagentRequirement, str]
    # Does it speak the platform gateway's own shape? Then every model the
    # project can use is one it can drive, and no deployment has to list them.
    # False means it supports only what it has its own adapter for, and an
    # operator names those in ``agent_harness_models``.
    speaks_gateway: bool = True
    # Can it present an Anthropic subscription credential? That credential is
    # minted for ONE harness; no other can carry it, whatever it can otherwise
    # drive.
    carries_subscription: bool = False
    # Does the agent DRAW on the screen it was started in? Claude Code is a TUI,
    # so its pane is the 现场 — a person watching it sees the work happen. A
    # harness whose screen runs a runner and talks to the agent over RPC has a
    # pane with nothing in it, for ever.
    #
    # Read by the terminal endpoint, which must answer "will the drawer
    # actually show a pane?" and until now answered "is a screen open" — the
    # same thing for a TUI, and not the same thing at all for a runner. The
    # drawer replaces the 施工记录 timeline with the embed on a true, so
    # answering it wrongly is what leaves someone in front of a black frame
    # with no way back to the timeline.
    draws_on_its_screen: bool = True

    def __post_init__(self) -> None:
        """答不全四条的，根本造不出来——这就是「摘掉」的可判形式。

        判在构造上而不是判在一条守卫测试上：注册表是一个字面量，一个造得出来的
        条目总会有人写进去。
        """
        for requirement in SubagentRequirement:
            answer = self.subagents.get(requirement)
            if type(answer) is not str or not answer.strip():
                raise ValueError(
                    f"{self.name} 没有答「{requirement}」。这是硬性要求（结论 43）："
                    "答得出的骨架才上注册表，答不出的留着代码不注册。"
                    "一条差异码也不算答——硬性要求没有「暂缺」那一档。"
                )


HARNESSES: dict[str, Harness] = {
    CLAUDE_CODE: Harness(
        CLAUDE_CODE,
        "Claude Code",
        subagents={
            SubagentRequirement.SPAWNS_WITH_A_MODEL: (
                "Agent(model=...) selects a native child model. The pinned-binary "
                "test_claude_child_models verifies general-purpose children: explicit "
                "selection overrides CLAUDE_CODE_SUBAGENT_MODEL, supplied from the "
                "project child default or project main default. "
                "`agent/harness/claude_code/session_launch.py` installs the preload "
                "that carries the chosen model to backend admission. "
                "Admission validates "
                "the catalog and tier policy before either supply pool forwards it. "
                "The pinned tool schema says forks inherit the parent model; the "
                "tested startup rejects the fork agent type with a visible tool "
                "error. Fork model selection is not claimed as supported."
            ),
            SubagentRequirement.LABELS_ITS_THREAD: (
                "`agent/harness/claude_code/hook_events.py` 的 `SubThreads`：标识由"
                "`room_task/thread_label.py` 的 `thread_label` 算出来、写在起它的那"
                "段 prompt 里，PostToolUse 的 tool_response.agentId 把它钉在这个 "
                "worker 上，此后这条子线程的每条记录都带着它出来。"
            ),
            SubagentRequirement.PARENT_RETASKS_IT: (
                "改指令的是起它的父线程，做法写在 "
                "`agent/skill_library/stage_delegating.md`：还在跑的，父线程直接给"
                "这条子线程发消息；已经停了的，在房间会话里用同一个线程标识重新派"
                "一条——所以换了要求还是那条活、还归那张卡。平台这一侧只有 "
                "`agent/harness/__init__.py` 上的 `AgentRuntime.deliver`，它把人对"
                "卡的操作送进**父**会话，父线程读到之后才去做上面那件事；平台不认"
                "子线程，也不直接对它说话。"
            ),
            SubagentRequirement.PARENT_STOPS_IT: (
                "停的是**一条**子线程，做法和改指令写在同一处 "
                "`agent/skill_library/stage_delegating.md`：父线程调 TaskStop，按起"
                "它时给的那个名字停那一条，同一条会话里的其他分身照跑。平台这一侧"
                "的 `agent/harness/__init__.py` 上 `AgentRuntime.interrupt` 与 "
                "`AgentRuntime.close` 停的都是整条会话——那是结论 43 的另一句「子 "
                "agent 与父进程同生同死」，不是这一条，拿它来答这一条等于这条要求"
                "恒真。这一手在房间里落不落得了地由 "
                "`agent/harness/claude_code/remote_execution/proxy.js` 决定：一个停"
                "任务的 id 有两个主人，转给执行器的那条路只认执行机上后台跑着的命"
                "令，执行器答「不认识」的那个 id 就是一条子线程，放手让骨架自己停；"
                "`agent/harness/claude_code/remote_execution/client.py` 的 `guarded`"
                " 把它从那道「插件没接住就拒掉」的闸门里摘出来，这次放手才到得了骨"
                "架。"
            ),
        },
        carries_subscription=True,
    ),
}

# Codex 与 pi 今天答不出这四条，所以不在上面（结论 43）。**这是一次产品收缩，不是
# 一次重构副作用**：它们的适配层原样留在 `agent/harness/codex/` 与
# `agent/harness/pi/`——契约夹具照跑、行为声明照写、pin 照被守卫管着——只是这套部
# 署不跑它们，功能矩阵里也不占一列。两个出口里取的是这一个；另一个是给它们各写一
# 层适配（两家上游都支持多线程），什么时候写出来、什么时候答得出四条，什么时候回
# 到上面这张表。


def harness_name(name: str | None) -> str:
    """调用方给的 harness 名，没给就是这套部署跑的那个。"""
    return name or deployment_harness()
