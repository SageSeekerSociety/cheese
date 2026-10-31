"""平台提示的 `meta`：里面的码，和拼这份 meta 的 `notice()`。

平台在房间里说的每一句都带一份结构化 `meta`，形状就是 `notice()` 答的那几个键。
`severity` / `who` / `event_type` 是**码**，不是文案 —— `who` 回答的是「谁在管这件
事」（平台会自己重试/自愈、芝士接着处理、要人来），渲染成什么词是前端的事。这跟
`platform_failures.py` 里已有的 code + copy contract 是同一条规矩。要说给人听的那
几句话（工具接回来了、记忆被平台版盖了……）怎么拼，在 `agent/platform_notices.py`。

## 为什么住在第 1 层

写这份 meta 的是每一层：第 1 层的前端报错摄入（`frontend_log`）、第 2 层的策略提案
（`policy`）和房间文件（`project`）、第 3 层的额度（`usage`）、第 4 层的采纳与合并
（`review`）、第 6 层的编排（`routine`）。这套词汇原来住在第 5 层的
`agent/platform_notices.py`，于是每一个只想要几个码的模块都得向上够四层，C5 分层契
约里冻着一批这样的边。

同一件事在随附技能的词汇上已经做过一次（`skill_library` 从 `agent.skills` 拆到第 1
层）：低层要用的那套词汇搬到低层，读它的人改读向下的边。这里搬的是平台提示契约里
**数据**的那一半 —— 码，和拼 meta 的 `notice()`。`platform_notices` 留下要说给人听
的那几句：它们要 block 的行键和记忆树的路径，还留在第 5 层。

契约本身（字段名、码的语义）是前后端两张卡共用的，改它要回到父话题改，不能单方面
动。
"""

from __future__ import annotations

from typing import Final

from app.core.sentences import notice_keys

# --- severity ---------------------------------------------------------------
SEVERITY_INFO: Final = "info"
SEVERITY_WARN: Final = "warn"
SEVERITY_ERROR: Final = "error"

# --- who: 谁在管这件事（码，不是文案）---------------------------------------
#: 平台会自己重试/自愈，没人需要动手。
WHO_PLATFORM: Final = "platform"
#: 芝士接着处理（多半下一轮就在修）。
WHO_CHEESE: Final = "cheese"
#: 平台和芝士都到头了，等人。
WHO_HUMAN: Final = "human"

# --- event_type: 类别码 ------------------------------------------------------
#: PR 的 CI 没过。
EVENT_CI_FAILED: Final = "ci_failed"
#: 质量闸门跑了，没通过 —— 芝士要去改代码。
EVENT_GATE_FAILED: Final = "gate_failed"
#: 质量闸门**没跑起来** —— 对代码没有任何结论，芝士要去弄环境。跟上面一条分开是
#: 刻意的：合并了它们，芝士就会对着一份「什么都没跑」的输出找 bug。
EVENT_GATE_BLOCKED: Final = "gate_blocked"
#: 闸门结果丢失（后端重启 / 任务丢了），卡被判死。同样不是「检查没通过」。
EVENT_GATE_ABANDONED: Final = "gate_abandoned"
#: PR 全绿，但 GitHub 拒绝合并。
EVENT_MERGE_REFUSED: Final = "merge_refused"
#: 采纳时合并冲突。
EVENT_ACCEPT_CONFLICT: Final = "accept_conflict"
#: 验收卡递上来了 —— 从这一刻起验收人手上多了一件事。
EVENT_CARD_FILED: Final = "card_filed"
#: 这次交付在产物清单上新建了一项 —— 名字此前没出现过，看一眼是不是要的那个。
EVENT_ARTIFACT_DECLARED: Final = "artifact_declared"
#: 房间里的一份东西被留进了资料库 —— 从这一刻起别的房间也引用得到它。
EVENT_LIBRARY_SAVED: Final = "library_saved"
#: 验收卡被人驳回了 —— 芝士要去改，不是等着。
EVENT_CARD_REJECTED: Final = "card_rejected"
#: 验收卡被作废 —— 不是驳回：没人对代码下过判断，卡只是被收尾了。
EVENT_CARD_VOIDED: Final = "card_voided"
#: 验收卡的描述或交付物被更正了 —— 这次改动会在 main 的历史里说什么，或者
#: 验收人要审的那一份，变了。
EVENT_CARD_REDESCRIBED: Final = "card_redescribed"
#: Historical upstream-sync notices remain readable after retiring local sync.
EVENT_UPSTREAM_CONFLICT: Final = "upstream_conflict"

