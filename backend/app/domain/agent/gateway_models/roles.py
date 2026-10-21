"""Platform model roles on the admin page: read them, point them, and keep
this process's view of them warm (``app.core.model_roles`` is what the rest of
the platform reads).

Pointing a role is a gateway-side change as much as a database one: the
virtual keys minted for the role's job allow exactly one model
(``service_keys.KeySpec``), so a role moved to another model is refused by the
gateway until its key allows the new one. The key is widened to both models
first, the choice is stored, and the key is narrowed to the new model only
once every process has read the new choice (after
``Settings.platform_model_roles_refresh_interval_s`` plus
``NARROW_MARGIN_SECONDS``): narrowed at once, a process still on the old model
would be refused until its next refresh. A failure before the
store changes nothing; a narrowing that never happens (the process ended, the
gateway refused) leaves a key that allows one model too many, which is safe
and is logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core import background, model_roles
from app.core.config import settings
from app.core.db import SessionFactory
from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.agent.gateway_admin import AdminModel, GatewayAdmin
from app.domain.agent.gateway_models._shared import _WRITE_ERRORS
from app.domain.agent.gateway_models_helpers import _billable, _offered
from app.domain.agent.models import PlatformModelRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.agent import gateway_models as pkg

logger = logging.getLogger(__name__)

# When the key is narrowed to the new model: after every process's next refresh.
NARROW_MARGIN_SECONDS = 15.0


async def _narrow_later(
    admin: GatewayAdmin, role: str, model: str, aliases: tuple[str, ...]
) -> None:
    """Narrow the role's keys to ``model`` once every process has read it, if the
    role still runs on it then (another change may have moved it again)."""
    await asyncio.sleep(
        settings.platform_model_roles_refresh_interval_s + NARROW_MARGIN_SECONDS
    )
    try:
        # By now this process's periodic refresh has read whatever was stored
        # since; a role moved again meanwhile is left wide for its own narrowing.
        if model_roles.get(role) != model:
            return
        for alias in aliases:
            await admin.set_key_models(alias, [model])
    except Exception:  # a background step: log it, the key stays wide (safe)
        logger.warning("narrowing the %s keys failed", role, exc_info=True)


async def refresh(db: AsyncSession) -> None:
    """Read the stored choices into this process's view of the roles."""
    rows = (await db.scalars(select(PlatformModelRole))).all()
    model_roles.install({row.role: row.model for row in rows})


async def refresh_from(sessions: SessionFactory) -> None:
    """The periodic refresh (``app/periodic_jobs.py``, every process): read the
    stored choices with a session of its own."""
    async with sessions() as db:
        await refresh(db)


async def listing(service: pkg.GatewayModelsService) -> dict:
    await refresh(service._db)
    return {
        "roles": [
            {
                "role": role,
                "model": model_roles.get(role),
                "fallback": model_roles.fallback(role),
                "chosen": model_roles.chosen(role),
                "offered_only": spec.offered,
            }
            for role, spec in model_roles.ROLES.items()
        ]
    }


def _check_target(role: str, model: AdminModel | None, name: str) -> None:
    if model is None:
        raise NotFoundError(say("gatewayModelNotFound", name=name))
    if model_roles.ROLES[role].offered:
        if not _offered(model):
            raise BadRequestError(say("roleNeedsOfferedModel", name=name))
    elif model.blocked or not _billable(model):
        raise BadRequestError(say("roleNeedsServedModel", name=name))


async def set_role(
    service: pkg.GatewayModelsService, *, handle: str, role: str, model: str | None
) -> dict:
    """Point ``role`` at ``model``; None goes back to its ``Settings`` field."""
    if role not in model_roles.ROLES:
        raise NotFoundError(say("roleNotFound", role=role))
    await refresh(service._db)
    before = {"model": model_roles.get(role)}
    after: dict = {"model": model or model_roles.fallback(role)}

    async def work() -> dict:
        new = model or model_roles.fallback(role)
        if model is not None:
            models = await service._require_models()
            _check_target(
                role, next((m for m in models if m.name == model), None), model
            )
        old = before["model"]
        aliases = model_roles.ROLES[role].keys
        allowed = [m for m in dict.fromkeys([old, new]) if m]
        for alias in aliases:
            await service._admin.set_key_models(alias, allowed)  # type: ignore[union-attr]
        row = await service._db.get(PlatformModelRole, role)
        if model is None:
            if row is not None:
                await service._db.delete(row)
        elif row is None:
            service._db.add(PlatformModelRole(role=role, model=model))
        else:
            row.model = model
        await service._db.commit()
        await refresh(service._db)
        if aliases and new and old != new:
            background.spawn(
                _narrow_later(service._admin, role, new, aliases),  # type: ignore[arg-type]
                name=f"narrow {role} keys",
            )
        return {"role": role, "model": model_roles.get(role)}

    try:
        out = await work()
    except _WRITE_ERRORS as exc:
        await service._record(
            handle=handle,
            action="role.set",
            target=role,
            result="failed",
            detail=str(exc),
            before=before,
            after=after,
        )
        raise
    await service._record(
        handle=handle,
        action="role.set",
        target=role,
        result="ok",
        before=before,
        after=after,
    )
    return out
