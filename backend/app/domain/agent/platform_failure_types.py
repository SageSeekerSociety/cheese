"""The shape one classification answers in.

``PlatformFailure`` is what every recogniser hands back, and the platform's own
failures carry a code on themselves rather than being recognised from a
sentence — ``failure_code_of`` is the read half of that contract.

This lives apart from the catalogue and the recognisers so both can name it: a
recogniser builds a ``PlatformFailure`` while the catalogue lists the
recognisers' results, so leaving the shape in the catalogue would make that one
import go round in a circle.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.sentences import notice_keys, say


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
        detail_label = say("labelDetails") if self.detail else None
        return {
            "event_type": "platform_error",
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "retryable": self.retryable,
            "detail": self.detail or None,
            "detail_label": detail_label,
            **notice_keys(
                title=self.title, detail=self.detail, detail_label=detail_label
            ),
        }


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
