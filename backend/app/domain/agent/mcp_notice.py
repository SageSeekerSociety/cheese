"""The remote MCP servers a session cannot use yet, said where it talks."""

import logging
import uuid

from sqlalchemy import select

from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_MCP_NOT_CONNECTED,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.block.models import Block, BlockKind
from app.domain.remote_mcp import service as remote_mcp

logger = logging.getLogger(__name__)


async def unconnected_mcp(
    sessions,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    agent_handle: str | None,
    *,
    inner_id: uuid.UUID | None,
) -> tuple[str, ...]:
    """The remote MCP servers this session cannot use yet, its type's too,
    each said once in the conversation the session talks in — the room's main
    line, or its task or 支线 (``inner_id``): 「<name> 需要在项目设置里连接」."""

    try:
        async with sessions() as session:
            unusable = (
                await remote_mcp.session_servers(session, project_id, agent_handle)
            ).unusable
            said = set(
                await session.scalars(
                    select(Block.meta["server"].as_string()).where(
                        Block.conversation_id == (inner_id or room_id),
                        Block.kind == BlockKind.event,
                        Block.meta["event_type"].as_string() == EVENT_MCP_NOT_CONNECTED,
                    )
                )
            )
            for name in unusable:
                if name in said:
                    continue
                await announce(
                    session,
                    place_id=room_id,
                    task_id=inner_id,
                    content=say("mcpNotConnected", server=name),
                    meta={
                        **notice(
                            EVENT_MCP_NOT_CONNECTED,
                            severity=SEVERITY_WARN,
                            who=WHO_HUMAN,
                        ),
                        "server": name,
                    },
                )
            await session.commit()
    except Exception:  # noqa: BLE001 — a notice must never fail a turn
        logger.exception("remote MCP check failed for room %s", room_id)
        return ()
    return unusable
