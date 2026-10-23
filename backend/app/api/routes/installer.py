"""Self-hosted connector install flow (fusion-design §5.1).

Serves the `cheesehost` thin-client connector so a fresh user machine can join
with one line — `curl -fsSL <origin>/connector/install.sh | sh`. The script
detects the platform, downloads the matching binary, and prints the next step
(`cheesehost auth login`). It carries no secrets and no business logic;
enrollment still goes through the device flow (routes/connector/devices.py), so a piped
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
# sha256sum is coreutils (Linux), shasum is Perl's (macOS), openssl is on both:
# any one of them can check the download.
sha256_of() {{
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{{print $NF}}'
  else
    return 1
  fi
}}
echo "downloading cheesehost ($target)…"
# A download through a relay can be cut mid-transfer, and plain --retry does
# not retry a cut one (curl 56). Each attempt resumes the partial file (-C -);
# --retry-all-errors would do this in one call but curl before 7.71 rejects it.
partial="$dest/cheesehost.part"
headers="$dest/cheesehost.headers"
rm -f "$partial"
attempt=1
# The desktop app shows how far the download has got, from curl's bar.
progress=-s
if [ -n "${{CHEESE_PROGRESS:-}}" ]; then
  progress=-#
fi
binary="$ORIGIN/connector/latest/$target/cheesehost"
# -D keeps the response's headers: the response that carries the bytes also
# names their sha256, and that number is what the check below compares to.
# The packed copy is a third of the size, and is asked for only where gzip can
# unpack it: a machine without gzip would otherwise receive a stream it cannot
# use. The header carries an empty value there, which sends no header at all.
# Naming it by hand instead of using curl's --compressed is what keeps a
# resumed range meaningful: --compressed decodes as it goes, while these bytes
# stay a gzip stream on disk until the unpack below, which is also where the
# checksum gets the bytes the machine runs.
accept="Accept-Encoding:"
if command -v gzip >/dev/null 2>&1; then
  accept="Accept-Encoding: gzip"
fi
until curl -fSL "$progress" -C - -D "$headers" -H "$accept" -o "$partial" "$binary"; do
  if [ "$attempt" -ge 5 ]; then
    rm -f "$partial" "$headers"
    echo "download failed after $attempt attempts"
    exit 1
  fi
  attempt=$((attempt + 1))
  echo "download interrupted; resuming (attempt $attempt)…"
  sleep 2
done
# Whether the packed copy arrived is decided by the file itself, not by a
# header: a resumed transfer's own response may not repeat Content-Encoding,
# and what is measured here is the file assembled across attempts. `gzip -t`
# says so without another tool to find — it fails on anything that is not a
# whole gzip stream, which is exactly when unpacking must not be attempted.
if gzip -t "$partial" >/dev/null 2>&1; then
  if ! gzip -dc "$partial" > "$partial.plain"; then
    rm -f "$partial" "$partial.plain" "$headers"
    echo "cannot unpack cheesehost ($target); not installing it" >&2
    exit 1
  fi
  mv "$partial.plain" "$partial"
fi
# The server hashes the very bytes it serves, so this comparison says whether
# what we hold is what it published. A transfer a relay truncated, or anything
# that substituted one, must not reach PATH as an executable. A missing header
# is a failure too: a proxy that strips it would otherwise turn this off
# silently.
want=$(grep -i '^x-checksum-sha256:' "$headers" 2>/dev/null \
  | tail -n 1 | cut -d: -f2- | tr -cd '0-9a-fA-F' | tr 'A-F' 'a-f')
rm -f "$headers"
if [ -z "$want" ]; then
  rm -f "$partial"
  echo "no sha256 in the response for cheesehost ($target); not installing it" >&2
  exit 1
fi
if ! got=$(sha256_of "$partial"); then
  rm -f "$partial"
  echo "no sha256sum, shasum or openssl to check the download; not installing it" >&2
  exit 1
fi
if [ "$got" != "$want" ]; then
  rm -f "$partial"
  echo "checksum mismatch for cheesehost ($target): expected $want, got $got" >&2
  exit 1
fi
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
$headers = "$exe.headers"
Remove-Item -Force -ErrorAction SilentlyContinue $partial
$url = "$origin/connector/latest/windows-$arch/cheesehost.exe"
# Resume a download cut mid-transfer, as install.sh does. Accept-Encoding names
# the packed copy by hand instead of curl's --compressed, which decodes as it
# goes: the file on disk stays a gzip stream, so a resumed range still lands on
# the same file, and the unpack below hands the checksum the bytes the machine
# runs.
for ($attempt = 1; ; $attempt++) {
  Remove-Item -Force -ErrorAction SilentlyContinue $headers
  & curl.exe -fsSL -C - -D $headers -H "Accept-Encoding: gzip" -o $partial $url
  if ($LASTEXITCODE -eq 0) { break }
  if ($attempt -ge 5) {
    Remove-Item -Force -ErrorAction SilentlyContinue $partial
    throw "download failed ($LASTEXITCODE)"
  }
  Write-Host "download interrupted; resuming (attempt $($attempt + 1))..."
  Start-Sleep -Seconds 2
}
# The gzip magic says which copy arrived, not a header: a resumed transfer's own
# response may not repeat Content-Encoding, and the file assembled across
# attempts is what gets unpacked.
$magic = New-Object byte[] 2
$head = [System.IO.File]::OpenRead($partial)
try { $read = $head.Read($magic, 0, 2) } finally { $head.Dispose() }
if ($read -eq 2 -and $magic[0] -eq 0x1f -and $magic[1] -eq 0x8b) {
  try { Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop } catch {}
  $plain = "$partial.plain"
  try {
    $source = [System.IO.File]::OpenRead($partial)
    try {
      $target = [System.IO.File]::Create($plain)
      try {
        $packed = [System.IO.Compression.GZipStream]::new($source, [System.IO.Compression.CompressionMode]::Decompress)
        try { $packed.CopyTo($target) } finally { $packed.Dispose() }
      } finally { $target.Dispose() }
    } finally { $source.Dispose() }
  } catch {
    Remove-Item -Force -ErrorAction SilentlyContinue $partial, $plain
    throw "cannot unpack cheesehost (windows-$arch): $($_.Exception.Message)"
  }
  Move-Item -Force $plain $partial
}
# The same response that carried the bytes named their sha256; check the file
# against it before it replaces anything, as install.sh does.
$want = (Get-Content $headers -ErrorAction SilentlyContinue |
  Where-Object { $_ -match '^X-Checksum-SHA256:' } | Select-Object -Last 1)
if ($want) { $want = ($want -replace '^[^:]*:\s*', '').Trim().ToLower() }
Remove-Item -Force -ErrorAction SilentlyContinue $headers
if (-not $want) {
  Remove-Item -Force -ErrorAction SilentlyContinue $partial
  throw "no sha256 in the response for cheesehost (windows-$arch); not installing it"
}
$got = (Get-FileHash -Algorithm SHA256 -Path $partial).Hash.ToLower()
if ($got -ne $want) {
  Remove-Item -Force -ErrorAction SilentlyContinue $partial
  throw "checksum mismatch for cheesehost (windows-$arch): expected $want, got $got"
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
async def download_claude(
    version: str, platform: str, name: str, request: Request
) -> Response:
    """The pinned Claude Code build. ``X-Checksum-SHA256`` names the binary a
    machine ends up running; a client that accepts gzip gets the packed copy
    and unpacks it itself. Either copy answers a Range request, so a machine
    whose download was cut off asks only for the rest of it."""
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
    headers = {
        "Vary": "Accept-Encoding",
        "X-Checksum-SHA256": await asyncio.to_thread(claude_dist.served_digest, binary),
    }
    if _accepts_gzip(request):
        return FileResponse(
            await asyncio.to_thread(claude_dist.packed, binary),
            media_type="application/octet-stream",
            filename=name,
            headers={**headers, "Content-Encoding": "gzip"},
        )
    return FileResponse(
        binary, media_type="application/octet-stream", filename=name, headers=headers
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
        headers={"X-Checksum-SHA256": artifact.sha256},
    )


def _accepts_gzip(request: Request) -> bool:
    for part in request.headers.get("accept-encoding", "").split(","):
        coding, _, params = part.strip().partition(";")
        if coding.strip().lower() == "gzip":
            return params.replace(" ", "").lower() not in ("q=0", "q=0.0", "q=0.00")
    return False


@router.api_route(
    "/latest/{target}/{name}",
    methods=["GET", "HEAD"],
    # Named here, not derived: FastAPI builds the default id from the first of the
    # route's methods and those are a set, so the document would otherwise change
    # from run to run — and the frontend generates its types from that document,
    # while backend/openapi-untyped.json is compared as text.
    operation_id="download_connector_binary",
)
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
