"""平台在房间里说话的统一契约 —— 一行 `content` + 结构化 `meta`.

## 为什么有这个模块

平台自己在房间里说话曾经有两条路。一条是 `ChatService.post_system_event()`，落
`kind=event, author_type=platform`，前端渲染成居中灰字一行。另一条是
`runner.submit(author="system")`，它落下的其实是
`kind=message, author_type=human, author="system"` —— 一条**伪装成人**的聊天
消息，前端按真人发言渲染：完整气泡、头像、名字显示成 "system"。最长的一条（CI
失败播报）可以往房间里铺 4000 字符。

统一之后，平台说的每一句都是系统事件：`content` 是一行 ≤40 字的人话，自带「出了
什么事」；原话/日志尾巴/traceback 一律收进 `meta.detail`，由前端折叠展示。

## 信息不能丢，只能收起来

这是产品定的硬约束。所以 `detail` 永远是**原文的完整副本**（各调用点原有的截断
上限保持不变），不是摘要，也不能替换成一个「去别处看」的链接 —— 轮次失败的「服务
原话」根本没有第二个副本。

## 后端只发码，文案由前端渲染

`who` 回答的是「谁在管这件事」，它是三个码而不是三句话：`platform`（平台会自己
重试/自愈）、`cheese`（芝士接着处理）、`human`（要人来）。渲染成什么词是前端的
事。这跟 `platform_failures.py` 里已有的 code + copy contract 是同一条规矩 ——
后端拼死文案，前端就再也改不动它。

契约本身（字段名、码的语义）是前后端两张卡共用的，改它要回到父话题改，不能单方面
动。
"""

from __future__ import annotations

from typing import Final

from app.core.sentences import notice_keys, say
from app.domain.block.models import AGENT_NOTICE_META_KEY
from app.domain.memory.files import (
    conflict_path,
    prompt_path,
    rejected_path,
)

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
#: 验收卡的描述被更正了 —— 这次改动会在 main 的历史里说什么，变了。
EVENT_CARD_REDESCRIBED: Final = "card_redescribed"
#: Historical upstream-sync notices remain readable after retiring local sync.
EVENT_UPSTREAM_CONFLICT: Final = "upstream_conflict"

