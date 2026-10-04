"""Harnesses: which ones this deployment runs, what each can be pointed at, and
the shapes every one of them is read in.

A harness is what runs in a session — Claude Code, pi, Codex. Where a turn runs
is a separate question (``ComputePool``), and so is driving a session: the
session core starts, talks to and reads every harness the same way
(`session_host`), and asks each only what its protocol spells differently
(`session_host/driver.py`). What lives here is what both sides need to name a
harness by: the registry (``HARNESSES``) and the facts on each entry, how a
room's conversation is keyed (``SessionRef``), and what one harness record is
before the platform reads it (``HarnessEvent``, ``Backlog``).
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.domain.agent.service import AgentEvent

# How a subscription hands what it read to the reading it serves
# (`session_host/host.py`'s ``read``).
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

# 部署的设置里没列可用骨架时，列表就是这一份。写在这里而不是写进
# ``core/config.py`` 的默认值，因为骨架的名字全仓只在这个文件出现（不变量 I5，
# ``tests/unit/test_harness_boundary.py`` 的字面量守卫盯着这一条）。
_UNCONFIGURED = (CLAUDE_CODE,)

#: 项目设置里指定骨架的那个键（``Project.settings``）。开发者选项，界面上没有
#: 它——普通用户看不到骨架这回事（结论 28）。
HARNESS_SETTING = "harness"


def _known(name: str, source: str) -> str:
    """名字得是这个仓库有适配层的一个，否则是写错了。

    写错的名字不兜底：兜底的那一版会让一个配错名字的部署或项目安静地跑另一个骨
    架，而「跑的是哪个」正是结论 28 要求只有一个答法的那件事。
    """
    if name not in (CLAUDE_CODE, CODEX, PI):
        # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
        raise ValueError(f"{source} 指定的骨架 {name!r} 没有适配层")
    return name


def deployment_harnesses() -> tuple[str, ...]:
    """这套部署可用的骨架，按偏好排好（结论 28）——一条部署设置，不是谁的属性。

    每一个都得注册了：装配 ``ComputePool`` 时就会解析，所以配错了是起不来，不是跑
    到一半才炸。

    设置在函数里读，不在模块顶上 import：这个文件是 codex runner 那个
    standard-library-only 归档的一部分（``codex/bundle.py``），而 ``core.config``
    带着 pydantic-settings 和它整棵依赖树，不在归档里——顶上一行 import 就是
    runner 进程起不来。runner 自己从不问这个问题，它被告知自己是谁。
    """
    from app.core.config import settings

    configured = [name.strip() for name in settings.agent_harnesses if name.strip()]
    if not configured:
        return _UNCONFIGURED
    for name in configured:
        if _known(name, "agent_harnesses") not in HARNESSES:
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise ValueError(
                f"agent_harnesses 列的骨架 {name!r} 这套部署没有；"
                f"有的是 {sorted(HARNESSES)}"
            )
    return tuple(dict.fromkeys(configured))


def _in_order(project_settings: Mapping[str, Any] | None) -> list[str]:
    """这个项目会依次考虑的骨架：它自己指定的那个排最前，前提是部署列了它。"""
    listed = deployment_harnesses()
    wanted = str((project_settings or {}).get(HARNESS_SETTING) or "").strip()
    if wanted and _known(wanted, f"项目设置 {HARNESS_SETTING}") in listed:
        return [wanted, *(name for name in listed if name != wanted)]
    return list(listed)


def harness_for(project_settings: Mapping[str, Any] | None) -> str:
    """这个项目跑的骨架，不问哪台机器：它指定的那个，部署没列就是部署偏好的第一个。

    房间的一轮问的是 ``harness_on``——那一问还要看房间那台机器上挂没挂这个骨架。
    今天每条进池子的通道都挂着每个注册了的骨架，所以两问答的是同一个。
    """
    return _in_order(project_settings)[0]


def harness_on(
    project_settings: Mapping[str, Any] | None, offered: Callable[[str], bool]
) -> str | None:
    """这个项目在一台机器上跑的骨架：按 ``_in_order`` 的次序，第一个这台机器挂着的。

    一个骨架挂不挂得上一台机器，取决于它能不能把工具送到那台机器的手上：它声明了
    ``Capability.REMOTE_EXECUTION``、能把工具调用交出去（``compute.py`` 的
    ``build_compute_pool``）。所以「这个场景要远端执行」不
    在这里再问一遍，问的是那张池子。

    ``None`` = 一个都没有，这一轮在房间里说明，不开始。
    """
    return next((name for name in _in_order(project_settings) if offered(name)), None)


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Which conversation this is, to the harness holding it.

    A room hosts as many conversations as it seats agents, so a topic id does
    not name one — ``(topic, agent_handle, harness)`` does, and it is the same
    key ``agent_sessions`` is written under. Everything that resolves a
    conversation starts from this.

    一个话题一个容器（2026-09-28 决定，推翻结论 60 的后半）：这个键仍然只认一条会
    话，但**它跑在哪台机器上不再由它自己答**——一间房只有一条算力选择，房间里的每
    一条会话都工作在那一项算出来的那台机器上（``machine/session_work._attempt``
    从房间那一项解析），机器于是不再是「每条会话各记一份」的东西。

    ``agent_handle`` is :attr:`ResolvedAgent.handle`, the agent's key inside its
    project — not the seat it authors under. The two differ, and reading under
    one while writing under the other hands back None rather than failing.

    It is left unset by the calls that address a PLACE rather than a
    conversation: a room's machine and the screens on it are one per room, so
    reading them names no agent.

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


class Capability(StrEnum):
    """骨架自己要提供、一部分场景才要的能力——可选的那一档。

    和 ``SubagentRequirement`` 不同：答不出不妨碍注册，只是要它的地方用不了这个骨
    架。每一项在 ``Harness.capabilities`` 里写一句「怎么做到的」，指得出代码在哪，
    规矩和四条硬性要求一样（``test_subagent_requirements.py`` 核）；没有这一项就是
    做不到。看图是模型的事，空闲退出是平台 runner 的事，都不在这里。
    """

    REMOTE_EXECUTION = "会话在一台机器上，工具调用交给另一台机器上的执行环境去跑"


@runtime_checkable
class Backlog(Protocol):
    """The unread tail of one session, as the platform needs to consume it.

    Seven calls, and the split between them is the point. ``unread`` and
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
        """The next page after what this reader has handed out, oldest first,
        starting at the landing cursor; empty once it has caught up. A page,
        not the whole tail: a cursor that fell behind can have a million
        records waiting, and a pass must not hold them all at once."""
        ...

    def assemble(self, entry: HarnessEvent) -> Sequence[AgentEvent]:
        """What this entry means, once anything it completes is folded in.
        Empty = nothing whole yet, or nothing that could be read."""
        ...

    def unfinished(self) -> set[str]:
        """Ids of entries assembled so far whose thing is still incomplete."""
        ...

    def landed(self, *, through: str) -> None:
        """Everything up to and including this key reached the timeline."""
        ...

    def step_over_older(self, *, than_s: float) -> int:
        """Land, unread, the run of entries at the cursor older than
        ``than_s``; how many there were."""
        ...

    def refused(self, key: str, *, times: int, over_s: float) -> bool:
        """Note one more refusal of the entry ``key``; True once it has been
        refused ``times`` times over at least ``over_s`` seconds."""
        ...

    def forget(self, *, older_than_s: float) -> None:
        """Drop records older than this. Retention removes an event, never
        having been read."""
        ...


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
    # `agent/skill_library/` 下和 `../../sandbox/skills/` 里那几份发给 agent 的
    # 说明也算数。
    # ``test_subagent_requirements.py`` 两样都核，而且核符号那一样要求它**参与了代
    # 码**：被定义、被赋值、被读。只核「文件里有这串字」是不够的——一张
    # ``merged.pop`` 的删除名单里也有这串字，而一张删除名单证明的恰好是这句话的反
    # 面。上游的工具名（Task、Agent）不加反引号：那不是这里能核的东西。
    #
    # 值只能是一句话。``Difference`` 是 StrEnum，填进来照样是个 ``str``，所以
    # ``__post_init__`` 认的是类型本身：硬性要求没有「这个骨架做不到」那一档。
    subagents: Mapping[SubagentRequirement, str]
    # 可选能力（``Capability``），每项一句「怎么做到的」，引文规矩同上。不在这里
    # 的就是做不到。
    capabilities: Mapping[Capability, str] = field(default_factory=dict)
    # Does it speak the platform gateway's own shape? Then every model the
    # project can use is one it can drive, and no deployment has to list them.
    # False means it supports only what it has its own adapter for, and an
    # operator names those in ``agent_harness_models``.
    speaks_gateway: bool = True
    # Can it present an Anthropic subscription credential? That credential is
    # minted for ONE harness; no other can carry it, whatever it can otherwise
    # drive.
    carries_subscription: bool = False
    # Do its sessions keep memory as files the platform can reconcile? The
    # system prompt's memory section is given only to a harness that does: it
    # says 「写进这里，平台下一轮就有一份」, which is a lie for a harness whose
    # files never come back. Claude Code's are files under the session host's
    # `~/.cheese/memory/` (`remote_execution/proxy.js`'s `memoryPath`), and its
    # runner answers the reconciliation.
    keeps_memory: bool = False
    # What a room may ask its session, by subtype, and which of those the
    # room's executor answers rather than the session: the files and commands
    # live on the executor. Each only reads; the room watches its session and
    # never steers it. Empty for a harness with no control channel, which is
    # still a harness: the room then shows less of it.
    controls: tuple[str, ...] = ()
    executor_controls: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """答不全四条的，根本造不出来——这就是「摘掉」的可判形式。

        判在构造上而不是判在一条守卫测试上：注册表是一个字面量，一个造得出来的
        条目总会有人写进去。
        """
        for requirement in SubagentRequirement:
            answer = self.subagents.get(requirement)
            if type(answer) is not str or not answer.strip():
                # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
                raise ValueError(
                    f"{self.name} 没有答「{requirement}」。这是硬性要求（结论 43）："
                    "答得出的骨架才上注册表，答不出的留着代码不注册。"
                    "一条差异码也不算答——硬性要求没有「暂缺」那一档。"
                )
        for capability, answer in self.capabilities.items():
            if type(answer) is not str or not answer.strip():
                # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
                raise ValueError(
                    f"{self.name} 声明了「{capability}」却没说怎么做到的。"
                    "做不到就不写这一项。"
                )


