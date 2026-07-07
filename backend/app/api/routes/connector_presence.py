"""Agent presence — batch lookup so any avatar in the UI can become agent-aware.

``GET /connector/agents/presence?ids=1,2,3`` returns, for each requested ``user_id``,
whether it is an agent and (if so) its live cheeselet status + how to open its 现场.
Humans come back with ``is_agent=false`` so the client can cache them and stop polling;
agents carry ``agent_status`` / ``elapsed`` / ``tokens`` / ``sid`` / ``device_id``.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.hub import DeviceHub
from app.agent.identity import build_member_dicts
from app.common.auth import get_current_user_id


def build_presence_router(
    hub: DeviceHub, session_factory: async_sessionmaker[AsyncSession]
) -> APIRouter:
    router = APIRouter(prefix="/connector/agents", tags=["connector"])

    @router.get("/presence")
    async def presence(
        ids: str = Query(default=""),
        _user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        user_ids: list[int] = []
        for part in ids.split(","):
            part = part.strip()
            if part.isdigit():
                user_ids.append(int(part))
        # De-dup while preserving order; cap to a sane batch size.
        seen: set[int] = set()
        unique = [i for i in user_ids if not (i in seen or seen.add(i))][:200]
        if not unique:
            return {"presence": {}}
        async with session_factory() as session:
            members = await build_member_dicts(session, hub, unique)
        return {"presence": {str(m["user_id"]): m for m in members}}

    return router