#: 一条消息被升级成了一条活（或一个房间），下一步在它的负责人手上。
EVENT_BLOCK_UPGRADED: Final = "block_upgraded"
#: 频道的工作环境没准备好：在等的人去项目设置的「环境」里看、改、重试。
EVENT_ENVIRONMENT_FAILED: Final = "environment_failed"
#: 房间的环境修好了，此前没送达的消息接着处理。
EVENT_ENVIRONMENT_REPAIRED: Final = "environment_repaired"
#: 这个房间的记忆在整理 —— 芝士自己的事，没有人在等它。
EVENT_MEMORY_ORGANIZING: Final = "memory_organizing"
#: 记忆树被改动了（新增/修改/删除，或会话里写的那一版被平台盖了回来）。同样是
#: 芝士自己的事：一条记忆是 agent 写下的一份观察，没有人在等它，所以谁也不点。
#: 改动本身收进 `detail`，按树的归属分别说进项目总览 / 本人的私聊。
EVENT_MEMORY_CHANGED: Final = "memory_changed"
#: Someone edited a message the room's agent had already read; the agent is told,
#: and the members read the same line in 现场.
EVENT_MESSAGE_EDITED: Final = "message_edited"
#: A message written into the running session whose arrival is still being
#: checked. It is not sent again.
EVENT_DELIVERY_CHECKING: Final = "delivery_checking"
#: A message expected to enter the live session had to return to the queue.
EVENT_DELIVERY_FALLBACK: Final = "delivery_fallback"
#: 轮次失败（`classify_platform_failure()` 没命中的那些）。
EVENT_TURN_FAILED: Final = "turn_failed"
#: 轮次超时 / 一个字都没输出。
EVENT_TURN_TIMEOUT: Final = "turn_timeout"
#: 部署中断了轮次（孤儿轮次扫底）。
EVENT_DEPLOY_INTERRUPTED: Final = "deploy_interrupted"
#: 有工具调用发出去了而结果永远不会回来了 —— 平台不替它猜做没做过，也不自动重发
#: （结论 57）。要人看一眼那件事到底落地没有。
EVENT_DISPATCH_UNKNOWN: Final = "dispatch_unknown"
#: 项目并发已满，这轮在排队。
EVENT_TURN_QUEUED: Final = "turn_queued"
#: 房间的平台工具通道断了，回复没能发进来 —— 平台接回来并重发了那条消息。
EVENT_TOOLS_RECOVERED: Final = "tools_recovered"
#: 话题的运行环境被重建 —— 会话和后台任务都断了，项目文件没事。
EVENT_SANDBOX_REBUILT: Final = "sandbox_rebuilt"
#: 任务合并后，包含这次合并的一次部署成功了。合并后的观察已经删掉，只留给旧任务里的行。
EVENT_DEPLOY_DONE: Final = "deploy_done"
#: 任务合并后，包含这次合并的一次部署失败了。同上，只留给旧行。
EVENT_DEPLOY_FAILED: Final = "deploy_failed"
#: 任务合并到默认分支后，合并提交上的一项检查失败了。合并后的观察已经删掉，
#: 只留给旧任务里的行。
EVENT_MAIN_CHECKS_FAILED: Final = "main_checks_failed"
#: 一件活的提交并进了房间的分支 / 先排队 / 冲突了。
EVENT_ROOM_MERGE: Final = "room_merge"
#: 一张采信卡有结果了：被采信 / 要补证据 / 升级等人拍板。
EVENT_CONCLUSION_SETTLED: Final = "conclusion_settled"
#: 话题的 Cloud 机器还在创建，这条消息先留着。
EVENT_MACHINE_PROVISIONING: Final = "machine_provisioning"
#: 话题绑定的机器连着失败，平台暂停向它派活；话题留在原机器上等人处理。
EVENT_HOST_FAILURE: Final = "host_failure"
#: 房间工作目录里不在任何仓库里的旧文件另存了一份，限期保留，到期删除。
EVENT_ROOM_FILES_KEPT: Final = "room_files_kept"
#: 结论结算了，但这个话题的归档欠着 —— 它还挂着一张没决议的验收卡。
EVENT_ARCHIVE_DEFERRED: Final = "archive_deferred"
#: 这次交付完成了。
EVENT_ACCEPT_DONE: Final = "accept_done"
#: 采纳没走完，停在半路 —— 开不出 PR、PR 合不上、工作区合并出错。
EVENT_ACCEPT_STOPPED: Final = "accept_stopped"
#: PR 满足项目的合并规则了（CLEAN）——通知验收人来采纳。
EVENT_ACCEPT_READY: Final = "accept_ready"
#: 新提交作废了已有的采纳批准（分支保护的 dismiss_stale，默认开）。
EVENT_ACCEPT_DISMISSED: Final = "accept_dismissed"
#: 机器在这张卡上没有可走的下一步（必跑检查迟迟没报到、反复换基追不上 main、
#: 布防了自动合但票数不够），扣住不动，等人来定。
EVENT_MERGE_WITHHELD: Final = "merge_withheld"
#: PR 在 GitHub 上被关掉且没合并。
EVENT_PR_CLOSED: Final = "pr_closed"
#: 有人明知检查没绿仍然放行合并，署了名。
EVENT_FORCE_MERGED: Final = "force_merged"
#: 另一张未决的验收卡也新建了迁移，合到一起会把迁移链分叉。
EVENT_MIGRATION_COLLISION: Final = "migration_collision"
#: 同一批消息反复被重投进轮次，前面几次都没跑完。
EVENT_PROMPT_REPLAYED: Final = "prompt_replayed"
#: 有人在 PR 上留了评审意见 / 要求改动 —— 芝士要去改，不是等着。
EVENT_PR_REVIEW: Final = "pr_review"
#: PR 和它的 base 分支冲突了，GitHub 合不了。
EVENT_PR_CONFLICT: Final = "pr_conflict"
# A parent task closed; its dependants need the executor to inspect their base.
EVENT_DEPENDENCY_CLOSED: Final = "dependency_closed"
EVENT_DEPENDENCY_REJECTED: Final = "dependency_rejected"
#: 一次调用撞上项目的档位策略，变成了给人的一条提议（`domain/policy/gate.py`）。
EVENT_POLICY_PROPOSAL: Final = "policy_proposal"
#: 到点了 —— 这一轮是这条线程自己当初请平台在这个时刻递给它的（结论 17）。
EVENT_TIMED_DELIVERY: Final = "timed_delivery"
#: 芝士把一次做法整理成了项目技能，等人确认后才保存、才下发。
EVENT_SKILL_PROPOSED: Final = "skill_proposed"
#: 芝士在某人的邮箱里写好了一封草稿；发不发由邮箱主人确认。
EVENT_MAIL_DRAFTED: Final = "mail_drafted"
#: 那封草稿的下落：邮箱主人发出了、发送失败了，或者放弃了。房间里的确认卡片据此
#: 改成终态。
EVENT_MAIL_RESULT: Final = "mail_result"
#: 一条例行事务开始了一次执行；结果或失败原因随后另起一条。
EVENT_ROUTINE_RUN: Final = "routine_run"
EVENT_ROUTINE_RESULT: Final = "routine_result"
#: 芝士起草了一条例行事务，等人确认后才会执行。
EVENT_ROUTINE_PROPOSED: Final = "routine_proposed"
#: 房间归档了，它里面还在执行的规则随之停下。规则自己的状态不变（取消归档后从下一
#: 个时刻继续），所以这一行说的是房间带走了什么，不是规则被改成了什么。
EVENT_ROUTINE_STOPPED: Final = "routine_stopped"
#: AI 服务的一次请求失败了，会话正在按它自己的退避重试。重试期间房间里没有任何
#: 输出，不说一声就和「在想」分不出来。同一段连续的重试只占一行，次数原地更新。
EVENT_API_RETRY: Final = "api_retry"
EVENT_TURN_RESUME_WAIT: Final = "turn_resume_wait"
EVENT_TURN_RESUME_STARTED: Final = "turn_resume_started"
EVENT_TURN_RESUME_STOPPED: Final = "turn_resume_stopped"
#: 会话在压缩上下文：对话太长，模型接下一句之前要先把前面的内容整理成摘要。这期间
#: 房间里没有任何输出，也不回新消息，不说一声就和会话挂了分不出来。开始时落一行，
#: 结束时同一行改成已完成或失败（`meta.state = "over"`）。
EVENT_CONTEXT_COMPACT: Final = "context_compact"
#: 这一轮开着，而跑它的机器够不着（离线、会话进程还没起来、连接在换）。平台在等
#: 它回来；回来了同一行改成已恢复（`meta.state = "over"`）。
EVENT_DEVICE_WAITING: Final = "device_waiting"
#: 平台请房间里刚干过活的队友补第一版实况文档。频道不再有实况文档，平台也不再
#: 这样提醒；这个值留给已经落在时间线上的那些行。
EVENT_DOC_MISSING: Final = "doc_missing"
#: 项目 `.mcp.json` 里的一个远程 MCP 服务器还没连接（或要重新连接、缺一个值），这
#: 个房间的会话用不了它。每个房间每个服务器只说一次：要做的事在项目设置里，不在
#: 这一轮里，说第二遍不会让它更快发生。`meta.server` 是服务器名。
EVENT_MCP_NOT_CONNECTED: Final = "mcp_not_connected"
#: AI 队友之间的点名在这间房一小时里到了上限，这一次没有叫醒被点名的队友
#: （`delivery/mention.py` 的熔断）。说出来，是因为不说的话它和「点名没用」分不开。
EVENT_MENTION_FUSED: Final = "mention_fused"
#: 一个人在「改动」里改了任务目录里的一个文件 —— AI 队友手里的旧内容作废，下一次
#: 动它之前要重读。
EVENT_FILE_EDITED: Final = "file_edited"
#: 有人改了一项成员能改的设置（`app/api/member_settings.py`），说在项目的综合里。
#: `meta.setting` 是哪一项，`meta.from` / `meta.to` 是改前改后存下的值，`meta.by`
#: 是改的人或 AI 队友；改的是一位 AI 队友的设置时，`meta.agent` 是它的 handle。
#: 这一行就是这次改动的记录，没有另一张表。
EVENT_SETTING_CHANGED: Final = "setting_changed"
#: AI 队友的运行程序正在下载到一个人自己的电脑上（`machine/install_progress.py`）。
#: 一台电脑在一段对话里只占一行，下载的进度原地更新，装好或停下时同一行改成结果
#: （`meta.state`）。
EVENT_EXECUTOR_INSTALL: Final = "executor_install"
#: 本模块新增的全部类别码。`platform_error` / `backend_error` / `frontend_error`
#: / `host_failure` / `action` 是别处已有的，不在这里重复登记。
EVENT_TYPES: Final = frozenset(
    {
        EVENT_CI_FAILED,
        EVENT_GATE_FAILED,
        EVENT_GATE_BLOCKED,
        EVENT_GATE_ABANDONED,
        EVENT_MERGE_REFUSED,
        EVENT_ACCEPT_CONFLICT,
        EVENT_CARD_FILED,
        EVENT_ARTIFACT_DECLARED,
        EVENT_LIBRARY_SAVED,
        EVENT_CARD_REJECTED,
        EVENT_CARD_VOIDED,
        EVENT_CARD_REDESCRIBED,
        EVENT_UPSTREAM_CONFLICT,
        EVENT_BLOCK_UPGRADED,
        EVENT_ENVIRONMENT_FAILED,
        EVENT_ENVIRONMENT_REPAIRED,
        EVENT_MEMORY_ORGANIZING,
        EVENT_MEMORY_CHANGED,
        EVENT_DELIVERY_CHECKING,
        EVENT_DELIVERY_FALLBACK,
        EVENT_TURN_FAILED,
        EVENT_TURN_TIMEOUT,
        EVENT_DEPLOY_INTERRUPTED,
        EVENT_DISPATCH_UNKNOWN,
        EVENT_ARCHIVE_DEFERRED,
        EVENT_TURN_QUEUED,
        EVENT_SANDBOX_REBUILT,
        EVENT_DEPLOY_DONE,
        EVENT_DEPLOY_FAILED,
        EVENT_MAIN_CHECKS_FAILED,
        EVENT_ROOM_MERGE,
        EVENT_CONCLUSION_SETTLED,
        EVENT_MACHINE_PROVISIONING,
        EVENT_HOST_FAILURE,
        EVENT_ROOM_FILES_KEPT,
        EVENT_ACCEPT_DONE,
        EVENT_ACCEPT_STOPPED,
        EVENT_ACCEPT_READY,
        EVENT_ACCEPT_DISMISSED,
        EVENT_MERGE_WITHHELD,
        EVENT_PR_CLOSED,
        EVENT_FORCE_MERGED,
        EVENT_MIGRATION_COLLISION,
        EVENT_PROMPT_REPLAYED,
        EVENT_PR_REVIEW,
        EVENT_PR_CONFLICT,
        EVENT_DEPENDENCY_CLOSED,
        EVENT_DEPENDENCY_REJECTED,
        EVENT_POLICY_PROPOSAL,
        EVENT_TIMED_DELIVERY,
        EVENT_SKILL_PROPOSED,
        EVENT_MAIL_DRAFTED,
        EVENT_MAIL_RESULT,
        EVENT_ROUTINE_RUN,
        EVENT_ROUTINE_RESULT,
        EVENT_ROUTINE_PROPOSED,
        EVENT_ROUTINE_STOPPED,
        EVENT_API_RETRY,
        EVENT_CONTEXT_COMPACT,
        EVENT_DEVICE_WAITING,
        EVENT_DOC_MISSING,
        EVENT_MCP_NOT_CONNECTED,
        EVENT_MENTION_FUSED,
        EVENT_FILE_EDITED,
        EVENT_SETTING_CHANGED,
        EVENT_EXECUTOR_INSTALL,
    }
)