HARNESSES: dict[str, Harness] = {
    CLAUDE_CODE: Harness(
        CLAUDE_CODE,
        "Claude Code",
        subagents={
            SubagentRequirement.SPAWNS_WITH_A_MODEL: (
                "Agent(model=...) selects a native child model. The pinned-binary "
                "test_claude_child_models verifies general-purpose children: explicit "
                "selection overrides CLAUDE_CODE_SUBAGENT_MODEL, which "
                "`agent/chat.py` supplies from the project child default or project "
                "main default. Admission validates "
                "the catalog and tier policy before either supply pool forwards it. "
                "The pinned tool schema says forks inherit the parent model; the "
                "tested startup rejects the fork agent type with a visible tool "
                "error. Fork model selection is not claimed as supported."
            ),
            SubagentRequirement.LABELS_ITS_THREAD: (
                "`agent/harness/claude_code/events.py` 的 `bind`：标识由"
                "`room_task/thread_label.py` 的 `thread_label` 算出来、写在起它的那"
                "段 prompt 里；`bind` 在派发它的那次调用上读到它，钉在这次调用的 id "
                "和 task_started 给这个 worker 的 agent id 上，此后这条子线程的每条"
                "记录——stdout 上带 parent_tool_use_id 的，和 "
                "`agent/harness/claude_code/runner.py` 的 `file_entry` 从它自己的 "
                "transcript 文件读进来的——都带着它出来。"
            ),
            SubagentRequirement.PARENT_RETASKS_IT: (
                "改指令的是起它的父线程，做法写在 "
                "`../../sandbox/skills/cheese/SKILL.md`：还在跑的，父线程直接给"
                "这条子线程发消息；已经停了的，在房间会话里用同一个线程标识重新派"
                "一条——所以换了要求还是那条活、还归那张卡。平台这一侧只有 "
                "`agent/room/sessions.py` 上的 `RoomSessions.steer`，它把人对"
                "卡的操作送进**父**会话，父线程读到之后才去做上面那件事；平台不认"
                "子线程，也不直接对它说话。"
            ),
            SubagentRequirement.PARENT_STOPS_IT: (
                "停的是**一条**子线程，做法和改指令写在同一处 "
                "`../../sandbox/skills/cheese/SKILL.md`：父线程调 TaskStop，按起"
                "它时给的那个名字停那一条，同一条会话里的其他分身照跑。平台这一侧"
                "的 `agent/room/sessions.py` 上 `RoomSessions.interrupt` 与 "
                "`RoomSessions.close` 停的都是整条会话——那是结论 43 的另一句「子 "
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
        capabilities={
            Capability.REMOTE_EXECUTION: (
                "中心机上的会话启动时带一个插件，"
                "`agent/harness/claude_code/remote_execution/client.py` 的 "
                "`write_plugin` 写出它；插件里的函数钩子 "
                "`agent/harness/claude_code/remote_execution/proxy.js` 用 `on` 接住"
                "每一次工具调用，把 Read、Edit、Write、Bash 这些用 `mcp.call` 交给执"
                "行机上的 native 服务去跑，结果原样还给会话，本机不执行。"
            ),
        },
        carries_subscription=True,
        keeps_memory=True,
        # Each is a ``control_request`` on the session's stdin
        # (`scripts/remote_execution/headless_contract.py` checks them against
        # the pinned build), except the file ones the room's executor answers
        # (``routes/agent_control.py``).
        controls=(
            "initialize",
            "file_suggestions",
            "read_file",
            "get_workspace_diff",
            "get_context_usage",
            "get_usage",
            "mcp_status",
        ),
        executor_controls=frozenset(
            {"read_file", "file_suggestions", "get_workspace_diff"}
        ),
    ),
    PI: Harness(
        PI,
        "pi",
        subagents={
            SubagentRequirement.SPAWNS_WITH_A_MODEL: (
                "pi 核心没有子 agent，起它的是平台给 pi 的 extension："
                "`agent/harness/pi/platform.ts` 的 `registerSubagentTools` 给会话一个 "
                "Task(model=...)，runner 在中心机上起第二个 pi，"
                "手在同一台执行机、同一个检出里。模型"
                "由 `agent/harness/pi/subagents.py` 的 `admitted_model` 问平台准入"
                "（/llm/admission，和 Claude Code 分身同一道目录、档位与预算校验），"
                "没指定时答的就是项目的分身默认；`Subagent._configure` 用答出来的模型"
                "照抄父会话的网关 provider，只换模型这一项。"
            ),
            SubagentRequirement.LABELS_ITS_THREAD: (
                "`agent/harness/pi/subagents.py` 的 `Subagent.pull` 把子会话的每条"
                "记录连同 `label_in_text` 从它的 prompt 里读出的线程标识，写进会话自"
                "己的 journal（`agent/harness/pi/runner.py` 的 `note_page`），起停"
                "各记一条 runner 自己的记录；`agent/harness/pi/events.py` 的 "
                "`thread_of` 认出它，`Assembler._subagent` 把标识放在每个事件的 "
                "`thread_label` 上。"
            ),
            SubagentRequirement.PARENT_RETASKS_IT: (
                "改指令的是起它的父线程，做法写在 "
                "`../../sandbox/skills/cheese/SKILL.md`：还在跑的，父线程调 "
                "SendMessage，`agent/harness/pi/subagents.py` 的 `Subagent.send` "
                "把消息 steer 进那条子会话；已经收工或停了的不再接指令，照原来的简报"
                "用同一个线程标识重派。平台这一侧只有 `agent/room/sessions.py` 上"
                "的 `RoomSessions.steer`，把人对卡的操作送进父会话。"
            ),
            SubagentRequirement.PARENT_STOPS_IT: (
                "停的是一条子会话：父线程调 TaskStop，"
                "`agent/harness/pi/subagents.py` 的 `Subagent.stop` 停下那一条，同"
                "一条会话里的其他分身照跑。会话被 interrupt 或关掉时，"
                "`agent/harness/pi/runner.py` 先用 `stop_all` 停掉它起过的每一条"
                "——那是结论 43 的「子 agent 与父进程同生同死」，不是这一条。"
            ),
        },
        capabilities={
            Capability.REMOTE_EXECUTION: (
                "pi 的 read、write、edit、bash、ls、find 都接受注入的文件与进程操作，"
                "平台给 pi 的 extension 在 `agent/harness/pi/platform.ts` 的 "
                "`registerMachineTools` 里把这些操作换成问 runner；grep 的搜索本身不"
                "走注入的操作，同一处把它整个换成执行器上的一次搜索。runner 经 "
                "`agent/harness/pi/machine.py` 的 `Machine` 把每一个文件操作变成执行"
                "器答的一次调用（`control` 的 `files`，"
                "`agent/harness/claude_code/remote_execution/machine_files.py`），"
                "命令变成执行器 shell 控制上的一条命令，走的是 `RemoteClient`，"
                "用到才领机器，本机不执行。"
            ),
        },
    ),
}


def harness_name(name: str | None) -> str:
    """调用方给的 harness 名，没给就是这套部署偏好的第一个。"""
    return name or deployment_harnesses()[0]
