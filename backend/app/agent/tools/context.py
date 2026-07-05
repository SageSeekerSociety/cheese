"""ToolContext: the per-request execution context injected into every tool.

Carries the db session the tool needs to reach business services. Built by the
invoker at the trust boundary and injected by the registry — never agent-
supplied.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