#: 平台运行中的事：不在对话里说，记成运行记录（`run_record`），在现场和管理后台
#: 看。读聊天的人用不着它们；想知道这一轮为什么慢的人去现场看。
#: `post_system_event` 遇到这几类自动改记，不用每个调用点自己判断。
RUN_RECORD_EVENTS: Final = frozenset(
    {
        EVENT_TURN_QUEUED,
        EVENT_DELIVERY_CHECKING,
        EVENT_DELIVERY_FALLBACK,
        EVENT_TOOLS_RECOVERED,
        EVENT_PROMPT_REPLAYED,
        EVENT_API_RETRY,
        EVENT_CONTEXT_COMPACT,
        EVENT_DEVICE_WAITING,
        EVENT_TIMED_DELIVERY,
        "cloud_startup",
        "cloud_provisioning",
        "sandbox_asleep",
        EVENT_MEMORY_CHANGED,
        EVENT_MESSAGE_EDITED,
        "backend_error",
        "frontend_error",
    }
)


# --- 给人看不看、看成什么样 ---------------------------------------------------
#
# 频道里只放三样东西：人和 AI 队友说的话，AI 队友交出来的东西，AI 队友没法开口时
# 平台替它说的话。判据是「一个人类同事做同样的事，会不会把这句话发到频道里」；平台
# 自己能处理的，一律不说。这张表在写下一行的时候答「给不给人看」（不给看的写成
# `meta.in_room = false`，记录照写：侧栏的「卡住」、去重、原地更新都按类型读它），
# 在读出的时候答「看成什么样」（`BlockOut.shown_as`），前端只照这个码画，不再自己
# 按类型分。旧的行不迁移：读出时按同一张表算，和新写的一样。

