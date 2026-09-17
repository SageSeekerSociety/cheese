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

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.core.config import settings
from app.domain.agent import connector_build, toolchain
from app.domain.machine import claude_dist, pi_dist, toolchain_dist

router = APIRouter(prefix="/connector", tags=["connector"])

# Only these <os>-<arch> pairs are served; the path is validated against this
# set so a request can never escape the dist dir. They come from
# `connector_build`, which hashes the very same files to decide whether a
# connected machine is running them — one list, so the two can never disagree
# about what a target is.
_TARGETS = connector_build.TARGETS
_TARGET_RE = connector_build.TARGET_RE
_dist_dir = connector_build.dist_dir


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


def _ws_control_url(origin: str) -> str:
    """The pre-derived control-channel URL for a WS-stripping edge, or "".

    ``connector_ws_overrides`` maps the friendly install origin to a plain
    http(s) origin that CAN carry WebSockets. Everything else (login, approve
    page, binary downloads, hook POSTs) stays on the friendly origin — only the
    persistent control channel `cheesehost run` dials out on is redirected, by
    pre-writing the cli's "ws" config key (dialed verbatim; `cheesehost auth
    login` preserves it). Empty → the cli derives wss://<base>/agent itself."""
    override = settings.connector_ws_overrides.get(
        origin.rstrip("/")
    ) or settings.connector_ws_overrides.get(origin.rstrip("/") + "/", "")
    if not override:
        return ""
    override = override.rstrip("/")
    if override.startswith("https://"):
        ws = "wss://" + override.removeprefix("https://")
    elif override.startswith("http://"):
        ws = "ws://" + override.removeprefix("http://")
    else:  # already ws(s):// — use as-is
        ws = override
    # The cli's base carries the /connector path, so the control channel it
    # would derive lives at /connector/agent — mirror that on the override.
    return ws + "/connector/agent"


