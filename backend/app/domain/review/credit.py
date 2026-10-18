"""Who a delivery is credited to, once the PR that carries it is known.

`identity.attribution` resolves the people from the platform's own rows; the
PR adds what only GitHub knows: whose account opened it, which decides who the
squash commit is by, and the App's bot account, credited beside a human author.
"""

import logging
from typing import Any
from urllib.parse import quote

from app.core.config import settings
from app.core.forge_http import forge_client
from app.core.github_headers import github_headers
from app.domain.repository import identity

logger = logging.getLogger("cheesex.review.credit")


async def app_identity(
    token: str, *, api_base: str = "https://api.github.com"
) -> identity.GitIdentity | None:
    """The App's bot account, read from GitHub. None when it cannot be read: a
    missing co-author line is not worth failing a merge over."""
    path = quote(f"{settings.github_app_slug}[bot]")
    try:
        async with forge_client(timeout=20.0) as client:
            resp = await client.get(
                f"{api_base.rstrip('/')}/users/{path}", headers=github_headers(token)
            )
        if resp.status_code == 200 and resp.json().get("id"):
            return identity.app_identity(resp.json()["id"])
    except Exception:  # a trailer, not a gate
        logger.warning("could not read the App's bot account", exc_info=True)
    return None


async def attribution(
    session: Any,
    topic: Any,
    *,
    opened_by: str | None,
    token: str | None,
    card: Any = None,
    decided_by: str | None = None,
    task_id: Any = None,
) -> identity.Attribution:
    """`identity.attribution` for a PR whose opener is `opened_by`."""
    app = await app_identity(token) if opened_by and token else None
    return await identity.attribution(
        session,
        topic,
        card=card,
        decided_by=decided_by,
        task_id=task_id,
        opened_by=opened_by,
        app=app,
    )
