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

码本身、以及拼这份 `meta` 的 `notice()`，住在第 1 层的
`app/domain/notice_meta.py`：要用这套码的模块遍布每一层，它们不该为了几个字符串
向上够到第 5 层。这里只留要说给人听的那几句 —— 它们要 block 的行键和记忆树的路径，
本来就待在这一层。

契约本身（字段名、码的语义）是前后端两张卡共用的，改它要回到父话题改，不能单方面
动。
"""

from __future__ import annotations

from app.core.sentences import say
from app.domain.block.models import AGENT_NOTICE_META_KEY
from app.domain.memory.files import (
    conflict_path,
    prompt_path,
    rejected_path,
)
from app.domain.notice_meta import (
    EVENT_DELIVERY_CHECKING,
    EVENT_DELIVERY_FALLBACK,
    EVENT_MEMORY_CHANGED,
    EVENT_TOOLS_RECOVERED,
    EVENT_TURN_QUEUED,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)


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
    unindexed: tuple[str, ...] = (),
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
        for_agent.append(
            memory_rejected_notice(where=where, reasons=rejected, unindexed=unindexed)
        )
    if for_agent:
        meta[AGENT_NOTICE_META_KEY] = "\n\n".join(for_agent)
    return line, meta


def memory_rejected_notice(
    *, where: str, reasons: dict[str, str], unindexed: tuple[str, ...] = ()
) -> str:
    """说给 agent 的那句：哪几条因为太长没存下、为什么、它写的那一版在哪。

    `unindexed` 是其中这一轮新加的索引行也被拿掉的那几条：只对它们说「把那一行
    加回去」。别的被拒的文件索引没动过，叫它加回去只会多出一行重复的。
    """
    lines = "\n".join(
        f"- `{prompt_path(path)}`（你写的那一版在 "
        f"`{prompt_path(rejected_path(path))}`）：{reason}"
        for path, reason in sorted(reasons.items())
    )
    text = (
        f"{where}记忆里有几条超出长度上限，没有写入，记忆里还是原来那一版：\n"
        f"{lines}\n"
        "改短之后重新写入。"
    )
    if unindexed:
        names = "、".join(f"`{prompt_path(path)}`" for path in sorted(unindexed))
        text += (
            f"你这次在 `MEMORY.md` 里为 {names} 新加的那一行也没有留下，"
            "重新写入之后再把它加回去。"
        )
    return text


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
