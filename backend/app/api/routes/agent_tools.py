"""The tool-RPC surface: how an agent (via the cheese CLI) calls platform tools.

Trust boundary: the acting identity comes ONLY from the X-Agent-Session token
here — never from the request body. Everything else (agent gate, permissions,
project containment) is enforced downstream by the ToolInvoker.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.authorization.authorizer import ProjectActor
from app.agent.authorization.token import decode_agent_session
from app.agent.tools.builtin import builtin_registry
from app.agent.tools.graph import build_tool_invoker
from app.core.errors import AuthenticationRequiredError
from app.db.session import get_db

router = APIRouter(prefix="/agent/tools", tags=["Agent Tools"])


async def _resolve_actor(
    x_agent_session: Annotated[str | None, Header(alias="X-Agent-Session")] = None,
) -> ProjectActor:
    if not x_agent_session:
        raise AuthenticationRequiredError("missing X-Agent-Session token")
    return decode_agent_session(x_agent_session)


class CallToolRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


@router.get("/schema", summary="List Agent Tools")
async def get_tools_schema(actor: ProjectActor = Depends(_resolve_actor)) -> dict:
    _ = actor
    return {"code": 200, "message": "success", "data": {"tools": builtin_registry.schema()}}


@router.post("/call", summary="Call an Agent Tool")
async def call_tool(
    payload: CallToolRequest,
    db: AsyncSession = Depends(get_db),
    actor: ProjectActor = Depends(_resolve_actor),
) -> dict:
    invoker = build_tool_invoker(db)
    result = await invoker.invoke(db, actor, payload.tool, payload.args)
    return {"code": 200, "message": "success", "data": {"result": result}}