#: 不在对话里给人看。
SHOWN_NOT: Final = "hidden"
#: 附在 AI 队友这次工作的消息下面：这次改了什么。
SHOWN_RESULT: Final = "result"
#: AI 队友交出来的一样东西，画成卡片放在这次回复的最后。
SHOWN_CARD: Final = "card"
#: 平台替 AI 队友说：它没法开口，而这件事要人来处理。
SHOWN_FOR_AGENT: Final = "for_agent"
#: 频道里发生的事：人做的操作，和人关心的最终结果。居中一行。
SHOWN_HAPPENING: Final = "happening"
#: 一个人说的话：退回时写下的理由，画成那个人的一条消息。
SHOWN_PERSON: Final = "person"

#: 下一步在谁手上，就看成什么：等人的由平台替 AI 队友说出来，AI 队友或平台自己会处
#: 理的不说（状态在采纳横条、工作状态行上）。
_BY_WHO: Final = frozenset(
    {
        EVENT_CI_FAILED,
        EVENT_GATE_FAILED,
        EVENT_GATE_BLOCKED,
        EVENT_GATE_ABANDONED,
        EVENT_MERGE_REFUSED,
        EVENT_ACCEPT_CONFLICT,
        EVENT_UPSTREAM_CONFLICT,
        EVENT_PR_REVIEW,
        EVENT_PR_CONFLICT,
        EVENT_MIGRATION_COLLISION,
        EVENT_MAIN_CHECKS_FAILED,
        EVENT_ROOM_MERGE,
        EVENT_DEPLOY_INTERRUPTED,
    }
)

