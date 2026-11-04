"""Whether a room's running app is reachable: the tunnel, and the app behind it.

Both the preview read (`routes/topics_preview.py`) and the panel's wake progress
(`routes/topics_preview_app.py`) knock on the same thing. Route modules may not
import each other (C4 `routes-independent`), so the knock lives here, beside the
other halves the content host and the routes share.
"""

import uuid
from dataclasses import dataclass

from app.core.config import settings
from app.domain.agent.preview_hub import preview_hub
from app.domain.agent.preview_owner import inspect_owner


@dataclass(frozen=True)
class AppState:
    #: The teammate's machine holds its tunnel open.
    tunnel_up: bool
    #: The app answered a probe through that tunnel.
    alive: bool
    instance: str | None


async def app_state(topic_id: uuid.UUID, seat: str) -> AppState:
    """Knocked LIVE, through the tunnel, every time. A declared preview is not a
    running one: the agent's dev server exits, the machine goes offline, the
    helper's token ages out — and each of those renders as a white iframe unless
    the two states are reported apart. `tunnel_up` without `alive` is
    「通道在，应用没在跑」."""
    if settings.preview_connection_mode == "owner":
        inspection = await inspect_owner(topic_id, seat, probe=True)
        return AppState(inspection.tunnel_up, inspection.alive, inspection.instance)
    tunnel_up = preview_hub.is_online(topic_id, seat)
    alive = tunnel_up and await preview_hub.probe(topic_id, seat)
    instance = await preview_hub.instance(topic_id, seat) if alive else None
    return AppState(tunnel_up, alive, instance)
