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


class Page[T](BaseModel):
    """``page()``'s payload as a model: the list under ``data``, plus ``total``.

    Subclass it to name a page, as ``class FeedbackListOut(Page[FeedbackCard])``
    does: the subclass is the name OpenAPI and the frontend types use.
    """

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

    The other way in is ``typed_response(model)`` below, which writes the same
    envelope into ``responses[200]`` under a *named* subclass — that is how a
    route published its schema before this class was named on a route, and how
    the feedback routes still do it.

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