#: 一条消息被升级成了一条活（或一个房间），下一步在它的负责人手上。
EVENT_BLOCK_UPGRADED: Final = "block_upgraded"
#: 一个房间的环境倒了，这件事交到总览芝士手上了。
EVENT_ENVIRONMENT_RECOVERY_REQUEST: Final = "environment_recovery_request"
#: 房间的环境修好了，此前没送达的消息接着处理。
EVENT_ENVIRONMENT_REPAIRED: Final = "environment_repaired"
#: 这个房间的记忆在整理 —— 芝士自己的事，没有人在等它。
EVENT_MEMORY_ORGANIZING: Final = "memory_organizing"
#: 记忆树被改动了（新增/修改/删除，或会话里写的那一版被平台盖了回来）。同样是
#: 芝士自己的事：一条记忆是 agent 写下的一份观察，没有人在等它，所以谁也不点。
#: 改动本身收进 `detail`，按树的归属分别说进项目总览 / 本人的私聊。
EVENT_MEMORY_CHANGED: Final = "memory_changed"
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
#: 采纳后触发的部署，跑完了。
EVENT_DEPLOY_DONE: Final = "deploy_done"
#: 采纳后触发的部署没跑完 / 回滚了 / 结果不明。
EVENT_DEPLOY_FAILED: Final = "deploy_failed"
#: 一件活的提交并进了房间的分支 / 先排队 / 冲突了。
EVENT_ROOM_MERGE: Final = "room_merge"
#: 一张采信卡有结果了：被采信 / 要补证据 / 升级等人拍板。
EVENT_CONCLUSION_SETTLED: Final = "conclusion_settled"
#: 话题的 Cloud 机器还在创建，这条消息先留着。
EVENT_MACHINE_PROVISIONING: Final = "machine_provisioning"
#: 话题绑定的机器连着失败，平台暂停向它派活；话题留在原机器上等人处理。
EVENT_HOST_FAILURE: Final = "host_failure"
#: 房间换了工作电脑，但有已结束任务的文件没能备份，只留在原来那台（它保留文件）上。
EVENT_WORK_LEFT_ON_MACHINE: Final = "work_left_on_machine"
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
#: 一条周期任务或事件触发规则开始了一次执行；结果或失败原因随后另起一条。
EVENT_ROUTINE_RUN: Final = "routine_run"
EVENT_ROUTINE_RESULT: Final = "routine_result"
#: 芝士起草了一条周期任务/触发规则，等人确认后才会执行。
EVENT_ROUTINE_PROPOSED: Final = "routine_proposed"
#: 房间归档了，它里面还在执行的规则随之停下。规则自己的状态不变（取消归档后从下一
#: 个时刻继续），所以这一行说的是房间带走了什么，不是规则被改成了什么。
EVENT_ROUTINE_STOPPED: Final = "routine_stopped"
#: AI 服务的一次请求失败了，会话正在按它自己的退避重试。重试期间房间里没有任何
#: 输出，不说一声就和「在想」分不出来。同一段连续的重试只占一行，次数原地更新。
EVENT_API_RETRY: Final = "api_retry"
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
#: 交活的人自己的 GitHub 授权开不了 PR，平台改用 App 的身份开了 —— PR 记在机器人
#: 名下。以前这只进 logger，于是这个人只看到 GitHub 把他的活算给了机器人。
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
        EVENT_ENVIRONMENT_RECOVERY_REQUEST,
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
        EVENT_ROOM_MERGE,
        EVENT_CONCLUSION_SETTLED,
        EVENT_MACHINE_PROVISIONING,
        EVENT_HOST_FAILURE,
        EVENT_WORK_LEFT_ON_MACHINE,
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
        "backend_error",
        "frontend_error",
    }
)


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


def tools_recovered_notice() -> tuple[str, dict]:
    """工具断了是平台的事，平台自己接回来并重发；房间里的人不用动手。"""
    return say("toolsRecovered"), notice(
        EVENT_TOOLS_RECOVERED,
        severity=SEVERITY_WARN,
        who=WHO_PLATFORM,
        detail=say("toolsRecoveredDetail"),
        detail_label=say("labelNote"),
    )


def catching_up_notice() -> tuple[str, dict]:
    """等的是这段对话自己的记录接回来，平台自己会往前推，没人需要动手。"""
    return say("catchingUp"), notice(
        EVENT_TURN_QUEUED,
        severity=SEVERITY_INFO,
        who=WHO_PLATFORM,
        detail=say("catchingUpDetail"),
        detail_label=say("labelWhatHappensNext"),
    )


def delivery_checking_notice() -> tuple[str, dict]:
    """A message whose write into the running session is still being
    checked: it went in once, and will not be sent twice."""
    return (
        say("deliveryChecking"),
        notice(EVENT_DELIVERY_CHECKING, severity=SEVERITY_INFO, who=WHO_PLATFORM),
    )


def delivery_fallback_notice() -> tuple[str, dict]:
    """A message that could not enter the running session went to the queue:
    the platform handles it from there."""
    return (
        say("deliveryFallback"),
        notice(
            EVENT_DELIVERY_FALLBACK,
            severity=SEVERITY_WARN,
            who=WHO_PLATFORM,
            detail=say("deliveryFallbackDetail"),
            detail_label=say("labelNote"),
        ),
    )


