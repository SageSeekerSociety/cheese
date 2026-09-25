"""Stable, user-facing classifications for platform/runtime failures.

Provider details and exception traces belong in logs. Conversation events carry
only a small code + copy contract that the frontend can render safely.

Two kinds of failure arrive here and they are recognised differently:

- **Somebody else's failure** — a full disk, a missing docker image, docker
  refusing to start. The platform did not write those sentences and cannot make
  them structured, so it matches their text. That is reading a foreign format,
  which is what a parser is for.
- **The platform's own failure** — the prompt never reached the session, the
  turn hit its ceiling, the pinned machine is not answering. These used to be
  recognised the same way, by looking for a fragment of a sentence the platform
  itself had just written. Editing the copy silently reclassified the failure,
  and the copy could never be shortened past the fragment. They now carry
  ``failure_code`` on the exception or the result they travel in.
"""

from __future__ import annotations

import errno
import re
from dataclasses import dataclass

STORAGE_EXHAUSTED_CODE = "storage_exhausted"
RUNTIME_IMAGE_MISSING_CODE = "runtime_image_missing"
WORKSPACE_VCS_PERMS_CODE = "workspace_vcs_perms"
HOST_UNREACHABLE_CODE = "host_unreachable"
SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE = "subscription_credential_expired"
PROMPT_UNDELIVERED_CODE = "prompt_undelivered"
TURN_TIMEOUT_CODE = "turn_timeout"

# The platform's OWN wording for the two failures a driven runtime raises by
# itself: "the prompt never reached the claude session" and "this turn hit its
# ceiling". They live HERE, next to the classifier that recognises them, for the
# same reason DEVICE_OFFLINE_MESSAGE does — recognition matches the platform's own
# marker, never free-form provider text. `harness/driven/runtime.py` imports these
# instead of spelling its own copy, so the sentence and its classification cannot drift.
#
# Both used to fall through to the `else` in chat.py and render as
# 「AI 服务返回错误」, blaming the model provider for a turn the provider never
# saw — which sends whoever is debugging in exactly the wrong direction.
PROMPT_UNDELIVERED_MESSAGE = (
    "消息未能送达会话，会话没有任何响应。改动都还在，重试会重新打开会话。"
)
TURN_TIMEOUT_MESSAGE = "轮次超时"

# The platform's OWN wording for "the machine this topic is pinned to is not
# answering". It sits next to the classification it belongs to, and the device
# provider raises it with HOST_UNREACHABLE_CODE attached. What must never happen
# is recognising it from free-form connectivity text ("connection refused", "no
# route to host") — that also fires on an unreachable *model gateway*, which is
# not a property of the machine and must never quarantine it.
DEVICE_OFFLINE_MESSAGE = (
    "话题绑定的设备已离线，重新连接后再继续（不会换到其他设备，以免工作目录和会话错乱）"
)
_STORAGE_PATTERNS = (
    re.compile(r"\bno space left on device\b", re.IGNORECASE),
    re.compile(r"\benospc\b", re.IGNORECASE),
    re.compile(r"\berrno\s*28\b", re.IGNORECASE),
)
_RUNTIME_IMAGE_MARKERS = (
    "cheesex-agent-sandbox",
    "/sandbox:",
)
_RUNTIME_IMAGE_FAILURES = (
    "unable to find image",
    "no such image",
    "pull access denied",
    "manifest unknown",
)