#: 平台正在自己处理的事故（`platform_error` 的 `code`）：处理完不用人做什么。
_PLATFORM_HANDLING: Final = frozenset({"storage_exhausted", "runtime_image_missing"})

#: 其余类别码各自看成什么。不在表里的类别码（别处新加的）按「频道里发生的事」画。
_SHOWN_AS: Final[dict[str, str]] = {
    # 平台自己的运行：排队、重试、整理上下文、环境起停、送达核对。
    **dict.fromkeys(RUN_RECORD_EVENTS, SHOWN_NOT),
    EVENT_MACHINE_PROVISIONING: SHOWN_NOT,
    EVENT_ENVIRONMENT_REPAIRED: SHOWN_NOT,
    EVENT_SANDBOX_REBUILT: SHOWN_NOT,
    EVENT_EXECUTOR_INSTALL: SHOWN_NOT,
    EVENT_MEMORY_ORGANIZING: SHOWN_NOT,
    EVENT_TURN_RESUME_WAIT: SHOWN_NOT,
    EVENT_TURN_RESUME_STARTED: SHOWN_NOT,
    EVENT_MENTION_FUSED: SHOWN_NOT,
    EVENT_ROOM_FILES_KEPT: SHOWN_NOT,
    "sandbox_archives_kept": SHOWN_NOT,
    EVENT_ROUTINE_RUN: SHOWN_NOT,
    EVENT_ROUTINE_STOPPED: SHOWN_NOT,
    EVENT_DOC_MISSING: SHOWN_NOT,
    "unresolved_reference": SHOWN_NOT,
    # 交付流程上的状态：在采纳横条上，不在对话里。
    EVENT_CARD_FILED: SHOWN_NOT,
    EVENT_ACCEPT_READY: SHOWN_NOT,
    EVENT_ACCEPT_DISMISSED: SHOWN_NOT,
    EVENT_ARTIFACT_DECLARED: SHOWN_NOT,
    EVENT_DEPENDENCY_CLOSED: SHOWN_NOT,
    EVENT_DEPENDENCY_REJECTED: SHOWN_NOT,
    # 草稿的下落画在草稿卡片上。
    EVENT_MAIL_RESULT: SHOWN_NOT,
    # AI 队友交出来、等人读过再定的东西。
    EVENT_SKILL_PROPOSED: SHOWN_CARD,
    EVENT_ROUTINE_PROPOSED: SHOWN_CARD,
    EVENT_MAIL_DRAFTED: SHOWN_CARD,
    # AI 队友停下了，要人来。
    EVENT_TURN_FAILED: SHOWN_FOR_AGENT,
    EVENT_TURN_TIMEOUT: SHOWN_FOR_AGENT,
    EVENT_TURN_RESUME_STOPPED: SHOWN_FOR_AGENT,
    EVENT_DISPATCH_UNKNOWN: SHOWN_FOR_AGENT,
    EVENT_HOST_FAILURE: SHOWN_FOR_AGENT,
    EVENT_ENVIRONMENT_FAILED: SHOWN_FOR_AGENT,
    EVENT_MCP_NOT_CONNECTED: SHOWN_FOR_AGENT,
    EVENT_POLICY_PROPOSAL: SHOWN_FOR_AGENT,
    EVENT_ACCEPT_STOPPED: SHOWN_FOR_AGENT,
    EVENT_MERGE_WITHHELD: SHOWN_FOR_AGENT,
    EVENT_DEPLOY_FAILED: SHOWN_FOR_AGENT,
    "platform_error": SHOWN_FOR_AGENT,
}

