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

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.core.config import settings
from app.domain.agent import connector_build, toolchain
from app.domain.agent.harness.claude_code import CLAUDE_PINNED_VERSION
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
# A download through a relay can be cut mid-transfer, and plain --retry does
# not retry a cut one (curl 56). Each attempt resumes the partial file (-C -);
# --retry-all-errors would do this in one call but curl before 7.71 rejects it.
partial="$dest/cheesehost.part"
rm -f "$partial"
attempt=1
# The desktop app shows how far the download has got, from curl's bar.
progress=-s
if [ -n "${{CHEESE_PROGRESS:-}}" ]; then
  progress=-#
fi
binary="$ORIGIN/connector/latest/$target/cheesehost"
until curl -fSL "$progress" -C - -o "$partial" "$binary"; do
  if [ "$attempt" -ge 5 ]; then
    rm -f "$partial"
    echo "download failed after $attempt attempts"
    exit 1
  fi
  attempt=$((attempt + 1))
  echo "download interrupted; resuming (attempt $attempt)…"
  sleep 2
done
mv "$partial" "$dest/cheesehost"
chmod +x "$dest/cheesehost"
echo "installed to $dest/cheesehost"
# Where cheesehost reads its config: Go's os.UserConfigDir, which on macOS
# ignores XDG.
case "$os" in
  darwin) CFG_DIR="$HOME/Library/Application Support/cheese" ;;
  *) CFG_DIR="${{XDG_CONFIG_HOME:-$HOME/.config}}/cheese" ;;
esac
CFG="$CFG_DIR/config.json"
mkdir -p "$CFG_DIR"
# The server this came from, so a bare `cheesehost link connect` knows where to
# go. A config that already names one keeps it: its token belongs there.
# WS-stripping edge (e.g. a campus front proxy that only forwards HTTP): the
# server baked a WS-capable control-channel URL above into "ws" — the login
# loads-then-saves, so it survives login. Login/approve/API/downloads all stay
# on ORIGIN.
if [ -f "$CFG" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$CFG" "$ORIGIN/connector" "$WS_URL" <<'PY'
import json, sys
path, base, ws = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {{}}
if not cfg.get("base"):
    cfg["base"] = base
if ws:
    cfg["ws"] = ws
json.dump(cfg, open(path, "w"), indent=2)
PY
elif [ ! -f "$CFG" ]; then
  if [ -n "$WS_URL" ]; then
    printf '{{\n  "base": "%s",\n  "ws": "%s"\n}}\n' \
      "$ORIGIN/connector" "$WS_URL" > "$CFG"
  else
    printf '{{\n  "base": "%s"\n}}\n' "$ORIGIN/connector" > "$CFG"
  fi
elif [ -n "$WS_URL" ]; then
  echo "note: set \"ws\": \"$WS_URL\" in $CFG by hand (python3 not found)"
fi
if [ -n "$WS_URL" ]; then
  echo "control channel pinned to $WS_URL (WS-stripping edge)"
fi
# A fresh macOS, or a Linux account that has not logged in again since
# ~/.local/bin was made, does not have it on PATH yet: name the binary by its
# full path, so the next command works as printed.
case ":$PATH:" in *":$dest:"*) bin=cheesehost ;; *) bin="$dest/cheesehost" ;; esac
echo "next: $bin link connect $ORIGIN/connector   (logs in, then stays connected)"
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


_INSTALL_PS1 = r"""# cheesehost installer for Windows, the counterpart of install.sh.
# Run it in PowerShell as the user whose machine this is:
#   irm <origin>/connector/install.ps1 | iex
# No administrator rights and no secrets; enrollment is `cheesehost link connect`.
$ErrorActionPreference = 'Stop'
$origin = '__ORIGIN__'
$wsUrl = if ($env:CHEESE_WS_URL) { $env:CHEESE_WS_URL } else { '__WS_URL__' }
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
# A directory this user can write, so the connector can replace itself there.
$dir = Join-Path $env:LOCALAPPDATA 'cheese\bin'
$exe = Join-Path $dir 'cheesehost.exe'
New-Item -ItemType Directory -Force $dir | Out-Null
Write-Host "downloading cheesehost (windows-$arch)..."
$partial = "$exe.part"
Remove-Item -Force -ErrorAction SilentlyContinue $partial
$url = "$origin/connector/latest/windows-$arch/cheesehost.exe"
# Resume a download cut mid-transfer, as install.sh does.
for ($attempt = 1; ; $attempt++) {
  & curl.exe -fsSL -C - -o $partial $url
  if ($LASTEXITCODE -eq 0) { break }
  if ($attempt -ge 5) {
    Remove-Item -Force -ErrorAction SilentlyContinue $partial
    throw "download failed ($LASTEXITCODE)"
  }
  Write-Host "download interrupted; resuming (attempt $($attempt + 1))..."
  Start-Sleep -Seconds 2
}
try {
  Move-Item -Force $partial $exe
} catch {
  # Windows will not replace a running exe; a connector already running keeps
  # itself current, so there is nothing to do.
  Remove-Item -Force $partial
  Write-Host "cheesehost is already installed and running; it updates itself."
}
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $dir)) {
  $newPath = if ($userPath) { "$userPath;$dir" } else { $dir }
  [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
  $env:Path += ";$dir"
}
Write-Host "installed to $exe"
# WS-stripping edge: the same pinned control-channel URL install.sh writes,
# into the file cheesehost reads (os.UserConfigDir is %APPDATA%).
if ($wsUrl) {
  $cfgDir = Join-Path $env:APPDATA 'cheese'
  $cfg = Join-Path $cfgDir 'config.json'
  New-Item -ItemType Directory -Force $cfgDir | Out-Null
  $config = [pscustomobject]@{}
  if (Test-Path $cfg) { $config = Get-Content -Raw $cfg | ConvertFrom-Json }
  $config | Add-Member -Force -NotePropertyName ws -NotePropertyValue $wsUrl
  [IO.File]::WriteAllText($cfg, ($config | ConvertTo-Json))
  Write-Host "control channel pinned to $wsUrl (WS-stripping edge)"
}
Write-Host "next: cheesehost link connect $origin/connector"
Write-Host "      (logs in, then stays connected)"
"""


