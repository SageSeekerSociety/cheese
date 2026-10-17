"""Model connections a person's account holds (#2991): an Anthropic API key,
or another model service that speaks Anthropic's API, entered on 「设置 →
模型连接」 without any machine.

Unlike a Claude subscription, whose login may not be collected or held by a
third party and so stays on the machine it was given on, a key may be kept by
the platform. It is sealed at rest (`app.core.crypto`) and opened in exactly
one place: as a session of its owner's own Claude Code on it starts on the
session host (`owner_provider.AccountChannel`), into that process's
environment. It is never returned, logged or sent anywhere but the service it
belongs to; its owner sees its last four characters.

A connection is checked with one cheap call before it is saved, and again
whenever its owner asks: Anthropic's model list for an API key (free), and a
one-token message for a model service, which is the only call every such
service answers. Its address is a person's to name, and both the backend and
the session host call it, so it must be https and outside this platform's own
network.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import Purpose, decrypt, encrypt
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.domain.agent_instance.models import AccountConnection
from app.domain.device.login_kind import ConnectionKind
from app.domain.integration.service import refuse_internal_host

#: Where an Anthropic API key is called.
ANTHROPIC_API = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
#: How long a check waits for the service.
_CHECK_TIMEOUT_S = 20
#: How much of a service's refusal is kept to show its owner.
_REASON_CHARS = 300

#: The kinds an account may hold. A subscription is not one of them.
HELD_KINDS = (ConnectionKind.api_key, ConnectionKind.model_service)


def _bound_to(row_id: uuid.UUID) -> str:
    return f"account-model-connection:{row_id}"


def key_of(row: AccountConnection) -> str:
    """The connection's key, opened. Only a session that runs on it reads this
    (`owner_provider.AccountChannel`) and the check below."""
    return decrypt(Purpose.MODEL_CONNECTION_KEY, row.key, bound_to=_bound_to(row.id))


def base_url_of(row: AccountConnection) -> str:
    return row.base_url or ANTHROPIC_API


def host_of(row: AccountConnection) -> str | None:
    """A model service's host, as its owner and the agent list show it."""
    return urlsplit(row.base_url).hostname if row.base_url else None


def refuse_unsafe_address(base_url: str) -> None:
    """Refuse an address a session or a check must not call: plain http, or a
    host that is this platform's own network. Blocking (it resolves the name)."""
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.hostname:
        raise UnprocessableEntityError(say("modelServiceNeedsHttps"))
    refuse_internal_host(parts.hostname, say("nounModelService"))


def _client() -> httpx.AsyncClient:
    # No redirects: a service that answers elsewhere would take the key there.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(_CHECK_TIMEOUT_S, connect=10), follow_redirects=False
    )


async def _ask(kind: ConnectionKind, base_url: str, key: str, model: str | None):
    """One cheap call the service has to answer with this key, made the way
    Claude Code will call it: an API key in ``x-api-key``, a model service's
    as a bearer token (``ANTHROPIC_AUTH_TOKEN``)."""
    root = base_url.rstrip("/")
    async with _client() as client:
        if kind is ConnectionKind.api_key:
            return await client.get(
                f"{root}/v1/models",
                params={"limit": 1},
                headers={"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION},
            )
        return await client.post(
            f"{root}/v1/messages",
            headers={
                "authorization": f"Bearer {key}",
                "anthropic-version": _ANTHROPIC_VERSION,
            },
            json={
                "model": model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )


async def probe(
    kind: ConnectionKind, base_url: str, key: str, model: str | None
) -> str | None:
    """Why the service refuses this connection, in its own words with the key
    blanked out; None when it answers."""
    await asyncio.to_thread(refuse_unsafe_address, base_url)
    try:
        response = await _ask(kind, base_url, key, model)
    except httpx.HTTPError as exc:
        reason = f"{type(exc).__name__}: {exc}".strip()
    else:
        if response.is_success:
            return None
        reason = f"HTTP {response.status_code}"
        try:
            message = (response.json().get("error") or {}).get("message")
        except (ValueError, AttributeError):
            message = None
        if isinstance(message, str) and message:
            reason += f" {message}"
    return reason.replace(key, "****")[:_REASON_CHARS]


async def of_owner(db: AsyncSession, owner_user_id: int) -> list[AccountConnection]:
    """The connections this person's account holds, oldest first."""
    rows = await db.scalars(
        select(AccountConnection)
        .where(AccountConnection.owner_user_id == owner_user_id)
        .order_by(AccountConnection.created_at)
    )
    return list(rows)


async def owned(
    db: AsyncSession, owner_user_id: int, connection_id: uuid.UUID
) -> AccountConnection | None:
    row = await db.get(AccountConnection, connection_id)
    return row if row is not None and row.owner_user_id == owner_user_id else None


async def create(
    db: AsyncSession,
    owner_user_id: int,
    *,
    kind: ConnectionKind,
    key: str,
    base_url: str | None,
    model: str | None,
    name: str | None,
) -> AccountConnection:
    """Keep a connection its owner entered, once the service has answered it.
    Refused with the service's reason, and nothing kept, when it does not."""
    if kind not in HELD_KINDS:
        raise UnprocessableEntityError(say("modelConnectionKindNotHeld"))
    if kind is ConnectionKind.api_key:
        base_url, model = None, None
    elif not (base_url and model):
        raise UnprocessableEntityError(say("modelServiceIncomplete"))
    reason = await probe(kind, base_url or ANTHROPIC_API, key, model)
    if reason is not None:
        raise UnprocessableEntityError(say("modelConnectionRefused", reason=reason))
    row = AccountConnection(
        id=uuid.uuid4(),
        owner_user_id=owner_user_id,
        kind=kind.value,
        name=name or None,
        base_url=base_url,
        model=model,
        key_last4=key[-4:],
        checked_at=datetime.now(UTC),
    )
    row.key = encrypt(Purpose.MODEL_CONNECTION_KEY, key, bound_to=_bound_to(row.id))
    db.add(row)
    await db.flush()
    return row


async def check(db: AsyncSession, row: AccountConnection) -> None:
    """Ask the service again, and record when and what it answered."""
    row.check_error = await probe(
        ConnectionKind(row.kind), base_url_of(row), key_of(row), row.model
    )
    row.checked_at = datetime.now(UTC)
    await db.flush()


def view(row: AccountConnection) -> dict:
    """The connection as its owner reads it. Never the key."""
    return {
        "id": str(row.id),
        "kind": row.kind,
        "name": row.name,
        "key_last4": row.key_last4,
        "service_host": host_of(row),
        "model": row.model,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "check_error": row.check_error,
        "created_at": row.created_at.isoformat(),
    }