@dataclass(frozen=True, slots=True)
class PlatformFailure:
    code: str
    title: str
    #: 卡面上那一句。**一句**，不是一段 —— 事故卡 = 眉标 + 标题 + 正文 + 状态，
    #: 正文写成 3-5 句，卡就是 5-6 行，房间里连着几张就没法看了。
    content: str
    retryable: bool
    #: 展开才看的那部分：常见原因、该找谁、为什么反复重试没用。以前这些都挤在
    #: `content` 里。收起来 ≠ 删掉 —— 它照样进 `meta`，前端折叠展示，芝士从 API
    #: 读到的也还是全量（平台提示统一契约：信息不能丢，只能收起来）。
    detail: str = ""
    severity: str = "error"
    # Is this failure a property of THIS TURN, or of THE MACHINE the turn ran on?
    # Only host-scoped failures count towards "this machine is dead" — retrying a
    # host-scoped failure on the same box cannot help, and moving a turn off a box
    # for a failure that would follow it there is the expensive mistake.
    host_scoped: bool = False

    @property
    def meta(self) -> dict:
        return {
            "event_type": "platform_error",
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "retryable": self.retryable,
            "detail": self.detail or None,
            "detail_label": "详细说明" if self.detail else None,
        }


STORAGE_EXHAUSTED = PlatformFailure(
    code=STORAGE_EXHAUSTED_CODE,
    title="运行环境存储空间不足",
    content="运行环境存储空间不足，本轮已暂停，平台正在清理。",
    detail=(
        "项目文件和已完成的改动都还在。清理完成后可以重试；如果反复出现，联系管理员。"
    ),
    retryable=True,
    # The disk belongs to the machine. Another container on the same box hits the
    # same full filesystem, so only a different machine can help.
    host_scoped=True,
)

RUNTIME_IMAGE_MISSING = PlatformFailure(
    code=RUNTIME_IMAGE_MISSING_CODE,
    title="运行环境镜像暂时不可用",
    content="本轮未开始，平台正在重新准备运行环境。",
    detail=(
        "本轮还没有开始执行，项目文件没有受到影响。"
        "稍后可以重试；如果反复出现，联系管理员。"
    ),
    retryable=True,
    # A missing image is a registry/network problem that follows the topic to any
    # machine — and usually hits every machine at once. Moving the topic would burn
    # a healthy box for nothing, so this must NOT count towards the machine's health.
    host_scoped=False,
)

HOST_UNREACHABLE = PlatformFailure(
    code=HOST_UNREACHABLE_CODE,
    title="无法连接设备",
    content="本轮未开始，无法连接话题绑定的设备。",
    # 话题一旦绑定就不会再换设备——`device_provider.resolve_device` 只在第一轮
    # 挑一次，之后任何一轮都回到同一台。所以这句只说该设备重新连上，不承诺平台
    # 会替它找一台：那是没有的机制，等它等不来。
    detail=(
        "项目文件和已提交的改动都还在。重新连接这台设备后可以重试。"
        "话题不会换到其他设备，以免工作目录和会话错乱。"
    ),
    retryable=True,
    host_scoped=True,
)


SUBSCRIPTION_CREDENTIAL_EXPIRED = PlatformFailure(
    code=SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE,
    title="模型订阅凭据已过期",
    content="本轮未开始，模型订阅凭据已过期，需要重新认证。",
    detail=(
        "需要有设备权限的人在设备上重新认证（claude setup-token，或恢复 "
        ".credentials.json）。这不是容器、磁盘或网络的问题，任务也没有开始，"
        "所以没有已完成的改动。重试没有作用；凭据更新后，下一条消息会正常处理。"
    ),
    # Not retried automatically: another turn against the same dead credential just
    # burns 300s again (the platform "对自己的失败没有记忆" complaint in #388). It
    # self-heals on the next human summon once the host re-auths.
    retryable=False,
    # The subscription credential is DEPLOYMENT-level (one metering proxy, shared
    # by every machine), not a property of one box — moving the topic to another
    # machine reaches the same dead credential. So it must NOT indict the machine.
    host_scoped=False,
)


WORKSPACE_VCS_PERMS = PlatformFailure(
    code=WORKSPACE_VCS_PERMS_CODE,
    title="工作区版本库权限异常",
    content="本轮未开始，工作区版本库的权限不正确。",
    detail=(
        "版本库目录属于另一个系统用户，平台无法访问。"
        "项目文件、已提交的改动和版本历史都没有受到影响。"
        "需要管理员在机器上修改一次属主（deploy/fix-workspace-ownership.sh），"
        "平台无法自行处理。把这条提示转给管理员，修复后可以重试。"
    ),
    retryable=True,
)