@router.get("/install.ps1")
async def install_powershell(request: Request) -> PlainTextResponse:
    origin = _origin(request)
    script = _INSTALL_PS1.replace("__ORIGIN__", origin).replace(
        "__WS_URL__", _ws_control_url(origin)
    )
    return PlainTextResponse(script, media_type="text/plain")


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
# Which Claude Code the platform runs, for a machine about to fetch it: the
# connector's `cheesehost claude login` downloads this build before its owner
# logs it in, so the login and every session after it use the one the runner's
# protocol is pinned to.
@router.get("/claude/pin")
async def claude_pin() -> dict[str, str]:
    return {"version": CLAUDE_PINNED_VERSION}


@router.get("/claude/{version}/{platform}/{name}")
async def download_claude(version: str, platform: str, name: str) -> Response:
    if not claude_dist.VERSION_RE.match(version):
        return PlainTextResponse("bad version", status_code=400)
    if not claude_dist.PLATFORM_RE.match(platform):
        return PlainTextResponse("unknown platform", status_code=400)
    if name != claude_dist.binary_name(platform):
        return PlainTextResponse("unknown file", status_code=404)
    try:
        binary = await claude_dist.ensure_cached(_dist_dir(), version, platform)
    except claude_dist.ClaudeDistError as exc:
        # 503, not 404: the build may well exist and be fetchable later. A 404
        # would tell an enrolling machine to give up on something transient.
        return PlainTextResponse(
            f"claude {version} unavailable: {exc}", status_code=503
        )
    return FileResponse(binary, media_type="application/octet-stream", filename=name)


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
        headers={"X-Checksum-SHA256": artifact.sha256},
    )


def _accepts_gzip(request: Request) -> bool:
    for part in request.headers.get("accept-encoding", "").split(","):
        coding, _, params = part.strip().partition(";")
        if coding.strip().lower() == "gzip":
            return params.replace(" ", "").lower() not in ("q=0", "q=0.0", "q=0.00")
    return False


@router.api_route("/latest/{target}/{name}", methods=["GET", "HEAD"])
async def download_binary(target: str, name: str, request: Request) -> Response:
    """The connector for ``target``. ``X-Checksum-SHA256`` names its bytes, so
    the connection owner can ask which build is published with a HEAD.

    A GET that accepts gzip gets the gzip copy (`connector_build.gzipped`); the
    checksum still names the decoded bytes, which is what the machine runs."""
    if not _TARGET_RE.match(target) or target not in _TARGETS:
        return PlainTextResponse("unknown target", status_code=404)
    if name != connector_build.binary_name(target):
        return PlainTextResponse("unknown file", status_code=404)
    binary = connector_build.binary_path(target)
    if binary is None:
        return PlainTextResponse(
            "binary not built for this target — run backend/scripts/build-connector.sh",
            status_code=404,
        )
    digest = await asyncio.to_thread(connector_build.served_digest, target)
    headers = {"Vary": "Accept-Encoding"}
    if digest:
        headers["X-Checksum-SHA256"] = digest
    if request.method == "GET" and _accepts_gzip(request):
        compressed = await asyncio.to_thread(connector_build.gzipped, target)
        if compressed is not None:
            return FileResponse(
                compressed,
                media_type="application/octet-stream",
                filename="cheesehost",
                headers={**headers, "Content-Encoding": "gzip"},
            )
    return FileResponse(
        binary,
        media_type="application/octet-stream",
        filename="cheesehost",
        headers=headers,
    )
