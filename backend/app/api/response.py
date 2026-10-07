"""Platform response envelope: {"code", "message", "data"} (project spec)."""

from functools import cache
from typing import Any, get_args, get_origin

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


class Envelope[T](BaseModel):
    """What ``ok(data)`` puts on the wire, as a type the OpenAPI document can name.

    Only ever a *documentation* model (see ``typed_response``): no route returns
    one, so it cannot change a response. ``warnings`` is left out on purpose — a
    route that sends them declares its own envelope when it gets a type.
    """

    code: int
    message: str
    data: T


class Page[T](BaseModel):
    """What ``page(items, total)`` builds: one page of a list, and the list's size.

    Subclass it to name a page, as ``class FeedbackListOut(Page[FeedbackCard])``
    does: the subclass is the name OpenAPI and the frontend types use.
    """

    data: list[T]
    total: int


class Deleted(BaseModel):
    """``ok({"deleted": True})``: the thing is gone (or was never there to see)."""

    deleted: bool


def typed_response(model: Any) -> dict[str, Any]:
    """Route keyword arguments that write ``Envelope[model]`` into OpenAPI.

        @router.get("/meta", **typed_response(FeedbackMeta))

    Documentation only. ``response_model=None`` keeps FastAPI from validating or
    filtering what the route returns, so declaring a type can never drop a field
    from a live response — the route still returns ``ok(...)``. Whether the two
    agree is the business of the route's own tests.
    """
    return {"response_model": None, "responses": {200: {"model": _envelope_of(model)}}}


@cache
def _envelope_of(model: Any) -> type[BaseModel]:
    # A named subclass, because OpenAPI names a parametrized model after its
    # generic form (`Envelope_FeedbackCard_`) and the frontend types are spelled
    # with that name. One class per model, so two routes share one schema.
    return type(f"{_type_name(model)}Envelope", (Envelope[model],), {})


def _type_name(tp: Any) -> str:
    if get_origin(tp) is list:
        return f"{_type_name(get_args(tp)[0])}List"
    return tp.__name__
