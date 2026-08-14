"""Stable, user-facing classifications for platform/runtime failures.

Provider details and exception traces belong in logs. Conversation events carry
only a small code + copy contract that the frontend can render safely.
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

# The platform's OWN wording for "the machine this topic is pinned to is not
# answering". It lives here, not in the device provider that raises it, because
# host-unreachable is recognised by matching this exact sentence: a
# platform-generated marker, not a guess at some provider's copy. Matching
# free-form connectivity text ("connection refused", "no route to host") would
# also fire on an unreachable *model gateway*, which is not a property of the
# machine and must never quarantine it.
DEVICE_OFFLINE_MESSAGE = (
    "话题绑定的算力设备已离线，请重新连接该设备再继续本轮"
    "（不会漂到别的设备，以免工作树/会话错乱）"
)
_HOST_UNREACHABLE_MARKER = "话题绑定的算力设备已离线"
_STORAGE_PATTERNS = (
    re.compile(r"\bno space left on device\b", re.IGNORECASE),
    re.compile(r"\benospc\b", re.IGNORECASE),
    re.compile(r"\berrno\s*28\b", re.IGNORECASE),
)
_RUNTIME_IMAGE_MARKERS = (
    "cheesex-agent-tmux",
    "cheesex-agent-sandbox",
    "/sandbox-tmux:",
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
    content: str
    retryable: bool
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
        }


STORAGE_EXHAUSTED = PlatformFailure(
    code=STORAGE_EXHAUSTED_CODE,
    title="运行环境存储空间不足",
    content=(
        "这轮因运行环境存储空间不足而暂停，项目文件和已完成的改动都还在。"
        "平台正在清理临时空间，请稍后再 @芝士 继续；若持续出现，请联系管理员。"
    ),
    retryable=True,
    # The disk belongs to the machine. Another container on the same box hits the
    # same full filesystem, so only a different machine can help.
    host_scoped=True,
)

RUNTIME_IMAGE_MISSING = PlatformFailure(
    code=RUNTIME_IMAGE_MISSING_CODE,
    title="Agent 运行组件暂时缺失",
    content=(
        "平台正在重新准备 Agent 运行组件，本轮还没有开始执行，项目文件没有受到影响。"
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
    title="算力机器连不上",
    content=(
        "这轮没能开始——本话题绑定的算力机器连不上，项目文件和已提交的改动都还在。"
        "请检查该机器是否在线；若它持续联系不上，平台会把本话题换到别的机器上继续。"
    ),
    retryable=True,
    host_scoped=True,
)


SUBSCRIPTION_CREDENTIAL_EXPIRED = PlatformFailure(
    code=SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE,
    title="平台的模型订阅凭据已过期",
    content=(
        "这轮没能开始：平台的 Claude 订阅凭据已过期，需要有主机权限的人在盒子上"
        "重新认证（claude setup-token，或恢复 .credentials.json）。这不是容器、"
        "磁盘或网络的问题，也不是芝士卡在某一步——所以这里没有「已完成的改动」。"
        "反复 @芝士 不会有用；凭据在主机侧刷新后，下一次 @ 它就会自动恢复。"
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
    content=(
        "这轮没能开始：工作区版本库里有一个元数据文件的属主不是平台进程，"
        "平台读不到它，话题就起不来。项目文件和已提交的改动都没有受影响，"
        "版本历史也没有动过。平台会在下一次访问时自动清掉这个文件并恢复，"
        "请稍后再 @芝士 重试；若反复出现，请把这条提示转给管理员。"
    ),
    retryable=True,
)


# Every classification this module can return. Keep new failures in this tuple —
# ``HOST_SCOPED_CODES`` is derived from it, so a failure left out silently opts
# itself out of the machine-health accounting.
ALL_FAILURES = (
    STORAGE_EXHAUSTED,
    RUNTIME_IMAGE_MISSING,
    HOST_UNREACHABLE,
    WORKSPACE_VCS_PERMS,
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


def is_host_unreachable(value: BaseException | str) -> bool:
    """Identify "the machine this topic is pinned to is not answering".

    Deliberately matches only the platform's own marker sentence
    (``DEVICE_OFFLINE_MESSAGE``) rather than generic connectivity text — see the
    comment on that constant for why the loose version is unsafe."""
    if isinstance(value, str):
        return _HOST_UNREACHABLE_MARKER in value

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _HOST_UNREACHABLE_MARKER in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def classify_platform_failure(
    value: BaseException | str,
) -> PlatformFailure | None:
    if is_storage_exhausted(value):
        return STORAGE_EXHAUSTED
    if is_runtime_image_missing(value):
        return RUNTIME_IMAGE_MISSING
    if is_workspace_vcs_perms(value):
        return WORKSPACE_VCS_PERMS
    if is_host_unreachable(value):
        return HOST_UNREACHABLE
    return None
