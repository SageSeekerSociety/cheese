"""``@tool`` decorator + ``ToolRegistry``: projects an ANNOTATED Python
function's signature + docstring + type hints into a JSON-Schema tool
definition (contract §6 ``GET /connector/tools/schema``), and dispatches
calls while injecting ``ActorContext`` (contract §6 ``POST
/connector/tools/call``).

Security invariant: a function's ``ActorContext``-typed parameter is
INJECTED by ``ToolRegistry.invoke`` -- it is never accepted as agent-supplied
JSON, and ``project_signature`` skips it entirely when building the
agent-facing schema.

Schema projection rule (kept in this one tested function,
``project_signature``): a parameter with NO default is required -- including
an ``X | None`` parameter with no default, which is still required. Only
parameters that declare a default are optional.
"""

import inspect
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin, get_type_hints

from app.agent.context import ActorContext

_PY_TYPE_TO_JSON_TYPE: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _project_type(annotation: Any) -> tuple[dict[str, Any], bool]:
    """Project a single type annotation into a JSON-Schema fragment.

    Returns ``(schema_fragment, nullable)``. ``nullable`` is reported
    separately (rather than folded into the fragment) so the caller decides
    how to represent it -- required-ness and nullability are independent
    axes (an ``X | None`` parameter with no default is still required).
    """
    origin = get_origin(annotation)

    if origin is Union or origin is types.UnionType:
        members = get_args(annotation)
        non_none = [m for m in members if m is not type(None)]
        nullable = type(None) in members
        if len(non_none) == 1:
            fragment, _ = _project_type(non_none[0])
            return fragment, nullable
        return {"anyOf": [_project_type(m)[0] for m in non_none]}, nullable

    if origin in (list, tuple, set, frozenset):
        item_args = get_args(annotation)
        item_fragment = _project_type(item_args[0])[0] if item_args else {}
        return {"type": "array", "items": item_fragment}, False

    if origin is dict:
        return {"type": "object"}, False

    if annotation in _PY_TYPE_TO_JSON_TYPE:
        return {"type": _PY_TYPE_TO_JSON_TYPE[annotation]}, False

    if annotation is inspect.Signature.empty or annotation is Any:
        return {}, False

    # Unknown/complex annotation (dataclass, TypedDict, ...): degrade to a
    # bare object rather than raising -- projection must never crash schema
    # generation for a tool whose author used a richer type.
    return {"type": "object"}, False


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    """A JSON-Schema object (``{"type": "object", "properties": {...},
    "required": [...]}``) describing the agent-supplied arguments only."""
    func: Callable[..., Any]
    actor_param: str | None
    """Name of the function parameter that receives the injected
    ``ActorContext``, or ``None`` if the tool declares none."""


def project_signature(func: Callable[..., Any]) -> tuple[dict[str, Any], str | None]:
    """Project ``func``'s signature into a JSON-Schema ``parameters`` object.

    Skips any parameter annotated ``ActorContext`` (it is injected, never
    agent-supplied) and returns its name as the second element of the tuple.
    Raises ``TypeError`` if more than one such parameter exists.
    """
    signature = inspect.signature(func)
    hints = get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    actor_param: str | None = None

    for name, param in signature.parameters.items():
        if name == "self":
            continue
        annotation = hints.get(name, param.annotation)
        if annotation is ActorContext:
            if actor_param is not None:
                raise TypeError(f"{func!r} declares more than one ActorContext parameter")
            actor_param = name
            continue

        fragment, nullable = _project_type(annotation)
        if nullable:
            fragment = {**fragment, "nullable": True}
        properties[name] = fragment
        if param.default is inspect.Signature.empty:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema, actor_param


class ToolRegistry:
    """Holds registered tools; used by the ``@tool`` decorator to register
    and by ``router.py`` to project ``/connector/tools/schema`` and dispatch
    ``/connector/tools/call``."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ToolDefinition:
        tool_name = name or func.__name__
        if tool_name in self._tools:
            raise ValueError(f"tool {tool_name!r} is already registered")
        parameters, actor_param = project_signature(func)
        doc = description or (inspect.getdoc(func) or "").strip()
        definition = ToolDefinition(
            name=tool_name,
            description=doc,
            parameters=parameters,
            func=func,
            actor_param=actor_param,
        )
        self._tools[tool_name] = definition
        return definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def schema(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ]

    def validate_args(self, definition: ToolDefinition, args: dict[str, Any]) -> None:
        """Minimal structural validation of agent-supplied args against the
        projected schema: every required property present, no unknown
        properties. Raises ``ValueError`` on failure -- callers (see
        ``router.py``) translate that into an HTTP error."""
        required = definition.parameters.get("required", [])
        missing = [key for key in required if key not in args]
        if missing:
            raise ValueError(f"missing required argument(s): {', '.join(sorted(missing))}")
        allowed = set(definition.parameters.get("properties", {}))
        unknown = [key for key in args if key not in allowed]
        if unknown:
            raise ValueError(f"unexpected argument(s): {', '.join(sorted(unknown))}")

    async def invoke(self, name: str, actor: ActorContext, args: dict[str, Any]) -> Any:
        """Validate args, inject ``actor``, and call the tool. Raises
        ``KeyError`` if ``name`` is not registered, ``ValueError`` on
        argument validation failure."""
        definition = self._tools.get(name)
        if definition is None:
            raise KeyError(name)
        self.validate_args(definition, args)
        kwargs: dict[str, Any] = dict(args)
        if definition.actor_param is not None:
            kwargs[definition.actor_param] = actor
        result = definition.func(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def __iter__(self) -> Iterator[ToolDefinition]:
        return iter(self._tools.values())


default_registry = ToolRegistry()
"""Process-wide default registry. Prefer passing an explicit ``registry=``
to ``@tool`` (see ``tools.py``) so independent object graphs (e.g. one per
test) never share tool state; this exists for simple scripts/demos."""


def tool(
    *,
    name: str | None = None,
    description: str | None = None,
    registry: ToolRegistry | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: registers ``func`` into ``registry`` (``default_registry``
    if omitted) as a tool. The decorated function is returned unchanged so it
    stays a plain, directly-callable/testable Python function."""

    target = registry if registry is not None else default_registry

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        target.register(func, name=name, description=description)
        return func

    return decorator
