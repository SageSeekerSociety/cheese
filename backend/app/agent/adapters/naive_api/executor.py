"""Wire the naive_api loop's ToolExecutor to the real ToolInvoker.

The naive loop runs outside any HTTP request, so each tool call opens its own db
session, runs the authorized invoker, and commits — one transaction per tool
call. This is the glue that lets a naive_api agent act on real business through
the same authorized tool path (agent gate + permissions + containment) as the
connector.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.adapters.naive_api.types import ToolExecutor
from app.agent.authorization.authorizer import ProjectActor
from app.agent.tools.graph import build_tool_invoker
from app.agent.tools.invoker import ToolInvoker


def make_tool_executor(
    session_factory: async_sessionmaker[AsyncSession],
    invoker_builder: Callable[[AsyncSession], ToolInvoker] = build_tool_invoker,
) -> ToolExecutor:
    async def execute(actor: ProjectActor, tool_name: str, args: dict[str, Any]) -> Any:
        async with session_factory() as session:
            result = await invoker_builder(session).invoke(session, actor, tool_name, args)
            await session.commit()
            return result

    return execute