#: 旧的动作行里只记了一笔、现在不再给人看的那几种（`meta.action`）。
_UNSHOWN_ACTIONS: Final = frozenset({"decision", "milestone"})


def shown_as(meta: dict | None, *, in_turn: bool) -> str:
    """一行平台事件在对话里看成什么（上面那几个 `SHOWN_*` 码）。

    ``in_turn``：这一行落在一位 AI 队友的某一次工作里（带轮次号）。一份文档的改动
    在它自己那次工作里是那次工作的结果，不在哪一次工作里（人改的）是频道里发生的事。
    """
    m = meta or {}
    event_type = m.get("event_type")
    if event_type in _BY_WHO:
        return SHOWN_FOR_AGENT if m.get("who") == WHO_HUMAN else SHOWN_NOT
    if event_type == "platform_error" and m.get("code") in _PLATFORM_HANDLING:
        return SHOWN_NOT
    if event_type == EVENT_CARD_REJECTED:
        return SHOWN_PERSON if m.get("detail") else SHOWN_HAPPENING
    if isinstance(event_type, str) and event_type in _SHOWN_AS:
        return _SHOWN_AS[event_type]
    action = m.get("action")
    if action == "doc":
        if m.get("doc_created") is True:
            return SHOWN_CARD
        return SHOWN_RESULT if in_turn else SHOWN_HAPPENING
    if action in _UNSHOWN_ACTIONS:
        return SHOWN_NOT
    if m.get("changeset") is not None:
        return SHOWN_RESULT
    return SHOWN_HAPPENING


