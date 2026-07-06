"""``@tool`` decorator + ``ToolRegistry``: projects an annotated Python
function's signature/docstring/hints into a JSON-Schema tool definition, and
dispatches calls while injecting typed context objects.

Security invariant: parameters whose annotation is one of the registry's
``injected_types`` (e.g. ProjectActor, ToolContext) are INJECTED by the
invoker at the trust boundary — they are never accepted as agent-supplied
JSON, and are skipped when projecting the agent-facing schema.

Schema projection rule: a parameter with NO default is required — including an
``X | None`` parameter with no default. Only parameters with a default are
optional.
"""

import inspect
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin, get_type_hints

from app.auth.core import Action, Resource


@dataclass(frozen=True)
class ToolPermission:
    """An RBAC requirement a tool declares: it may run only if the actor is
    authorized to ``action`` the ``resource`` identified by the agent argument
    named ``resource_arg``. Tools that touch only 2.0-native objects (blocks)
    declare none — their containment is structural (project from the actor)."""

    action: Action
    resource: Resource
    resource_arg: str

_PY_TYPE_TO_JSON_TYPE: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _project_type(annotation: Any) -> tuple[dict[str, Any], bool]:
    """Project a single annotation into a ``(schema_fragment, nullable)``.

    Nullability is reported separately from the fragment because required-ness
    and nullability are independent axes (an ``X | None`` param with no default
    is still required)."""
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

    # Unknown/complex annotation: degrade to a bare object rather than raising —
    # projection must never crash schema generation.
    return {"type": "object"}, False


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    """JSON-Schema object describing the agent-supplied arguments only."""
    func: Callable[..., Any]
    injected: dict[str, type]
    """Map of parameter name -> injected type (ProjectActor, ToolContext, ...)."""
    permission: ToolPermission | None = None
    """Optional RBAC requirement enforced by the invoker before dispatch."""


def project_signature(
    func: Callable[..., Any], injected_types: tuple[type, ...]
) -> tuple[dict[str, Any], dict[str, type]]:
    """Project ``func``'s signature into a JSON-Schema ``parameters`` object,
    skipping parameters whose annotation is an injected type and returning a
    ``{param_name: injected_type}`` map for them."""
    signature = inspect.signature(func)
    hints = get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    injected: dict[str, type] = {}

    for name, param in signature.parameters.items():
        if name == "self":
            continue
        annotation = hints.get(name, param.annotation)
        if annotation in injected_types:
            injected[name] = annotation
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
    return schema, injected


def _matches_type(value: Any, fragment: dict[str, Any]) -> bool:
    """Whether ``value`` conforms to a projected JSON-Schema fragment. ``bool``
    is rejected where an integer/number is expected (Python's bool-is-int trap).
    Fragments without a concrete ``type`` (Any / anyOf) are not constrained."""
    if value is None:
        return bool(fragment.get("nullable", False))
    expected = fragment.get("type")
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True  # unconstrained fragment ({} / anyOf) — cannot type-check


class ToolRegistry:
    def __init__(self, injected_types: tuple[type, ...] = ()) -> None:
        self._injected_types = injected_types
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        permission: ToolPermission | None = None,
    ) -> ToolDefinition:
        tool_name = name or func.__name__
        if tool_name in self._tools:
            raise ValueError(f"tool {tool_name!r} is already registered")
        parameters, injected = project_signature(func, self._injected_types)
        if permission is not None and permission.resource_arg not in parameters.get(
            "properties", {}
        ):
            raise ValueError(
                f"tool {tool_name!r} permission references unknown arg "
                f"{permission.resource_arg!r}"
            )
        doc = description or (inspect.getdoc(func) or "").strip()
        definition = ToolDefinition(
            name=tool_name,
            description=doc,
            parameters=parameters,
            func=func,
            injected=injected,
            permission=permission,
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
        """Every required property present, no unknown properties, and each
        value matching its projected JSON-Schema type. Raises ``ValueError`` on
        failure — the invoker maps that to a 400 so bad agent input never
        reaches the DB layer as a 500."""
        properties: dict[str, Any] = definition.parameters.get("properties", {})
        required = definition.parameters.get("required", [])
        missing = [key for key in required if key not in args]
        if missing:
            raise ValueError(f"missing required argument(s): {', '.join(sorted(missing))}")
        unknown = [key for key in args if key not in properties]
        if unknown:
            raise ValueError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
        for key, value in args.items():
            fragment = properties[key]
            if not _matches_type(value, fragment):
                expected = fragment.get("type", "the declared type")
                raise ValueError(f"argument {key!r} must be {expected}")

    async def invoke(
        self, name: str, args: dict[str, Any], injected: dict[type, Any]
    ) -> Any:
        """Validate args, inject the typed context objects, and call the tool.

        ``injected`` maps a type (ProjectActor, ToolContext, ...) to the
        instance to bind. Raises ``KeyError`` if the tool is unknown,
        ``ValueError`` on validation failure."""
        definition = self._tools.get(name)
        if definition is None:
            raise KeyError(name)
        self.validate_args(definition, args)
        kwargs: dict[str, Any] = dict(args)
        for param_name, param_type in definition.injected.items():
            if param_type not in injected:
                raise ValueError(f"missing injected context for {param_type.__name__}")
            kwargs[param_name] = injected[param_type]
        result = definition.func(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def __iter__(self) -> Iterator[ToolDefinition]:
        return iter(self._tools.values())


def tool(
    registry: ToolRegistry,
    *,
    name: str | None = None,
    description: str | None = None,
    permission: ToolPermission | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: registers ``func`` into ``registry``. The function is returned
    unchanged so it stays a plain, directly-callable/testable Python function."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        registry.register(func, name=name, description=description, permission=permission)
        return func

    return decorator
