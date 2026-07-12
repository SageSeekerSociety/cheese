"""Self-hosted connector install flow (fusion-design §5.1).

Serves the `cheesehost` thin-client connector so a fresh user machine can join
with one line — `curl -fsSL <origin>/connector/install.sh | sh`. The script
detects the platform, downloads the matching binary, and prints the next step
(`cheesehost auth login`). It carries no secrets and no business logic;
enrollment still goes through the device flow (routes/connector.py), so a piped
installer never grants access by itself.

Binaries are built artifacts under backend/connector-dist/ (git-ignored; built
by scripts/build-connector.sh from the main repo's cli/). A target with no built
binary 404s until a build runs — the endpoint degrades, never crashes.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.core.config import settings

router = APIRouter(prefix="/connector", tags=["connector"])

# Only these <os>-<arch> pairs are served; the path is validated against this
# set so a request can never escape the dist dir.
_TARGETS = {"darwin-arm64", "darwin-amd64", "linux-arm64", "linux-amd64"}
_TARGET_RE = re.compile(r"^(darwin|linux)-(amd64|arm64)$")


def _dist_dir() -> Path:
    # backend/connector-dist relative to this file (app/api/routes/installer.py).
    return Path(__file__).resolve().parents[3] / "connector-dist"


def _origin(request: Request) -> str:
    """The externally-reachable base URL the installed agent talks back to.

    The script runs on the user's machine and must reach the *same* host they
    downloaded it from, so the incoming request's own base URL is the correct
    default — it's right regardless of which port/host actually serves us. Only
    when `connector_public_base` is set to a real public origin (i.e. NOT the
    localhost dev default) do we prefer it, so a deployment behind an edge/proxy
    can pin the externally-reachable name."""
    configured = settings.connector_public_base.rstrip("/")
    if configured and "localhost" not in configured and "127.0.0.1" not in configured:
        return configured
    return str(request.base_url).rstrip("/")


@router.get("/install.sh")
async def install_script(request: Request) -> PlainTextResponse:
    origin = _origin(request)
    script = f"""#!/bin/sh
# cheesehost installer — connects this machine to CheeseX so your sessions can
# run here. No secrets; enrollment is the device flow (cheesehost auth login).
set -eu
ORIGIN="{origin}"
os=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$os" in
  linux) os=linux ;;
  darwin) os=darwin ;;
  *) echo "unsupported OS: $os"; exit 1 ;;
esac
arch=$(uname -m)
case "$arch" in
  x86_64|amd64) arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo "unsupported arch: $arch"; exit 1 ;;
esac
target="$os-$arch"
dest="$HOME/.local/bin"
mkdir -p "$dest"
echo "downloading cheesehost ($target)…"
curl -fsSL "$ORIGIN/connector/latest/$target/cheesehost" -o "$dest/cheesehost"
chmod +x "$dest/cheesehost"
echo "installed to $dest/cheesehost"
case ":$PATH:" in *":$dest:"*) : ;; *) echo "add $dest to your PATH" ;; esac
echo "next: cheesehost auth login $ORIGIN/connector"
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


@router.get("/latest/{target}/cheesehost")
async def download_binary(target: str) -> Response:
    if not _TARGET_RE.match(target) or target not in _TARGETS:
        return PlainTextResponse("unknown target", status_code=404)
    binary = _dist_dir() / target / "cheesehost"
    if not binary.is_file():
        return PlainTextResponse(
            "binary not built for this target — run scripts/build-connector.sh",
            status_code=404,
        )
    return FileResponse(
        binary,
        media_type="application/octet-stream",
        filename="cheesehost",
    )
