"""Authorizer: the single choke point where every tool RPC is authorized
against the calling actor's REAL permissions, at the orchestrator -> business
function boundary, before the tool's business logic runs (see
``router.py``'s ``/connector/tools/call`` handler).

Swap the default ``PermissiveAuthorizer`` for a real permission-checking
implementation via ``wiring.py`` -- call sites never change.
"""

from typing import Any, Protocol

from app.agent.context import ActorContext
from app.core.errors import PermissionDeniedError


class Authorizer(Protocol):
    async def authorize(self, actor: ActorContext, tool_name: str, args: dict[str, Any]) -> None:
        """Raise (typically ``app.core.errors.PermissionDeniedError`` or a
        subclass of ``app.core.errors.BaseError``) to deny the call. Return
        ``None`` to allow it."""
        ...


class PermissiveAuthorizer:
    """Default ``Authorizer``: allows every call. Appropriate for the demo
    app and for early development; real deployments inject a permission- or
    scope-checking ``Authorizer`` instead."""

    async def authorize(self, actor: ActorContext, tool_name: str, args: dict[str, Any]) -> None:
        return None


class ScopeAuthorizer:
    """Example non-default ``Authorizer``: requires ``f"tool:{tool_name}"``
    (or the wildcard ``"tool:*"``) in ``actor.scopes``. Demonstrates that the
    seam is real without committing every deployment to a specific scope
    naming scheme."""

    async def authorize(self, actor: ActorContext, tool_name: str, args: dict[str, Any]) -> None:
        required = f"tool:{tool_name}"
        if not actor.has_scope(required) and not actor.has_scope("tool:*"):
            raise PermissionDeniedError(
                f"actor {actor.actor_id!r} lacks scope {required!r}",
                data={"actorId": actor.actor_id, "tool": tool_name, "requiredScope": required},
            )