PROMPT_UNDELIVERED = PlatformFailure(
    code=PROMPT_UNDELIVERED_CODE,
    title="消息未送达",
    content="本轮未开始，消息没有送进会话，会话也没有任何响应。",
    detail=(
        "这不是 AI 服务的问题，请求没有到达模型。"
        "消息发往运行环境里的 claude 会话，但会话没有接收："
        "常见原因是会话停在一个等待回答的界面上，或者它所在的终端已经关闭。"
        "工作区里的文件和已完成的改动都没有受到影响。"
        "重试会重新打开会话；如果连续几次都这样，把这条提示转给管理员。"
    ),
    retryable=True,
    # NOT host-scoped: a wedged or dead claude session is a property of THIS
    # topic's screen, not of the box. Every other topic on the same machine is
    # usually fine, so counting this against the machine would quarantine a
    # healthy box and drag unrelated topics onto a new one for nothing.
    host_scoped=False,
)


TURN_TIMEOUT = PlatformFailure(
    code=TURN_TIMEOUT_CODE,
    title="本轮超过时间上限，已停止",
    content="本轮超过时间上限，已停止。",
    detail=(
        "这不是 AI 服务返回的错误，而是本轮没有在时限内结束。"
        "常见原因是某个命令一直没有返回，或者会话停在一个等待回答的界面上。"
        "已完成的改动都在工作区里。"
        "重试会从中断处继续；如果同一件事反复超时，可以把它拆小。"
    ),
    retryable=True,
    # Same reasoning as PROMPT_UNDELIVERED, and more sharply so: a turn that ran
    # long is usually a property of the WORK, not of the machine it ran on.
    host_scoped=False,
)


# Every classification this module can return. Keep new failures in this tuple —
# ``HOST_SCOPED_CODES`` is derived from it, so a failure left out silently opts
# itself out of the machine-health accounting.
ALL_FAILURES = (
    STORAGE_EXHAUSTED,
    RUNTIME_IMAGE_MISSING,
    HOST_UNREACHABLE,
    WORKSPACE_VCS_PERMS,
    PROMPT_UNDELIVERED,
    TURN_TIMEOUT,
)

# Failure codes that indict the MACHINE rather than the turn. The turn layer reads
# this off the wire (error frames carry only a code) to tell "this box is suspect"
# from "this run went wrong".
HOST_SCOPED_CODES = frozenset(f.code for f in ALL_FAILURES if f.host_scoped)


def _text_is_storage_exhausted(text: str) -> bool:
    return any(pattern.search(text) for pattern in _STORAGE_PATTERNS)


