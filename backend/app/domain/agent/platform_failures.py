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
)

RUNTIME_IMAGE_MISSING = PlatformFailure(
    code=RUNTIME_IMAGE_MISSING_CODE,
    title="Agent 运行组件暂时缺失",
    content=(
        "平台正在重新准备 Agent 运行组件，本轮还没有开始执行，项目文件没有受到影响。"
        "请稍后再 @芝士 重试；若持续出现，请联系管理员。"
    ),
    retryable=True,
)


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


def classify_platform_failure(
    value: BaseException | str,
) -> PlatformFailure | None:
    if is_storage_exhausted(value):
        return STORAGE_EXHAUSTED
    if is_runtime_image_missing(value):
        return RUNTIME_IMAGE_MISSING
    return None
