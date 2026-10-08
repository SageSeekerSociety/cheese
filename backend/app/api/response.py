"""Platform response envelope: {"code", "message", "data"} (project spec)."""

from typing import Any

from pydantic import BaseModel


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


class Page[T](BaseModel):
    """``page()``'s payload as a model: the list under ``data``, plus ``total``."""

    data: list[T]
    total: int


class Envelope[T](BaseModel):
    """``ok()``'s envelope as a model, so a route can *declare* what it returns.

    Name it on the route and the OpenAPI document publishes it, which is what
    lets the frontend generate its response types instead of hand-copying them::

        @router.get("", response_model=Envelope[Page[AgentTypeOut]],
                    response_model_exclude_unset=True)
        async def list_agent_types() -> dict:
            return ok(page(items, len(items)))

    ``response_model_exclude_unset`` is part of the pattern, not decoration. The
    route builds the body with ``ok()``, which leaves ``warnings`` out entirely
    when there is nothing to warn about; serializing the model without it would
    put ``"warnings": null`` on every such response — a field no caller saw
    before and that the payload never promised.
    """

    code: int = 200
    message: str = "ok"
    data: T
    #: Beside ``data``, not inside it — see ``ok()``.
    warnings: list[str] | None = None
