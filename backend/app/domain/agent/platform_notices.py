"""平台在房间里说话的统一契约 —— 一行 `content` + 结构化 `meta`.

## 为什么有这个模块

平台自己在房间里说话曾经有两条路。一条是 `ChatService.post_system_event()`，落
`kind=event, author_type=system`，前端渲染成居中灰字一行。另一条是
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
#: 验收卡被人驳回了 —— 芝士要去改，不是等着。
EVENT_CARD_REJECTED: Final = "card_rejected"
#: 同步上游时合并冲突。
EVENT_UPSTREAM_CONFLICT: Final = "upstream_conflict"
#: A message expected to enter the live session had to return to the queue.
EVENT_DELIVERY_FALLBACK: Final = "delivery_fallback"
#: 轮次失败（`classify_platform_failure()` 没命中的那些）。
EVENT_TURN_FAILED: Final = "turn_failed"
#: 轮次超时 / 一个字都没输出。
EVENT_TURN_TIMEOUT: Final = "turn_timeout"
#: 部署中断了轮次（孤儿轮次扫底）。
EVENT_DEPLOY_INTERRUPTED: Final = "deploy_interrupted"

#: 本模块新增的全部类别码。`platform_error` / `backend_error` / `frontend_error`
#: / `host_swap` / `action` 是别处已有的，不在这里重复登记。
EVENT_TYPES: Final = frozenset(
    {
        EVENT_CI_FAILED,
        EVENT_GATE_FAILED,
        EVENT_GATE_BLOCKED,
        EVENT_GATE_ABANDONED,
        EVENT_MERGE_REFUSED,
        EVENT_ACCEPT_CONFLICT,
        EVENT_CARD_REJECTED,
        EVENT_UPSTREAM_CONFLICT,
        EVENT_DELIVERY_FALLBACK,
        EVENT_TURN_FAILED,
        EVENT_TURN_TIMEOUT,
        EVENT_DEPLOY_INTERRUPTED,
    }
)


def notice(
    event_type: str,
    *,
    severity: str,
    who: str,
    detail: str | None = None,
    detail_label: str | None = None,
) -> dict:
    """一条平台提示的 `meta`。

    五个键**总是**都在，哪怕值是 None —— 读它的一方（前端、测试、以后的别的
    消费者）可以直接取，不用先判断键存不存在。

    `detail` 是要折叠起来的原文，`detail_label` 是展开区的标题（"CI 日志" /
    "检查输出" / "服务原话"…）。给了 detail 却不给 label 是允许的，前端有默认
    标题；反过来给 label 不给 detail 没有意义，但也不报错 —— 这个函数不做校验，
    它只是把契约写成一处。
    """
    return {
        "event_type": event_type,
        "severity": severity,
        "who": who,
        "detail": detail,
        "detail_label": detail_label,
    }


def delivery_fallback_notice() -> tuple[str, dict]:
    """The single room-visible error for live-delivery fallback."""
    return (
        "⚠️ 实时送入当前会话失败，已自动转入正常队列。",
        notice(
            EVENT_DELIVERY_FALLBACK,
            severity=SEVERITY_ERROR,
            who=WHO_PLATFORM,
            detail="消息已保存并保持待处理状态；平台会从正常队列继续处理，无需重发。",
            detail_label="处理说明",
        ),
    )
