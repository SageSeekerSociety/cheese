"""Serve pi from the platform, for the reason the other harness already is.

``claude_dist`` states the argument in full: a cloud node sits on a private
subnet, a self-hosted machine belongs to a user whose network we do not
control, and a version fetched by the machine is a hope rather than a fact.
Every word of it holds for pi, which arrived installing itself with ``npm
install`` from the public registry — the machine reaching a third party we
have no relationship with, for twenty packages, on every enrollment that had
not done it yet.

The vendor publishes a self-contained build per platform on its GitHub
releases, with a ``SHA256SUMS`` covering them, so this is the same shape as
``claude_dist``: fetch once, verify against what the vendor published, cache,
and serve it to machines that may have no route to GitHub at all. It also
retires the node and npm requirement outright — the binary is compiled and
carries its own runtime.

What differs is the artifact. claude is one file; pi is a directory (the
binary, a wasm module, themes, an optional native clipboard module), shipped as
a tarball. So this caches the tarball and the machine unpacks it, which is why
nothing here chmods anything: the modes are in the archive.
"""

import asyncio
import hashlib
import re
from pathlib import Path

import httpx

# The vendor's release layout:
#   <base>/v<version>/SHA256SUMS         → "<sha256>  <asset>" per line
#   <base>/v<version>/pi-<platform>.tar.gz
UPSTREAM_BASE = "https://github.com/earendil-works/pi/releases/download"

# What the vendor builds AND we can run. Deliberately short of their full
# matrix: they publish Windows too, and no machine we host sessions on is one.
#
# No musl variant, and that is the vendor's fact rather than an omission here —
# their Linux builds link glibc, so an Alpine-style machine cannot run pi at
# all. The launcher says that in as many words rather than downloading a binary
# whose loader will refuse it.
PLATFORM_RE = re.compile(r"^(darwin|linux)-(x64|arm64)$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

_FETCH_TIMEOUT_S = 300.0
_SUMS_TIMEOUT_S = 30.0

# One in-flight fetch per (version, platform), for the reason claude_dist has
# one: a fleet enrolling at once asks for the same 40MB archive at the same
# moment, and without this each request downloads its own copy and races the
# others onto the same path.
_locks: dict[tuple[str, str], asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


class PiDistError(RuntimeError):
    """Upstream could not supply this build. Distinct from 'bad request' so the
    route can answer 503 (try later) rather than 404 (never existed)."""


def archive_name(platform: str) -> str:
    return f"pi-{platform}.tar.gz"


def cache_dir(root: Path) -> Path:
    return root / "pi-cache"


def cached_path(root: Path, version: str, platform: str) -> Path:
    return cache_dir(root) / version / platform / archive_name(platform)


async def _lock_for(version: str, platform: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault((version, platform), asyncio.Lock())


async def _checksum(client: httpx.AsyncClient, version: str, platform: str) -> str:
    """The vendor's published SHA-256 for this archive.

    Fetched every time rather than cached alongside the archive: it is the only
    thing that makes the cached file trustworthy, and a checksum stored next to
    the artifact it vouches for proves nothing.
    """
    resp = await client.get(
        f"{UPSTREAM_BASE}/v{version}/SHA256SUMS", timeout=_SUMS_TIMEOUT_S
    )
    if resp.status_code != 200:
        raise PiDistError(
            f"checksums for {version} unavailable upstream (HTTP {resp.status_code})"
        )
    want = archive_name(platform)
    for line in resp.text.splitlines():
        digest, _, name = line.strip().partition(" ")
        if name.strip() == want and re.fullmatch(r"[a-f0-9]{64}", digest):
            return digest
    raise PiDistError(f"{version} has no usable checksum for {platform}")


async def ensure_cached(root: Path, version: str, platform: str) -> Path:
    """Return a verified local copy of the archive, fetching it once if needed."""
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
                url = f"{UPSTREAM_BASE}/v{version}/{archive_name(platform)}"
                async with client.stream("GET", url, timeout=_FETCH_TIMEOUT_S) as resp:
                    if resp.status_code != 200:
                        raise PiDistError(
                            f"{version}/{platform} unavailable upstream "
                            f"(HTTP {resp.status_code})"
                        )
                    with tmp.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(1 << 20):
                            digest.update(chunk)
                            fh.write(chunk)
            got = digest.hexdigest()
            if got != want:
                raise PiDistError(
                    f"{version}/{platform} failed checksum "
                    f"(want {want[:12]}…, got {got[:12]}…)"
                )
            # Rename last: a reader either sees no file or a verified one, never
            # a half-written archive that would be handed to a machine as valid.
            tmp.replace(target)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise PiDistError(f"fetching {version}/{platform}: {exc}") from exc
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    return target
