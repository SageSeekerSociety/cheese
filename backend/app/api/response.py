"""Platform response envelope: {"code", "message", "data"} (project spec)."""

from typing import Any


def ok(
    data: Any, message: str = "ok", *, warnings: list[str] | None = None
) -> dict[str, Any]:
    """The envelope, plus an optional ``warnings`` list beside ``data``.

    警告不属于 ``data``：``data`` 是这次动的那件东西（写入的文档那一块），而
    警告说的是「它哪里不像话」——同一个 ``data`` 上会挂不同的一串。空表不出现，
    因为「没有警告」和「这次响应不带这个字段」对读的人是同一件事。
    """
    envelope: dict[str, Any] = {"code": 200, "message": message, "data": data}
    if warnings:
        envelope["warnings"] = warnings
    return envelope


def page(items: list[Any], total: int) -> dict[str, Any]:
    return {"data": items, "total": total}
