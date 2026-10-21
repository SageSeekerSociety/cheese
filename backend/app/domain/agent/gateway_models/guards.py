"""What a write must not take away: the checks that run before a model stops
being served.

A model stops being served when it is deleted or paused, or when the last
source still serving it is. `deploy/gateway/config.yaml` used to be the one
place the platform's models were declared, and `check_config.py` held that file
to "some model is offered"; runtime rows are held to it here, at the write that
would break it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import model_roles
from app.core.errors import BadRequestError
from app.core.sentences import listing, say
from app.domain.agent.gateway_admin import AdminModel
from app.domain.agent.gateway_models import roles
from app.domain.agent.gateway_models_helpers import _offered, _require_another_offered

if TYPE_CHECKING:
    from app.domain.agent import gateway_models as pkg


def _serves_without(model: AdminModel, source_id: str) -> bool:
    """Is a source other than ``source_id`` still serving ``model``."""
    return any(s.source_id != source_id and not s.blocked for s in model.sources)


async def _refuse_unserving(
    service: pkg.GatewayModelsService, model: AdminModel
) -> None:
    """``model`` is about to stop being served: refuse while a platform model
    role still runs on it (repoint the role first), or if that leaves the
    platform with no offered model."""
    await roles.refresh(service._db)
    pointing = model_roles.pointing_at(model.name)
    if pointing:
        raise BadRequestError(
            say("gatewayModelHasRoles", name=model.name, roles=listing(pointing))
        )
    if _offered(model):
        _require_another_offered(await service._require_models(), model.name)


async def _refuse_unserving_source(
    service: pkg.GatewayModelsService, model: AdminModel, source_id: str
) -> None:
    """``source_id`` is about to stop serving: the same check, when it is the
    last source serving ``model``."""
    if not _serves_without(model, source_id):
        await _refuse_unserving(service, model)


async def _refuse_unlisting(
    service: pkg.GatewayModelsService, model: AdminModel
) -> None:
    """``model`` is about to stop being offered while still served (unlisted):
    refuse while a role that only takes offered models (the default model, the
    subagent default) runs on it, since every project would be offered a model
    the picker no longer lists."""
    await roles.refresh(service._db)
    pointing = [
        role
        for role in model_roles.pointing_at(model.name)
        if model_roles.ROLES[role].offered
    ]
    if pointing:
        raise BadRequestError(
            say("gatewayModelHasRoles", name=model.name, roles=listing(pointing))
        )
