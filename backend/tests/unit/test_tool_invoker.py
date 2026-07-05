"""Unit tests for ToolInvoker: agent gate + per-tool RBAC + dispatch, with a
fake authorizer so the composition is tested in isolation."""

from typing import Any

import pytest

from app.agent.authorization.authorizer import ProjectActor
from app.agent.tools.context import ToolContext
from app.agent.tools.invoker import ToolInvoker
from app.agent.tools.registry import ToolPermission, ToolRegistry, tool
from app.auth.core import Action, Resource
from app.core.errors import BadRequestError, NotFoundError, PermissionDeniedError

pytestmark = pytest.mark.anyio

_DB: Any = object()


class _FakeAuthorizer:
    def __init__(self, *, gate_ok: bool = True, allow: bool = True) -> None:
        self.gate_ok = gate_ok
        self.allow = allow
        self.authorize_calls: list[tuple[Action, Resource, int]] = []

    async def ensure_agent_gate(self, db, actor) -> None:
        if not self.gate_ok:
            raise PermissionDeniedError("ai off")

    async def authorize(self, db, actor, action, resource, resource_id) -> None:
        self.authorize_calls.append((action, resource, resource_id))
        if not self.allow:
            raise PermissionDeniedError("denied")


def _registry() -> ToolRegistry:
    reg = ToolRegistry(injected_types=(ProjectActor, ToolContext))

    @tool(reg)
    async def echo(actor: ProjectActor, ctx: ToolContext, x: int) -> dict:
        return {"x": x}

    @tool(reg, permission=ToolPermission(Action.READ, Resource.TASK, "task_id"))
    async def read_task(actor: ProjectActor, ctx: ToolContext, task_id: int) -> dict:
        return {"task_id": task_id}

    return reg


def _invoker(**authz_kwargs) -> tuple[ToolInvoker, _FakeAuthorizer]:
    authz = _FakeAuthorizer(**authz_kwargs)
    return ToolInvoker(_registry(), authz), authz  # type: ignore[arg-type]


_ACTOR = ProjectActor(kind="agent", actor_id=1, project_id=10)


async def test_unknown_tool_raises_not_found():
    invoker, _ = _invoker()
    with pytest.raises(NotFoundError):
        await invoker.invoke(_DB, _ACTOR, "nope", {})


async def test_agent_gate_blocks():
    invoker, _ = _invoker(gate_ok=False)
    with pytest.raises(PermissionDeniedError):
        await invoker.invoke(_DB, _ACTOR, "echo", {"x": 1})


async def test_plain_tool_dispatches():
    invoker, authz = _invoker()
    out = await invoker.invoke(_DB, _ACTOR, "echo", {"x": 5})
    assert out == {"x": 5}
    assert authz.authorize_calls == []  # no RBAC requirement -> no authorize call


async def test_permissioned_tool_authorizes_then_dispatches():
    invoker, authz = _invoker(allow=True)
    out = await invoker.invoke(_DB, _ACTOR, "read_task", {"task_id": 42})
    assert out == {"task_id": 42}
    assert authz.authorize_calls == [(Action.READ, Resource.TASK, 42)]


async def test_permissioned_tool_denied():
    invoker, _ = _invoker(allow=False)
    with pytest.raises(PermissionDeniedError):
        await invoker.invoke(_DB, _ACTOR, "read_task", {"task_id": 42})


async def test_permissioned_tool_bad_resource_arg():
    invoker, _ = _invoker()
    with pytest.raises(BadRequestError):
        await invoker.invoke(_DB, _ACTOR, "read_task", {"task_id": "not-an-int"})


async def test_arg_validation_maps_to_bad_request():
    invoker, _ = _invoker()
    with pytest.raises(BadRequestError):
        await invoker.invoke(_DB, _ACTOR, "echo", {})  # missing required x
