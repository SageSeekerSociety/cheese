"""Unit tests for the tool registry: schema projection, context injection,
and argument validation. No DB, no real services."""

import pytest

from app.agent.tools.registry import ToolRegistry, tool

pytestmark = pytest.mark.anyio


class _Actor:
    pass


class _Ctx:
    pass


def _registry() -> tuple[ToolRegistry, _Actor, _Ctx]:
    reg = ToolRegistry(injected_types=(_Actor, _Ctx))

    @tool(reg, description="a sample tool")
    async def sample(actor: _Actor, ctx: _Ctx, a: int, c: int | None, b: str = "x") -> dict:
        return {"a": a, "b": b, "c": c, "actor": actor, "ctx": ctx}

    return reg, _Actor(), _Ctx()


def test_schema_skips_injected_and_marks_required():
    reg, _, _ = _registry()
    definition = reg.get("sample")
    assert definition is not None
    props = definition.parameters["properties"]
    # injected params are not exposed to the agent
    assert set(props) == {"a", "b", "c"}
    # a and c (X|None, no default) are required; b (has default) is optional
    assert set(definition.parameters["required"]) == {"a", "c"}
    assert props["a"] == {"type": "integer"}
    assert props["c"] == {"type": "integer", "nullable": True}
    assert definition.injected == {"actor": _Actor, "ctx": _Ctx}
    assert definition.description == "a sample tool"


def test_validate_rejects_missing_and_unknown():
    reg, _, _ = _registry()
    definition = reg.get("sample")
    assert definition is not None
    with pytest.raises(ValueError):
        reg.validate_args(definition, {"a": 1})  # missing required c
    with pytest.raises(ValueError):
        reg.validate_args(definition, {"a": 1, "c": 2, "zzz": 9})  # unknown arg


async def test_invoke_injects_context_and_dispatches():
    reg, actor, ctx = _registry()
    out = await reg.invoke(
        "sample", {"a": 1, "c": None}, injected={_Actor: actor, _Ctx: ctx}
    )
    assert out["a"] == 1
    assert out["b"] == "x"  # default applied
    assert out["c"] is None
    assert out["actor"] is actor  # injected, not agent-supplied
    assert out["ctx"] is ctx


async def test_invoke_unknown_tool_raises():
    reg, actor, ctx = _registry()
    with pytest.raises(KeyError):
        await reg.invoke("nope", {}, injected={_Actor: actor, _Ctx: ctx})
