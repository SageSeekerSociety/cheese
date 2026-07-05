"""ToolInvoker: the single entry point that turns an agent's structured tool
call into an authorized business action.

For every call it (1) opens the universal agent gate (project ai_mode), (2)
enforces the tool's optional RBAC requirement via the ProjectAuthorizer, then
(3) builds the ToolContext and dispatches through the registry. The actor is
always the one injected here — never agent-supplied.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.authorization.authorizer import ProjectActor, ProjectAuthorizer
from app.agent.tools.context import ToolContext
from app.agent.tools.registry import ToolRegistry
from app.core.errors import BadRequestError, NotFoundError


class ToolInvoker:
    def __init__(self, registry: ToolRegistry, authorizer: ProjectAuthorizer) -> None:
        self._registry = registry
        self._authorizer = authorizer

    async def invoke(
        self,
        db: AsyncSession,
        actor: ProjectActor,
        tool_name: str,
        args: dict[str, Any],
    ) -> Any:
        definition = self._registry.get(tool_name)
        if definition is None:
            raise NotFoundError(f"tool {tool_name!r} not found")

        # 1. Universal agent gate — a project with AI off blocks every tool.
        await self._authorizer.ensure_agent_gate(db, actor)

        # 2. Per-tool RBAC requirement, if the tool declares one.
        if definition.permission is not None:
            perm = definition.permission
            resource_id = args.get(perm.resource_arg)
            if not isinstance(resource_id, int):
                raise BadRequestError(f"argument {perm.resource_arg!r} must be an integer")
            await self._authorizer.authorize(
                db, actor, perm.action, perm.resource, resource_id
            )

        # 3. Dispatch with the injected context.
        ctx = ToolContext(session=db)
        try:
            return await self._registry.invoke(
                tool_name, args, injected={ProjectActor: actor, ToolContext: ctx}
            )
        except ValueError as exc:  # argument validation failure from the registry
            raise BadRequestError(str(exc)) from exc
