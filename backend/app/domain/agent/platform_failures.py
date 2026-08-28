"""Stable, user-facing classifications for platform/runtime failures.

Provider details and exception traces belong in logs. Conversation events carry
only a small code + copy contract that the frontend can render safely.

Two kinds of failure arrive here and they are recognised differently:

- **Somebody else's failure** — a full disk, a missing docker image, jj refusing
  a store file. The platform did not write those sentences and cannot make them
  structured, so it matches their text. That is reading a foreign format, which
  is what a parser is for.
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
# jj's own wording, plus the backend's translation of it (workspace/service.py).
# Either one reaching here means a metadata file in the store is owned by another
# uid — a chmod-shaped problem that used to render as "AI 服务返回错误".
_VCS_PERMS_MARKERS = (
    "failed to determine the secure config",
    "工作区版本库权限异常",
)
HOST_UNREACHABLE_CODE = "host_unreachable"
SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE = "subscription_credential_expired"
PROMPT_UNDELIVERED_CODE = "prompt_undelivered"
TURN_TIMEOUT_CODE = "turn_timeout"

# The platform's OWN wording for the two failures the hooks substrate raises by
# itself: "the prompt never reached the claude session" and "this turn hit its
# ceiling". They live HERE, next to the classifier that recognises them, for the
# same reason DEVICE_OFFLINE_MESSAGE does — recognition matches the platform's own
# marker, never free-form provider text. `hooks_substrate` imports these instead
# of spelling its own copy, so the sentence and its classification cannot drift.
#
# Both used to fall through to the `else` in chat.py and render as
# 「AI 服务返回错误」, blaming the model provider for a turn the provider never
# saw — which sends whoever is debugging in exactly the wrong direction.
PROMPT_UNDELIVERED_MESSAGE = (
    "这条消息没能送到芝士那边，它的会话没有任何反应。改动都还在，"
    "再 @ 它一次就会重开会话重试。"
)
TURN_TIMEOUT_MESSAGE = "轮次超时"

# The platform's OWN wording for "the machine this topic is pinned to is not
# answering". It sits next to the classification it belongs to, and the device
# provider raises it with HOST_UNREACHABLE_CODE attached. What must never happen
# is recognising it from free-form connectivity text ("connection refused", "no
# route to host") — that also fires on an unreachable *model gateway*, which is
# not a property of the machine and must never quarantine it.
DEVICE_OFFLINE_MESSAGE = (
    "话题绑定的算力设备已离线，请重新连接该设备再继续本轮"
    "（不会漂到别的设备，以免工作树/会话错乱）"
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
    content="本轮因运行环境存储空间不足而暂停，平台正在清理，稍后可继续。",
    detail=(
        "项目文件和已完成的改动都还在。平台正在清理临时空间，"
        "请稍后再 @芝士 继续；若持续出现，请联系管理员。"
    ),
    retryable=True,
    # The disk belongs to the machine. Another container on the same box hits the
    # same full filesystem, so only a different machine can help.
    host_scoped=True,
)

RUNTIME_IMAGE_MISSING = PlatformFailure(
    code=RUNTIME_IMAGE_MISSING_CODE,
    title="运行环境镜像暂时不可用",
    content="本轮没能开始：平台正在重新准备运行环境镜像。",
    detail=(
        "本轮还没有开始执行，项目文件没有受到影响。"
        "请稍后再 @芝士 重试；若持续出现，请联系管理员。"
    ),
    retryable=True,
    # A missing image is a registry/network problem that follows the topic to any
    # machine — and usually hits every machine at once. Moving the topic would burn
    # a healthy box for nothing, so this must NOT count towards the machine's health.
    host_scoped=False,
)

HOST_UNREACHABLE = PlatformFailure(
    code=HOST_UNREACHABLE_CODE,
    title="设备连不上",
    content="本轮没能开始：本话题绑定的设备连不上。",
    detail=(
        "项目文件和已提交的改动都还在。"
        "请检查该设备是否在线；若它持续联系不上，平台会把本话题换到别的设备上继续。"
    ),
    retryable=True,
    host_scoped=True,
)


SUBSCRIPTION_CREDENTIAL_EXPIRED = PlatformFailure(
    code=SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE,
    title="平台的模型订阅凭据已过期",
    content="本轮没能开始：平台的模型订阅凭据已过期，需要有权限的人在设备上重新认证。",
    detail=(
        "需要有设备权限的人在设备上重新认证（claude setup-token，或恢复 "
        ".credentials.json）。这不是容器、磁盘或网络的问题，也不是芝士卡在某一步"
        "——所以这里没有「已完成的改动」。"
        "反复 @芝士 不会有用；凭据在设备上刷新后，下一次 @ 它就会自动恢复。"
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
    content="本轮没能开始：工作区版本库有个元数据文件平台读不到，话题起不来。",
    detail=(
        "那个文件的属主不是平台进程，平台读不到它，话题就起不来。"
        "项目文件和已提交的改动都没有受影响，版本历史也没有动过。"
        "平台会在下一次访问时自动清掉这个文件并恢复，"
        "请稍后再 @芝士 重试；若反复出现，请把这条提示转给管理员。"
    ),
    retryable=True,
)


PROMPT_UNDELIVERED = PlatformFailure(
    code=PROMPT_UNDELIVERED_CODE,
    title="消息没送到芝士那边",
    content="本轮没能开始：消息没送进芝士的会话，它那边一点反应都没有。",
    detail=(
        "这不是 AI 服务的问题 —— 请求根本没走到模型那一步。"
        "消息是打进运行环境里那个 claude 会话的，而它没有接住："
        "常见的是会话停在某个等人回答的界面上，或者它所在的终端已经不在了。"
        "工作区里的文件和已完成的改动都没有受影响。"
        "再 @ 一次芝士，平台会重开会话重试；若连着几次都这样，请把这条提示转给管理员。"
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
    title="本轮到达时间上限，已被强制结束",
    content="本轮到达平台的时间上限仍未结束，已被强制结束。",
    detail=(
        "这不是 AI 服务返回的错误 —— 是本轮在时限内没有收尾。"
        "常见的是卡在某个一直不返回的命令上，或者会话停在了一个等人回答的界面上。"
        "已完成的改动都还在工作区里。"
        "再 @ 一次芝士，它会从断点接着做；如果同一件事反复超时，把它拆小一点再试。"
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


def _text_is_workspace_vcs_perms(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _VCS_PERMS_MARKERS)


def is_workspace_vcs_perms(value: BaseException | str) -> bool:
    """Identify the cross-uid jj metadata failure without leaking a traceback."""
    if isinstance(value, str):
        return _text_is_workspace_vcs_perms(value)

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _text_is_workspace_vcs_perms(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


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
    if is_workspace_vcs_perms(value):
        return WORKSPACE_VCS_PERMS
    return None
