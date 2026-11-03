"""The lines Claude Code prints into the conversation as if the agent said
them, turned into the platform's own notices.

A line saying that calling the model failed (a busy or unreachable service, a
quota, the platform's way to the model) is not one of these: a failed turn
says only that the model call went wrong (`turn.state.notices`).
"""

from app.core.sentences import say
from app.domain.agent.platform_failures import (
    RESPONSE_TRUNCATED_CODE,
    TOOL_UNAVAILABLE_CODE,
    classify_cli_notice,
)
from app.domain.notice_meta import (
    EVENT_TURN_FAILED,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)

# CLI 自己印在对话里的那几句英文,换成平台自己的中文提示卡。
#
# 它们过去顶着芝士的名字发出来,读的人看到的是「芝士在说英文报错」,而实际上
# 芝士根本没说话 —— 是它脚下的 CLI 印的。归属错了比语言错了更糟:一个平台故障
# 被读成 AI 的回答,谁也不知道该找谁。
#
# 英文原话一个字都不丢,收进「服务原话」的折叠区 —— 它是唯一的一份。
#
# 每一条是 (那一行的键, severity, who, 说明的键)，句子在 roomNotice 词表。
_CLI_NOTICE_COPY: dict[str, tuple[str, str, str, str]] = {
    TOOL_UNAVAILABLE_CODE: (
        "cliToolUnavailable",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "cliToolUnavailableHint",
    ),
    RESPONSE_TRUNCATED_CODE: (
        "cliResponseTruncated",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "cliResponseTruncatedHint",
    ),
}


#: 等一等、再来一次就可能好的那几种。工具配置错了，重试不会有变化。
_CLI_RETRYABLE = frozenset({RESPONSE_TRUNCATED_CODE})


def cli_notice(text: str) -> tuple[str, dict] | None:
    """整条消息其实是 CLI 印的一句英文提示时,给出该发的中文提示卡;否则 None。"""
    failure = classify_cli_notice(text)
    if failure is None or failure not in _CLI_NOTICE_COPY:
        return None
    line, severity, who, hint = _CLI_NOTICE_COPY[failure]
    return say(line), notice(
        EVENT_TURN_FAILED,
        severity=severity,
        who=who,
        detail=say("hintAndServiceWords", hint=say(hint), said=text.strip()),
        detail_label=say("labelDetails"),
        retryable=failure in _CLI_RETRYABLE,
    )
