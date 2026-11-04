"""Which gateway key a caller's spend goes on, and whether it may spend now.

The model route (`routes/llm_proxy.py`) and the platform's web search
(`routes/fetch.py`, `cheese_web(action=search)`) ask the same questions about an
agent's credential, so a search is admitted and paid for exactly as the
work's model calls are: on the project's key, or its task's when the
credential works a task whose credits a member owns.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.queries import TaskCreditContext, task_credit_context
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.usage.balance import Refusal
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.services import UsageService


async def task_credit_context_of(
    db: AsyncSession, project, claims: dict
) -> TaskCreditContext | None:
    if not claims.get("t"):
        return None
    try:
        conversation_id = uuid.UUID(claims["t"])
    except ValueError:
        return None
    agents = AgentInstanceService(db)
    agent = await agents.for_seat_handle(project, claims.get("a"))
    if agent is None:
        agent = await agents.for_project(project)
    handles = [claims.get("a") or ""]
    if agent is not None:
        handles.extend([agent.handle, agent_instance_handle(agent.instance_id)])
    return await task_credit_context(db, project.id, conversation_id, handles)


async def credit_refusal(
    db: AsyncSession, project, context: TaskCreditContext | None
) -> Refusal | None:
    refused = await UsageService(db).admit_project(project.id)
    if refused is not None or context is None:
        return refused
    return await CreditBudgets(db).member_refusal(project.id, context.owner_handle)
