"""The addresses a machine dials to reach this backend.

An address belongs to the dialer: which one a machine is handed depends on how
it reaches the backend, never on where the backend happens to listen.
"""

from urllib.parse import urlsplit

from app.core.config import settings
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply

# A private-control cloud machine's backend: the reverse SSH forward
# `deploy/cloud-control.py` opens onto the machine's own loopback. It lands on
# api-front, which routes the model tunnel as well as the backend.
CLOUD_LOOPBACK_BASE = "http://127.0.0.1:18080"
# The site itself over TLS, on the same machine's loopback: the forward
# `deploy/cloud-control.py` opens onto api-front's listener for it.
CLOUD_SITE_TLS_PORT = 18445


async def device_api_base(session, device_id: str, public_base: str) -> str:
    """The backend base that ``device_id`` dials, from configuration.

    The session host reaches the backend over its own configured base, a
    private-control cloud machine over loopback, and everything else over the
    public connector base.
    """
    if (
        device_id == settings.agent_session_device_id
        and settings.agent_session_api_base
    ):
        return settings.agent_session_api_base.rstrip("/")
    device = await session.get(DeviceRow, device_id)
    if device and device.supply == Supply.cloud and device.cloud_control_private:
        return CLOUD_LOOPBACK_BASE
    return public_base.rstrip("/")


def ws_url(base: str, route: str) -> str:
    """``ws(s)://…{route}`` on the base a machine already dials.

    Scheme-swapped rather than configured: the connector and the CLI all reach
    this origin already, so a socket that rides the same one needs no second
    address to keep true — and a deployment cannot end up with one pointed
    somewhere the machine was never able to reach.
    """
    base = base.rstrip("/")
    for http_scheme, ws_scheme in (("https://", "wss://"), ("http://", "ws://")):
        if base.startswith(http_scheme):
            base = ws_scheme + base[len(http_scheme) :]
            break
    return f"{base}{route}"


def tunnel_url(api_base: str) -> str:
    """Where the model tunnel helper on a machine dials: the tunnel route on the
    base that machine already dials, so the session host, a private-control
    cloud machine and every other machine each reach the tunnel the way they
    reach the backend, with no second address to keep true."""
    return ws_url(api_base, "/llm/tunnel")


def site_forward(api_base: str) -> str:
    """``host:port`` when a machine that dials ``api_base`` reaches the site
    (``frontend_url``) over TLS on that loopback port; empty otherwise.

    A private-control cloud machine's default route reaches the public name
    only through the Hong Kong relay, which sends the request straight back
    to this deployment through a tunnel. Its sessions' sandboxes resolve the
    name to the forward instead (``bootstrap.start_sandbox``); the origin,
    and so its certificate and cookies, stay the public one."""
    host = urlsplit(settings.frontend_url).hostname
    if api_base != CLOUD_LOOPBACK_BASE or not host:
        return ""
    return f"{host}:{CLOUD_SITE_TLS_PORT}"
