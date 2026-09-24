"""What a session machine fetches or triggers: the CLI, the storage sweep.

It lives OUTSIDE /api on purpose: the cheese_token_gate middleware only guards
/api write paths, so these routes do their own token check.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.errors import UnauthorizedError
from app.core.sandbox_auth import scoped_token_claims

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.post("/storage-sweep")
async def trigger_storage_sweep(x_cheese_token: str = Header(default="")) -> dict:
    from app.core.background import spawn
    from app.core.db import async_session_factory
    from app.core.sandbox_auth import is_global_sandbox_token
    from app.domain.topic.retire import sweep_retired_storage

    if not is_global_sandbox_token(x_cheese_token):
        raise UnauthorizedError("Cleanup trigger requires the server credential")
    spawn(sweep_retired_storage(async_session_factory), name="archived-room cleanup")
    return {"code": 200, "data": {"scheduled": True}}


# The `cheese` platform-action CLI source, shipped to enrolled devices (the local
# sandbox bakes it into its own image instead). Loaded LAZILY and defensively:
# this module is imported by route auto-discovery, which SWALLOWS import errors —
# a module-level read of a file the image may not carry silently unmounted the
# whole sandbox router on dev. Import must never depend on it.
_CLI_PATH = Path(__file__).resolve().parents[3] / "sandbox" / "cheese"


def _cheese_cli_source() -> str | None:
    try:
        return _CLI_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("cheese CLI source unavailable at %s", _CLI_PATH)
        return None


@router.get("/cli/cheese", response_model=None)
async def get_cheese_cli(
    x_cheese_token: str = Header(default=""),
) -> PlainTextResponse | JSONResponse:
    """Serve the `cheese` platform-action CLI to an enrolled device's screen launcher
    (a device has no baked-in image). Gated by any valid scoped
    token — it carries no data, just the script; the token still authorizes the
    ACTIONS the CLI performs."""
    if not scoped_token_claims(x_cheese_token):
        return JSONResponse(
            {"code": 401, "message": "invalid sandbox token", "data": None},
            status_code=401,
        )
    src = _cheese_cli_source()
    if src is None:
        return JSONResponse(
            {
                "code": 503,
                "message": "cheese CLI not shipped in this image",
                "data": None,
            },
            status_code=503,
        )
    return PlainTextResponse(src, media_type="text/x-python")
