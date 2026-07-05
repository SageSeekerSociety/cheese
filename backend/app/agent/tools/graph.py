"""Wiring for the tool-invocation object graph.

The one place the concrete registry + authorizer + invoker are assembled per
request (bound to the request's db session). Keeping it here means the route
stays thin and tests can build the same graph.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.authorization.authorizer import ProjectAuthorizer
from app.agent.tools.builtin import builtin_registry
from app.agent.tools.invoker import ToolInvoker
from app.domain.grant.repositories import ProjectGrantRepository
from app.domain.grant.services import ProjectGrantService
from app.domain.project.repositories import ProjectRepository


def build_tool_invoker(session: AsyncSession) -> ToolInvoker:
    authorizer = ProjectAuthorizer(
        ProjectGrantService(ProjectGrantRepository(session)),
        ProjectRepository(session),
    )
    return ToolInvoker(builtin_registry, authorizer)
