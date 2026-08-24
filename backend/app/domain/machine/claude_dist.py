"""Serve Claude Code binaries from the platform, not from the vendor's CDN.

A machine that hosts sessions needs `claude` on it, at a version the launcher
will accept. Telling each machine to fetch it from `claude.ai` puts three
assumptions in the way of every enrollment:

  * that the machine can reach that host at all — cloud nodes sit on private
    subnets, and a self-hosted machine belongs to a user whose network we do
    not control;
  * that the vendor serves it *there* — the installer's own error text says
    "not available in your region", so this is a documented failure, not a
    hypothetical one;
  * that whatever version it happens to get satisfies our floor.

The platform already answers all three for the connector binary, which every
machine downloads from us (`/connector/latest/<target>/cheesehost`). This does
the same for `claude`: we fetch once, verify against the vendor's published
SHA-256, cache it, and serve it to machines that may have no route to the
vendor at all. Pinning stops being a wish about what a machine downloaded and
becomes a fact about what we handed it.

Deliberately a cache and not a vendored artifact: the binaries are ~40MB each
across six platform variants, and baking them into the image would tie every
version bump to a rebuild. The first request for a version pays the fetch; the
rest are local.
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path

import httpx

# The vendor's release layout, as read from their install.sh:
#   <base>/latest                      → a bare version string
#   <base>/<version>/manifest.json     → platforms.<p>.checksum (sha256)
#   <base>/<version>/<platform>/claude → the binary
UPSTREAM_BASE = "https://downloads.claude.ai/claude-code-releases"

# Platform strings are the vendor's, NOT our `<os>-<arch>` connector targets.
# Keeping their vocabulary preserves the musl distinction (Alpine and friends
# need a different build), which our own target names cannot express — and the
# machine, which knows whether its libc is musl, is the only place that can
# decide. So this is a proxy, not a translator.
PLATFORM_RE = re.compile(r"^(darwin-(x64|arm64)|linux-(x64|arm64)(-musl)?)$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$")

_FETCH_TIMEOUT_S = 180.0
_MANIFEST_TIMEOUT_S = 30.0

# One in-flight fetch per (version, platform). Enrolling a fleet asks for the
# same binary at the same moment, and without this each request would download
# its own 40MB copy and race the others onto the same path.
_locks: dict[tuple[str, str], asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


class ClaudeDistError(RuntimeError):
    """Upstream could not supply this binary. Distinct from 'bad request' so the
    route can answer 503 (try later) rather than 404 (never existed)."""


def cache_dir(root: Path) -> Path:
    return root / "claude-cache"


def cached_path(root: Path, version: str, platform: str) -> Path:
    return cache_dir(root) / version / platform / "claude"


async def _lock_for(version: str, platform: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault((version, platform), asyncio.Lock())


async def _checksum(client: httpx.AsyncClient, version: str, platform: str) -> str:
    """The vendor's published SHA-256 for this build.

    Fetched every time rather than cached alongside the binary: it is the only
    thing that makes the cached file trustworthy, and a checksum stored next to
    the artifact it vouches for proves nothing.
    """
    resp = await client.get(
        f"{UPSTREAM_BASE}/{version}/manifest.json", timeout=_MANIFEST_TIMEOUT_S
    )
    if resp.status_code != 200:
        raise ClaudeDistError(
            f"manifest for {version} unavailable upstream (HTTP {resp.status_code})"
        )
    try:
        platforms = json.loads(resp.text).get("platforms", {})
    except json.JSONDecodeError as exc:  # an HTML error page, a region block
        raise ClaudeDistError(f"manifest for {version} was not JSON: {exc}") from exc
    entry = platforms.get(platform) or {}
    digest = entry.get("checksum", "")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ClaudeDistError(f"{version} has no usable checksum for {platform}")
    return digest


async def ensure_cached(root: Path, version: str, platform: str) -> Path:
    """Return a verified local copy of the binary, fetching it once if needed."""
    target = cached_path(root, version, platform)
    if target.is_file() and target.stat().st_size > 0:
        return target

    lock = await _lock_for(version, platform)
    async with lock:
        if target.is_file() and target.stat().st_size > 0:
            return target  # won by another request while we waited

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        digest = hashlib.sha256()
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                want = await _checksum(client, version, platform)
                url = f"{UPSTREAM_BASE}/{version}/{platform}/claude"
                async with client.stream("GET", url, timeout=_FETCH_TIMEOUT_S) as resp:
                    if resp.status_code != 200:
                        raise ClaudeDistError(
                            f"{version}/{platform} unavailable upstream "
                            f"(HTTP {resp.status_code})"
                        )
                    with tmp.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(1 << 20):
                            digest.update(chunk)
                            fh.write(chunk)
            got = digest.hexdigest()
            if got != want:
                raise ClaudeDistError(
                    f"{version}/{platform} failed checksum "
                    f"(want {want[:12]}…, got {got[:12]}…)"
                )
            tmp.chmod(0o755)
            # Rename last: a reader either sees no file or a verified one, never
            # a half-written binary that would be handed to a machine as valid.
            tmp.replace(target)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise ClaudeDistError(f"fetching {version}/{platform}: {exc}") from exc
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    return target
