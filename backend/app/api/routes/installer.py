"""Convenient installer for the frozen ``cli/`` client (architecture §5.5).

Two public, unauthenticated endpoints, both under ``/connector`` so they sit next
to the agent front door and are reached at ``<origin>/connector/...``:

* ``GET /connector/install.sh`` — the one-line installer. A fresh machine runs
  ``curl -fsSL <origin>/connector/install.sh | sh``; the script fetches the right
  prebuilt binary (and a private tmux) from this same origin and records the
  server URL. It carries no secrets and no business logic — enrollment still goes
  through the device flow — so serving it openly is safe.
* ``GET /connector/latest/{target}/{artifact}`` — the prebuilt artifacts
  (``cheese`` / ``tmux``) for one ``<os>-<arch>`` target, served from
  ``settings.connector_dist_dir`` (published by CI from the frozen ``cli/``
  source). Returns 404 with a clear hint until that directory is populated.

The connector base URL baked into the served script is resolved from
``settings.connector_origin`` when set, else derived from the request — relative
to however the script was reached, so the binaries resolve behind any edge
prefix.
"""

import os
import re

import aiofiles
import aiofiles.ospath
from fastapi import APIRouter, Request, Response
from starlette.responses import FileResponse, PlainTextResponse

from app.common.origin import public_origin
from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError

router = APIRouter(prefix="/connector", tags=["connector"])

_INSTALL_SH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "agent", "install", "install.sh")
_INSTALL_SH = os.path.normpath(_INSTALL_SH)

# <os>-<arch>, the only shape the installer ever requests.
_TARGET_RE = re.compile(r"^(?:linux|darwin)-(?:amd64|arm64)$")
# The only artifacts published per target; keeps the served path off the filesystem's leash.
_ARTIFACTS = {"cheese", "tmux"}


def _connector_base(request: Request) -> str:
    """The ``…/connector`` URL this deployment is reachable at, baked into the served
    ``install.sh`` (the cli's ``base`` is this minus ``/connector``).

    Derived from the origin the request actually came in on: ``<origin>/api/connector``.
    Since the frontend builds the install command from ``window.location.origin`` and the
    edge always mounts the backend at ``<origin>/api`` (CLAUDE.md), this keeps the served
    base correct under any host/port/proxy with nothing configured."""
    return public_origin(request) + "/api/connector"


# Also served at the origin root so `curl <origin>/install.sh | sh` works (the
# short, memorable form). The binary base is still resolved from connector_origin,
# so artifacts keep coming from `<origin>/connector/latest/...`.
root_router = APIRouter(tags=["connector"])


async def _serve_install_sh(request: Request) -> Response:
    async with aiofiles.open(_INSTALL_SH) as f:
        script = await f.read()
    script = script.replace("__CONNECTOR_BASE__", _connector_base(request))
    # text/x-shellscript so `curl … | sh` and a browser both do the sane thing.
    return PlainTextResponse(script, media_type="text/x-shellscript")


@router.get("/install.sh")
async def install_script(request: Request) -> Response:
    return await _serve_install_sh(request)


@root_router.get("/install.sh")
async def install_script_root(request: Request) -> Response:
    return await _serve_install_sh(request)


@router.get("/latest/{target}/{artifact}")
async def client_artifact(target: str, artifact: str) -> Response:
    if not _TARGET_RE.match(target):
        raise BadRequestError(f"Unknown target: {target} (expected <os>-<arch>, e.g. linux-amd64)")
    if artifact not in _ARTIFACTS:
        raise BadRequestError(f"Unknown artifact: {artifact} (expected one of {sorted(_ARTIFACTS)})")
    if not settings.connector_dist_dir:
        raise NotFoundError("No client distribution is published (CONNECTOR_DIST_DIR is unset)")

    path = os.path.join(settings.connector_dist_dir, target, artifact)
    if not await aiofiles.ospath.exists(path):
        raise NotFoundError(f"No published {artifact} for {target}")
    # Stream from disk (FileResponse) rather than reading a multi-MB binary into
    # memory per request; it also sets the attachment Content-Disposition.
    return FileResponse(path, media_type="application/octet-stream", filename=artifact)