def memory_changed_notice(
    *,
    where: str,
    summary: str,
    diff: str,
    refused: dict[str, str],
    rejected: dict[str, str] | None = None,
) -> tuple[str, dict]:
    """记忆树的一次改动：一行说改了哪一棵、改了几条，diff 收进 `detail`。

    `where` 是那棵树的名字（「项目共享」/「你的私人」）。`refused` 非空是说会话里
    写的那几版被平台这一份盖回来了（路径 → 会话那一版的正文，空串是它把这条删
    了）—— 它得重读再写，否则下一轮写的还是它刚才那一版。这句话只说进那棵树自己
    的房间：同一句带 diff 的话说进总览，就是把一个人的偏好广播给了整个项目。

    **被盖回去这件事必须进 `agent_notice`**（`AGENT_NOTICE_META_KEY`）。那条灰字
    事件是给人看的，agent 一个字的 prompt 都读不到它：写记忆的 agent 在会话机上，
    它看到的世界就是那棵树，而它刚才写的那一版已经不在了。不说，它会以为写成功
    了、下一轮再写一遍同一版，而每一轮都会被盖回去。这一句落在哪间房由调用方
    定（`queries._say_memory_change`）：写它的 agent 所在的那一间，不一定是这
    棵树的房间。
    """
    rejected = rejected or {}
    line = (
        say("memoryChanged", where=where, summary=summary)
        if summary
        else say("memoryChangedBare", where=where)
    )
    if refused:
        line = say("wideJoin", first=line, second=say("memoryRefused"))
    if rejected:
        line = say(
            "wideJoin",
            first=line,
            second=say("memoryRejected", count=len(rejected)),
        )
    meta = notice(
        EVENT_MEMORY_CHANGED,
        severity=SEVERITY_INFO,
        who=WHO_PLATFORM,
        detail=diff or None,
        detail_label=say("labelChanges"),
    )
    for_agent = []
    if refused:
        for_agent.append(memory_conflict_notice(where=where, refused=refused))
    if rejected:
        for_agent.append(memory_rejected_notice(where=where, reasons=rejected))
    if for_agent:
        meta[AGENT_NOTICE_META_KEY] = "\n\n".join(for_agent)
    return line, meta


def memory_rejected_notice(*, where: str, reasons: dict[str, str]) -> str:
    """说给 agent 的那句：哪几条因为太长没存下、为什么、它写的那一版在哪。"""
    lines = "\n".join(
        f"- `{prompt_path(path)}`（你写的那一版在 "
        f"`{prompt_path(rejected_path(path))}`）：{reason}"
        for path, reason in sorted(reasons.items())
    )
    return (
        f"{where}记忆里有几条超出长度上限，没有写入，记忆里还是原来那一版：\n"
        f"{lines}\n"
        "改短之后重新写入。"
    )


def memory_conflict_notice(*, where: str, refused: dict[str, str]) -> str:
    """说给 agent 的那句：哪几条被平台版盖了、去哪儿找它刚写的那一版。

    点名到条是为了让它下一步就能动手：一句「有改动被盖了」它得先猜是哪一条，而
    猜错的那一次是把别的记忆又覆盖一遍。路径按它那一侧的说法写全
    （`files.prompt_path`）：写成 `team/a.md`，它手里的文件工具把这次 Read 发给
    工作机，那里没有记忆树，读回来是「文件不存在」。

    正文为空的那几条（`memory.tree.REMOVED`：它把这条删了，平台那一份也动过）
    没有副本：`runner._keep_refused` 不落空文件，所以不能指一个旁路文件给它。
    """
    lines = []
    for path in sorted(refused):
        if refused[path]:
            lines.append(
                f"- `{prompt_path(path)}`（你写的那一版在 "
                f"`{prompt_path(conflict_path(path))}`）"
            )
        else:
            lines.append(
                f"- `{prompt_path(path)}`（你那一版是删掉它，平台这一版留着，没有副本）"
            )
    body = "\n".join(lines)
    return (
        f"{where}记忆里有几条被平台的版本盖回去了——平台这一份也动过它们，"
        "按规矩平台赢。下面每一条都是你刚才动的、现在不是你以为的那一版了：\n"
        f"{body}\n"
        "**重读它们，把你要写的东西重新写进去。**上面给了副本的，从旁边那个 "
        "`.conflict.md` 里取回你要写的内容，别整个文件照抄回去；上面说没有副本的"
        "那几条，读一下平台这一版再决定还删不删。这几条你手里的副本已经旧了，"
        "照旧的写只会再被盖一次。"
    )
