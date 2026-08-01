"""The model endpoint a machine talks to.

A machine already reaches the backend — hooks, the `cheese` CLI and git
smart-HTTP all go through it — so the gateway needs no public address of its
own. Proxying it here instead of exposing it adds no attack surface, reuses the
scoped token the machine already carries, and keeps the gateway's admin API
(key minting, spend, budgets) on the box where it belongs.

The reason this matters beyond tidiness: today a machine is handed the raw
upstream key in its environment, in plain sight of anyone on that host, and its
spend lands in the provider's bill under one undifferentiated key. Here the
backend swaps in the PROJECT's virtual key, so the upstream credential never
leaves the box and every call is attributed to a project without the machine
having to be trusted to say so.
"""

import uuid

import httpx
from fastapi import APIRouter, Header, Request, Response

from app.api.deps import get_chat_service
from app.core.config import settings
from app.core.errors import AuthenticationRequiredError, ValidationError
from app.core.sandbox_auth import verify_scoped_token

router = APIRouter(prefix="/api/llm", tags=["llm"])

# Anthropic's surface, which is what Claude Code speaks. Nothing else is
# forwarded: the admin paths (/key/*, /spend/*, /model/*) must stay unreachable
# from a machine, and an allowlist keeps that true as the gateway grows.
_ALLOWED = {"v1/messages", "v1/messages/count_tokens"}


@router.post("/{path:path}")
async def proxy(
    path: str,
    request: Request,
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
    x_cheese_project: str | None = Header(default=None, alias="X-Cheese-Project"),
) -> Response:
    if path.strip("/") not in _ALLOWED:
        raise ValidationError(f"not proxied: {path}")
    if not x_cheese_project:
        raise AuthenticationRequiredError("missing project")
    try:
        project_id = uuid.UUID(x_cheese_project)
    except ValueError as exc:
        raise ValidationError("bad project id") from exc
    if not x_cheese_token or not verify_scoped_token(
        x_cheese_token, project_id=str(project_id)
    ):
        raise AuthenticationRequiredError("this project's token is required")

    # The project's virtual key — minted on first use, budget kept in step with
    # the project's grants. Without it there is nothing to bill against, so a
    # failure here must NOT fall back to the upstream key: that is exactly the
    # unattributed spend this route exists to end.
    chat = get_chat_service()
    env = await chat._gateway_project_env(project_id)
    key = (env or {}).get("ANTHROPIC_AUTH_TOKEN")
    if not key:
        raise ValidationError("no gateway key for this project")

    base = (settings.llm_gateway_admin_base or "").rstrip("/")
    async with httpx.AsyncClient(timeout=600.0) as client:
        upstream = await client.post(
            f"{base}/{path.lstrip('/')}",
            content=await request.body(),
            headers={
                "content-type": request.headers.get("content-type", "application/json"),
                "anthropic-version": request.headers.get(
                    "anthropic-version", "2023-06-01"
                ),
                "x-api-key": key,
            },
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