def is_storage_exhausted(value: BaseException | str) -> bool:
    """Conservatively identify ENOSPC without guessing from vague copy."""
    if isinstance(value, str):
        return _text_is_storage_exhausted(value)

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno == errno.ENOSPC:
            return True
        if _text_is_storage_exhausted(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _text_is_runtime_image_missing(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RUNTIME_IMAGE_MARKERS) and any(
        failure in lowered for failure in _RUNTIME_IMAGE_FAILURES
    )


def is_runtime_image_missing(value: BaseException | str) -> bool:
    """Identify an unavailable Cheese agent image without leaking daemon copy."""
    if isinstance(value, str):
        return _text_is_runtime_image_missing(value)

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _text_is_runtime_image_missing(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


_BY_CODE = {failure.code: failure for failure in ALL_FAILURES}


def failure_code_of(value: BaseException | str) -> str | None:
    """The code an exception is carrying, if it raised itself deliberately.

    Walks the cause/context chain, because the raise site and the handler that
    turns it into a result are usually several frames and one wrapper apart."""
    if isinstance(value, str):
        return None

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "failure_code", None)
        if isinstance(code, str) and code:
            return code
        current = current.__cause__ or current.__context__
    return None


def classify_platform_failure(
    value: BaseException | str,
    *,
    code: str | None = None,
) -> PlatformFailure | None:
    """What went wrong, or None when the platform cannot say.

    ``code`` is what the failure declared about itself — from an ``AgentResult``
    that carried one across the turn boundary. It wins over everything below,
    which only ever inspects text the platform did not write."""
    carried = code or failure_code_of(value)
    if carried is not None:
        return _BY_CODE.get(carried)
    if is_storage_exhausted(value):
        return STORAGE_EXHAUSTED
    if is_runtime_image_missing(value):
        return RUNTIME_IMAGE_MISSING
    return None


# --- CLI 自己印在对话里的英文提示 -------------------------------------------
#
# 上面那些故障都走异常或 `AgentResult`。这一类不走:它们是 Claude Code 自己
# **当成助手输出**印出来的一句话,于是原样落进房间,看起来像芝士在用英文说
# 「API Error: Unable to connect to API (ConnectionRefused)」。实测 200 个会话
# 里 88 条,而且高度集中 —— 去重之后就几句,前两句占了 57 条。
#
# 这正是本模块开头说的第一类:「别人写的句子,平台没法让它变结构化,只能匹配
# 它的文本」。识别出来之后它们该走平台提示卡,不该顶着芝士的名字发英文。
#
# 匹配**整条消息**,不是子串:芝士自己用中文讨论一个报错时会把原话引在句子里,
# 那条消息是它的话,不能被换掉。所以三个条件缺一不可 —— 通篇没有汉字、短、且
# 从已知的开头起头。
PROVIDER_UNREACHABLE_CODE = "provider_unreachable"
PROVIDER_OVERLOADED_CODE = "provider_overloaded"
MODEL_LIMIT_REACHED_CODE = "model_limit_reached"
RESPONSE_TRUNCATED_CODE = "response_truncated"
TOOL_UNAVAILABLE_CODE = "tool_unavailable"

#: 一条 CLI 提示最长能有多长。真实样本最长的一条 120 字符出头;留三倍余量,再长
#: 就不是提示而是内容了。
_CLI_NOTICE_MAX = 300

_CLI_NOTICES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^API Error:.*\b(ConnectionRefused|ECONNRESET|Connection refused)\b",
            re.I,
        ),
        PROVIDER_UNREACHABLE_CODE,
    ),
    (
        re.compile(r"^API Error:\s*(502|503|529)\b", re.I),
        PROVIDER_OVERLOADED_CODE,
    ),
    (
        re.compile(r"^API Error:.*\b(Overloaded|Bad Gateway)\b", re.I),
        PROVIDER_OVERLOADED_CODE,
    ),
    (
        re.compile(r"^You'?ve reached your .*\blimit\b", re.I),
        MODEL_LIMIT_REACHED_CODE,
    ),
    (
        re.compile(
            r"^(API Error:\s*)?(Response stalled mid-stream"
            r"|The response stopped arriving)",
            re.I,
        ),
        RESPONSE_TRUNCATED_CODE,
    ),
    # 「我还没有 X 的权限,请批准一下」—— 在这个平台上根本没有人能批准:那个
    # 授权框画在容器的终端里,房间里的人够不着。所以它出现本身就说明配置不对,
    # 而它读起来却像一句正常的请求 —— 一个故障伪装成了一句话,最坏的一种。
    (
        re.compile(
            r"^(I don'?t have permission to use\b"
            r"|No response requested\.?$"
            r"|Tool ran without output)",
            re.I,
        ),
        TOOL_UNAVAILABLE_CODE,
    ),
)

_HAS_CJK = re.compile(r"[一-鿿]")


def classify_cli_notice(text: str) -> str | None:
    """整条消息其实是 CLI 自己印的一句英文提示时,它属于哪一类;否则 None。"""
    line = text.strip()
    if not line or len(line) > _CLI_NOTICE_MAX or _HAS_CJK.search(line):
        return None
    for pattern, failure in _CLI_NOTICES:
        if pattern.search(line):
            return failure
    return None