@router.get("/install.sh")
async def install_script(request: Request) -> PlainTextResponse:
    origin = _origin(request)
    ws_url = _ws_control_url(origin)
    script = f"""#!/bin/sh
# cheesehost installer — connects this machine to CheeseX so your sessions can
# run here. No secrets; enrollment is the device flow (cheesehost auth login).
set -eu
ORIGIN="{origin}"
WS_URL="${{CHEESE_WS_URL:-{ws_url}}}"
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
# WS-stripping edge (e.g. a campus front proxy that only forwards HTTP): the
# server baked a WS-capable control-channel URL above. Pre-write it into the
# cli config's "ws" key — `cheesehost auth login` loads-then-saves, so it
# survives login. Login/approve/API/downloads all stay on ORIGIN.
if [ -n "$WS_URL" ]; then
  CFG_DIR="${{XDG_CONFIG_HOME:-$HOME/.config}}/cheese"
  CFG="$CFG_DIR/config.json"
  mkdir -p "$CFG_DIR"
  if [ -f "$CFG" ] && command -v python3 >/dev/null 2>&1; then
    python3 - "$CFG" "$WS_URL" <<'PY'
import json, sys
path, ws = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {{}}
cfg["ws"] = ws
json.dump(cfg, open(path, "w"), indent=2)
PY
  elif [ ! -f "$CFG" ]; then
    printf '{{\\n  "ws": "%s"\\n}}\\n' "$WS_URL" > "$CFG"
  else
    echo "note: set \\"ws\\": \\"$WS_URL\\" in $CFG by hand (python3 not found)"
  fi
  echo "control channel pinned to $WS_URL (WS-stripping edge)"
fi
echo "next: cheesehost auth login $ORIGIN/connector"
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


# Claude Code itself, served from here for the same reason the connector is: a
# machine may have no route to the vendor (private cloud subnets, a user's
# network we do not control, or the vendor's own regional availability, which
# their installer names as a failure mode). Fetched once, checksum-verified
# against the vendor's manifest, cached, then served locally — so the pinned
# version is something we HAND the machine rather than something we hope it can
# reach.
#
# The platform string is the vendor's (`linux-x64`, `linux-arm64-musl`, …), not
# our `<os>-<arch>` connector target: only the machine knows whether its libc is
# musl, and our target names cannot express that distinction.
@router.get("/claude/{version}/{platform}/claude")
async def download_claude(version: str, platform: str) -> Response:
    if not claude_dist.VERSION_RE.match(version):
        return PlainTextResponse("bad version", status_code=400)
    if not claude_dist.PLATFORM_RE.match(platform):
        return PlainTextResponse("unknown platform", status_code=400)
    try:
        binary = await claude_dist.ensure_cached(_dist_dir(), version, platform)
    except claude_dist.ClaudeDistError as exc:
        # 503, not 404: the build may well exist and be fetchable later. A 404
        # would tell an enrolling machine to give up on something transient.
        return PlainTextResponse(
            f"claude {version} unavailable: {exc}", status_code=503
        )
    return FileResponse(
        binary, media_type="application/octet-stream", filename="claude"
    )


# pi, served for the same reason and in the same shape. The artifact is a
# tarball rather than a bare binary because that is how the vendor ships it: the
# compiled agent plus the assets it reads beside itself. The machine unpacks it
# into a version-named directory, which is what makes the pin a fact about what
# we handed over rather than about what a registry resolved.
@router.get("/pi/{version}/{platform}/pi.tar.gz")
async def download_pi(version: str, platform: str) -> Response:
    if not pi_dist.VERSION_RE.match(version):
        return PlainTextResponse("bad version", status_code=400)
    if not pi_dist.PLATFORM_RE.match(platform):
        return PlainTextResponse("unknown platform", status_code=400)
    try:
        archive = await pi_dist.ensure_cached(_dist_dir(), version, platform)
    except pi_dist.PiDistError as exc:
        # 503 for the reason the claude route answers 503: the build may well
        # exist and be fetchable later, and a 404 tells a machine to give up.
        return PlainTextResponse(f"pi {version} unavailable: {exc}", status_code=503)
    return FileResponse(
        archive, media_type="application/gzip", filename=pi_dist.archive_name(platform)
    )


# The document toolchain, served for the reason the two above are and pinned in
# a way they are not: neither typst nor pandoc publishes a checksum, so the
# digest lives in `agent/toolchain` and a release re-cut under the same tag fails
# to verify rather than being handed to a machine.
#
# No version in the path. claude and pi are version-addressed because a machine
# can be running under a pin older than the deployment's; these are placed by
# the launcher on every start, so the only version anyone can ask for is the one
# this deployment pins — and putting it in the URL would only invite a machine
# to ask for another.
@router.get("/toolchain/{tool}/{platform}/artifact")
async def download_toolchain(tool: str, platform: str) -> Response:
    resolved = toolchain.resolve(tool, platform)
    if resolved is None:
        return PlainTextResponse("unknown tool or platform", status_code=404)
    platform_key, artifact = resolved
    try:
        path = await toolchain_dist.ensure_cached(_dist_dir(), tool, platform_key)
    except toolchain_dist.ToolchainDistError as exc:
        # 503 for the reason the two routes above answer 503: upstream may well
        # serve it later, and a 404 tells a machine to give up on something
        # transient.
        return PlainTextResponse(f"{tool} unavailable: {exc}", status_code=503)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"{tool}{artifact.suffix}",
    )


@router.get("/latest/{target}/cheesehost")
async def download_binary(target: str) -> Response:
    if not _TARGET_RE.match(target) or target not in _TARGETS:
        return PlainTextResponse("unknown target", status_code=404)
    binary = connector_build.binary_path(target)
    if binary is None:
        return PlainTextResponse(
            "binary not built for this target — run backend/scripts/build-connector.sh",
            status_code=404,
        )
    return FileResponse(
        binary,
        media_type="application/octet-stream",
        filename="cheesehost",
    )
