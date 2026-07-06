"""agent_session token: a short-lived JWT that carries a ProjectActor.

The connector/tool-RPC surface trusts this token at exactly ONE point (the
route) to resolve the acting identity — the actor never comes from the request
body. Minted by the orchestrator when it starts/drives an agent; here we only
mint and verify, reusing the platform's JWT secret + ``type``-claim convention.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.agent.authorization.authorizer import ProjectActor
from app.core.config import settings
from app.core.errors import AuthenticationRequiredError

_TOKEN_TYPE = "agent_session"
_DEFAULT_TTL_SECONDS = 3600


def mint_agent_session(actor: ProjectActor, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "type": _TOKEN_TYPE,
        "kind": actor.kind,
        "actor_id": actor.actor_id,
        "project_id": actor.project_id,
        "on_behalf_of": actor.on_behalf_of_user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_agent_session(token: str) -> ProjectActor:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # type: ignore[attr-defined]
        raise AuthenticationRequiredError("Invalid or expired agent session token") from exc
    if payload.get("type") != _TOKEN_TYPE:
        raise AuthenticationRequiredError("not an agent session token")
    kind = payload.get("kind")
    if kind not in ("user", "agent"):
        raise AuthenticationRequiredError("invalid actor kind in agent session token")
    try:
        actor_id = int(payload["actor_id"])
        project_id = int(payload["project_id"])
        on_behalf = payload.get("on_behalf_of")
        on_behalf_id = int(on_behalf) if on_behalf is not None else None
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationRequiredError("malformed agent session token") from exc
    return ProjectActor(
        kind=kind,
        actor_id=actor_id,
        project_id=project_id,
        on_behalf_of_user_id=on_behalf_id,
    )
