"""Serve the room's document toolchain from the platform.

``claude_dist`` states the argument in full: a cloud node sits on a private
subnet, a self-hosted machine belongs to a user whose network we do not
control, and a version a machine fetched for itself is a hope rather than a
fact. Every word of it holds for typst, pandoc, uv and the fonts beside them.

This module is only the serving half. WHICH artifacts exist, where they come
from and what they must hash to is `agent/toolchain`, for the reason the claude
pin lives in `harness/claude_code/device_launch` rather than here: the layer
that runs a room decides what a room needs.

Deliberately a cache and not a vendored artifact, exactly as claude_dist argues:
these are ~110MB per platform and baking them into the image would tie every
version bump to a rebuild. The first request for one pays the fetch; the rest
are local.
"""

import asyncio
import hashlib
from pathlib import Path

import httpx

from app.domain.agent.toolchain import ARTIFACTS

_FETCH_TIMEOUT_S = 600.0

# One in-flight fetch per artifact. A fleet preparing at once asks for the same
# file at the same moment, and without this each request would download its own
# copy and race the others onto the same path.
_locks: dict[tuple[str, str], asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


class ToolchainDistError(RuntimeError):
    """Upstream could not supply this artifact. Distinct from 'bad request' so
    the route can answer 503 (try later) rather than 404 (never existed)."""


def cache_dir(root: Path) -> Path:
    return root / "toolchain-cache"


def cached_path(root: Path, tool: str, platform_key: str) -> Path:
    artifact = ARTIFACTS[(tool, platform_key)]
    return cache_dir(root) / tool / platform_key / f"artifact{artifact.suffix}"


async def _lock_for(tool: str, platform_key: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault((tool, platform_key), asyncio.Lock())


async def ensure_cached(root: Path, tool: str, platform_key: str) -> Path:
    """Return a verified local copy of the artifact, fetching it once if needed."""
    artifact = ARTIFACTS[(tool, platform_key)]
    target = cached_path(root, tool, platform_key)
    if target.is_file() and target.stat().st_size == artifact.size:
        return target

    lock = await _lock_for(tool, platform_key)
    async with lock:
        if target.is_file() and target.stat().st_size == artifact.size:
            return target  # won by another request while we waited

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        digest = hashlib.sha256()
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                async with client.stream(
                    "GET", artifact.url, timeout=_FETCH_TIMEOUT_S
                ) as resp:
                    if resp.status_code != 200:
                        raise ToolchainDistError(
                            f"{tool}/{platform_key} unavailable upstream "
                            f"(HTTP {resp.status_code})"
                        )
                    with tmp.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(1 << 20):
                            digest.update(chunk)
                            fh.write(chunk)
            got = digest.hexdigest()
            if got != artifact.sha256:
                raise ToolchainDistError(
                    f"{tool}/{platform_key} failed checksum "
                    f"(want {artifact.sha256[:12]}…, got {got[:12]}…)"
                )
            # Rename last: a reader either sees no file or a verified one, never
            # a half-written archive that would be handed to a machine as valid.
            tmp.replace(target)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise ToolchainDistError(f"fetching {tool}/{platform_key}: {exc}") from exc
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    return target
