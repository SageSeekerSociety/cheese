"""Which connector build this server hands out — the one number that lets it
tell a machine it is running an older one.

``PROTOCOL_VERSION`` cannot answer that, and is not meant to: it moves only for
a BREAKING change, so every capability added compatibly arrives without touching
it. A connector missing one of those is indistinguishable on the wire from a
current one, and the frame it does not recognise is dropped by a ``switch`` with
no default — dropped without an answer, so the server learns of it only when its
own timeout fires, in a place that has nothing to do with the version.

So the identity is the bytes. A connector reports the sha256 of its own
executable; we compare it with the sha256 of what the origin publishes for its
platform (`published_digest`) — the file its self-update downloads. Installing
an update is a rename of exactly those bytes onto the executable, so a machine
on the published build hashes to that number and anything else is a machine to
update. A connector too old to report anything is, by construction, older than
the build that started reporting.
"""

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# The `<os>-<arch>` names the server publishes artifacts under. Go-style
# (amd64/arm64), never uname-style (x86_64/aarch64) — the connector's own
# `update.PlatformDir` and install.sh both build this exact string.
TARGETS = frozenset(
    {
        "darwin-arm64",
        "darwin-amd64",
        "linux-arm64",
        "linux-amd64",
        "windows-arm64",
        "windows-amd64",
    }
)
TARGET_RE = re.compile(r"^(darwin|linux|windows)-(amd64|arm64)$")

# Cached per target on (size, mtime_ns) rather than for the life of the process:
# this answer decides whether every connected machine is told to reinstall
# itself, so it must describe the file that is there now.
_digests: dict[str, tuple[tuple[int, int], str]] = {}


def dist_dir() -> Path:
    """backend/connector-dist — where the built `cheesehost` binaries live."""
    return Path(__file__).resolve().parents[3] / "connector-dist"


def binary_name(target: str) -> str:
    """The file name of the connector for ``target``: Windows runs only .exe."""
    return "cheesehost.exe" if target.startswith("windows-") else "cheesehost"


def binary_path(target: str) -> Path | None:
    """The connector we serve for ``target``, or None if we serve none."""
    if target not in TARGETS:
        return None
    binary = dist_dir() / target / binary_name(target)
    return binary if binary.is_file() else None


def served_digest(target: str) -> str | None:
    """Hex sha256 of the connector we serve for ``target``; None if we have none.

    Blocking (tens of megabytes off disk) — call it off the event loop.
    """
    binary = binary_path(target)
    if binary is None:
        _digests.pop(target, None)
        return None
    stat = binary.stat()
    key = (stat.st_size, stat.st_mtime_ns)
    cached = _digests.get(target)
    if cached is not None and cached[0] == key:
        return cached[1]
    digest = hashlib.sha256()
    with binary.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    _digests[target] = (key, hexdigest)
    return hexdigest


def has_any_build() -> bool:
    """Whether we serve a connector for any platform at all.

    A checkout that never ran the build has nothing to hand anybody, and telling
    a machine to fetch it would only cost it a failed download per reconnect.
    """
    return any(binary_path(target) is not None for target in TARGETS)


# How long the origin's answer stands before it is asked again. Every machine
# says hello on every reconnect, and a release changes the answer a few times
# a day at most.
PUBLISHED_TTL_S = 60.0
# The origin is asked over this transport; None is the network. Tests hand in
# the app itself.
origin_transport: httpx.AsyncBaseTransport | None = None
_published: dict[str, tuple[float, str]] = {}


async def published_digest(target: str) -> str | None:
    """Hex sha256 of the connector the origin publishes for ``target``; None
    when it publishes none or cannot be asked.

    The connection owner is released on its own schedule, so the files in its
    own image can be days behind the ones the origin hands out, and a machine
    updating itself takes the origin's. Compared with the owner's copy, a
    machine on the published build read as stale and was told to update on
    every reconnect, while one still on the owner's older build read as current
    and never was."""
    if target not in TARGETS:
        return None
    if not settings.connector_origin_url:
        return await asyncio.to_thread(served_digest, target)
    now = time.monotonic()
    cached = _published.get(target)
    if cached is not None and cached[0] > now:
        return cached[1]
    base = settings.connector_origin_url.rstrip("/")
    url = f"{base}/connector/latest/{target}/{binary_name(target)}"
    try:
        # An address inside the deployment: never through an outbound proxy
        # the environment may name.
        async with httpx.AsyncClient(
            transport=origin_transport, timeout=10, trust_env=False
        ) as client:
            response = await client.head(url)
    except httpx.HTTPError as exc:
        logger.warning("cannot ask %s which connector is published: %s", url, exc)
        return None
    digest = response.headers.get("X-Checksum-SHA256")
    if response.status_code != 200 or not digest:
        return None
    _published[target] = (now + PUBLISHED_TTL_S, digest)
    return digest
