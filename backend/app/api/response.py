"""Platform response envelope: {"code", "message", "data"} (project spec)."""

from typing import Any


def ok(data: Any, message: str = "ok") -> dict[str, Any]:
    return {"code": 200, "message": message, "data": data}


def page(items: list[Any], total: int) -> dict[str, Any]:
    return {"data": items, "total": total}
