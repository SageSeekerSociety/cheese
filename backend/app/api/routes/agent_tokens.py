"""Agent tokens: how a member lets their OWN agent onto the platform.

Before this existed, an agent a member ran themselves (a local Claude Code, a
bot) had no way to hold a credential of its own. The only way in was to reuse
the human's session token, which made the two literally the same account — the
agent's messages were indistinguishable from the person's, and @-ing your own
agent was @-ing yourself, which the mention path filters out.

So: the human issues a token here (their own session authenticates the issue),
and that token authenticates as a *separate* agent-user bound to them. Writes
carry the agent's handle; authorization weighs the owner, so the agent can reach
exactly what its owner can and nothing more.

The secret is returned once, at issue. Only its SHA-256 lives in the database,
so a lost token is re-issued, never recovered.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.db import get_db
from app.domain.identity.models import AgentToken
from app.domain.identity.services import (
    DEFAULT_TOKEN_TTL_DAYS,
    MAX_TOKEN_TTL_DAYS,
    AgentTokenService,
)

router = APIRouter(prefix="/api/agent-tokens", tags=["agent-tokens"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AuthUser = Annotated[AuthUserInfo, Depends(require_auth_user)]


class IssueAgentTokenRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # What this credential is for ("我的 MacBook"), so a user revoking one knows
    # which machine they are cutting off.
    name: str = Field(default="", max_length=64)
    expires_in_days: int = Field(
        default=DEFAULT_TOKEN_TTL_DAYS,
        alias="expiresInDays",
        ge=1,
        le=MAX_TOKEN_TTL_DAYS,
    )


def _token_out(row: AgentToken) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "tokenPrefix": row.token_prefix,
        "expiresAt": row.expires_at,
        "revokedAt": row.revoked_at,
        "lastUsedAt": row.last_used_at,
        "createdAt": row.created_at,
    }


@router.get("/whoami")
async def whoami(resolver: ActorResolverDep) -> dict:
    """Who the presented credential acts as.

    Exists for the agent, not the browser: the first thing a freshly configured
    agent needs is confirmation that its token works and which handle its writes
    will carry. Without this the only way to find out is to post something.
    """
    actor = await resolver.resolve(fallback_handle=None)
    return ok(
        {
            "handle": actor.handle,
            "userId": actor.user_id,
            "isAgent": actor.is_agent,
            "via": actor.via,
            "ownerHandle": actor.owner_handle,
            "authenticated": actor.authenticated,
        }
    )


@router.post("")
async def issue_agent_token(
    body: IssueAgentTokenRequest, auth_user: AuthUser, db: DbSession
) -> dict:
    issued = await AgentTokenService(db).issue(
        owner_user_id=auth_user.user_id,
        name=body.name,
        ttl_days=body.expires_in_days,
    )
    return ok(
        {
            # The one and only time this value is readable.
            "token": issued.secret,
            "agentHandle": issued.agent_handle,
            **_token_out(issued.row),
        },
        "token 只显示这一次，请立即保存",
    )


@router.get("")
async def list_agent_tokens(auth_user: AuthUser, db: DbSession) -> dict:
    rows = await AgentTokenService(db).list_for_owner(auth_user.user_id)
    return ok(page([_token_out(r) for r in rows], len(rows)))


@router.delete("/{token_id}")
async def revoke_agent_token(
    token_id: uuid.UUID, auth_user: AuthUser, db: DbSession
) -> dict:
    row = await AgentTokenService(db).revoke(
        token_id=token_id, owner_user_id=auth_user.user_id
    )
    return ok(_token_out(row))
