"""Who a delivery is credited to, with what only the forge knows filled in.

`identity.attribution` resolves the people from the platform's own rows. This
adds the forge: where the project's code lives (`lane`), and on GitHub the
App's bot account — the AI teammate there, shown with the App's logo — and
whose account opened the PR, which GitHub makes the author of the squash.
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
    missing co-author line is not worth failing a merge or a commit over."""
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


async def _forge(session: Any, project_id: Any) -> tuple[str | None, Any]:
    """The project's lane and, on GitHub, the App's identity."""
    # deferred-import: tests replace these names on app.domain.project.forge
    from app.domain.project.forge import binding_for_project, tokens_for_project

    try:
        binding = await binding_for_project(project_id, session)
        if binding is None:
            return None, None
        if binding.kind == "forgejo":
            return "forgejo", None
        if binding.kind != "github_app":
            return None, None
        tokens = await tokens_for_project(project_id, session)
        if tokens is None:
            return "github", None
        token, _ = await tokens.installation_token()
        return "github", await app_identity(token, api_base=binding.api_url)
    except Exception:  # a credit, not a gate
        logger.warning("could not read the forge of %s", project_id, exc_info=True)
        return None, None


async def attribution(
    session: Any,
    topic: Any,
    *,
    opened_by: str | None = None,
    card: Any = None,
    decided_by: str | None = None,
    task_id: Any = None,
) -> identity.Attribution:
    """`identity.attribution` with the forge filled in. `opened_by` is the
    address of the person whose account opened the PR, when one did."""
    lane, app = await _forge(session, topic.project_id)
    return await identity.attribution(
        session,
        topic,
        card=card,
        decided_by=decided_by,
        task_id=task_id,
        opened_by=opened_by,
        app=app,
        lane=lane,
    )
