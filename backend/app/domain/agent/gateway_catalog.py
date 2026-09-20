"""What the platform pool offers, as the gateway reports it.

The gateway is the only thing that knows: a model it does not route cannot be
run, whatever any list in this codebase says. So cheese keeps no list of its
own — it keeps the gateway's last answer, and a floor for when there has never
been one.

Read it synchronously (``snapshot``) and refresh it on a timer (``refresh``).
That split is the point: ``model_choices`` is on the path that starts a turn and
validates an agent, so it must not make a network call, and its callers must not
have to become async to ask what models exist.

**A failed refresh never shrinks the catalogue.** An empty list and an
unreachable gateway look identical to a picker and are opposites in fact; the
first is "this deployment serves nothing", the second is "ask again later".
Confusing them takes every agent on the deployment offline for the length of a
blip.
"""

import asyncio
import logging
from dataclasses import dataclass

from app.core.config import settings
from app.domain.agent.gateway import GatewayModel, LlmGateway

logger = logging.getLogger(__name__)

# How stale the catalogue may get before a refresh is due. Adding a model is a
# deliberate act by an operator who can wait a few minutes to see it; polling
# faster buys nothing and costs a request per interval forever.
REFRESH_INTERVAL_SECONDS = 300.0


@dataclass
class _State:
    models: list[GatewayModel] | None = None


_state = _State()


def _floor() -> list[GatewayModel]:
    """What to offer when the gateway has never answered.

    ``settings.agent_model`` is the model this deployment is configured to run,
    so it is the one thing we can name without asking anyone. A deployment that
    never configured the admin API (``llm_gateway_admin_base`` is optional) lives
    here permanently and keeps working — on exactly the model it was set up for,
    rather than on a list this file would otherwise be inventing.
    """
    return [
        GatewayModel(
            id=settings.agent_model,
            label=settings.agent_model,
            selectable=True,
            priced=True,
        )
    ]


def snapshot() -> list[GatewayModel]:
    """The models to offer right now. Never empty, never a network call."""
    known = _state.models
    if known is None:
        return _floor()
    return known


def offerable() -> list[GatewayModel]:
    """The models a person may actually be offered.

    Two conditions, and the second is the one worth stating. A model the gateway
    cannot bill is metered at zero, so a project's ``max_budget`` never trips and
    the only brake on its spend is the invoice. Leaving such a model out means a
    forgotten price shows up as a model missing from the menu — which someone
    notices — instead of as a budget that silently stopped working, which nobody
    does until it has cost money.

    Falls back to the floor when nothing qualifies, so a deployment whose gateway
    is misconfigured still runs on the model it was configured with rather than
    offering nothing at all.
    """
    picked = [m for m in snapshot() if m.selectable and m.priced]
    return picked or _floor()


async def refresh(gateway: LlmGateway | None) -> bool:
    """Ask the gateway what it routes. True when the catalogue was updated.

    A gateway that is absent or unreachable leaves the last answer in place —
    including the very first time, when the last answer is the floor.
    """
    if gateway is None:
        return False
    models = await gateway.models()
    if models is None:
        return False
    _state.models = models
    return True


def reset() -> None:
    """Drop the cache — the seam a test uses to say what the catalogue knows."""
    _state.models = None


async def keep_fresh(gateway: LlmGateway | None) -> None:
    """Refresh the catalogue forever, starting now.

    Runs as a background task so nothing on the request path ever waits on the
    gateway. The first pass is the important one: until it lands, a deployment
    WITH a gateway is serving the floor, which is correct but narrower than what
    it actually routes.
    """
    if gateway is None:
        return
    while True:
        try:
            await refresh(gateway)
        except Exception:  # noqa: BLE001 — a refresh must never kill its own loop
            logger.warning("gateway catalogue refresh failed", exc_info=True)
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