def shaped(meta: dict | None, *, in_turn: bool) -> dict | None:
    """写下一行平台事件之前：不给人看的打上 `in_room = false`。

    只往「不给看」那边打：调用方自己说了不露面的照旧不露面。"""
    if shown_as(meta, in_turn=in_turn) != SHOWN_NOT:
        return meta
    return {**(meta or {}), "in_room": False}


def notice(
    event_type: str,
    *,
    severity: str,
    who: str,
    detail: str | None = None,
    detail_label: str | None = None,
    retryable: bool = False,
) -> dict:
    """一条平台提示的 `meta`。

    这五个键**总是**都在，哪怕值是 None —— 读它的一方（前端、测试、以后的别的
    消费者）可以直接取，不用先判断键存不存在。

    `detail` 是要折叠起来的原文，`detail_label` 是展开区的标题（"CI 日志" /
    "检查输出" / "服务原话"…）。给了 detail 却不给 label 是允许的，前端有默认
    标题；反过来给 label 不给 detail 没有意义，但也不报错 —— 这个函数不做校验，
    它只是把契约写成一处。

    `retryable` 说的是「人现在点一下重试有没有用」：房间会在这条提示上给一个
    重试按钮，而不是让人自己去 @ 队友。只在为真时才写进去。
    """
    return {
        "event_type": event_type,
        "severity": severity,
        "who": who,
        "detail": detail,
        "detail_label": detail_label,
        **({"retryable": True} if retryable else {}),
        **notice_keys(detail=detail, detail_label=detail_label),
    }
