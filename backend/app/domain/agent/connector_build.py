"""Which connector build this server hands out — the one number that lets it
tell a machine it is running an older one.

``PROTOCOL_VERSION`` cannot answer that, and is not meant to: it moves only for
a BREAKING change, so every capability added compatibly arrives without touching
it. A connector missing one of those is indistinguishable on the wire from a
current one, and the frame it does not recognise is dropped by a ``switch`` with
no default — dropped without an answer, so the server learns of it only when its
own timeout fires, in a place that has nothing to do with the version.

So the identity is the bytes. A connector reports the sha256 of its own
executable; we compare it with the sha256 of what we serve for its platform.
Installing an update is a rename of exactly those bytes onto the executable, so
a machine on our build hashes to our number and anything else is a machine to
update. A connector too old to report anything is, by construction, older than
the build that started reporting.
"""

import hashlib
import re
from pathlib import Path

# The `<os>-<arch>` names the server publishes artifacts under. Go-style
# (amd64/arm64), never uname-style (x86_64/aarch64) — the connector's own
# `update.PlatformDir` and install.sh both build this exact string.
TARGETS = frozenset({"darwin-arm64", "darwin-amd64", "linux-arm64", "linux-amd64"})
TARGET_RE = re.compile(r"^(darwin|linux)-(amd64|arm64)$")

# Cached per target on (size, mtime_ns) rather than for the life of the process:
# this answer decides whether every connected machine is told to reinstall
# itself, so it must describe the file that is there now.
_digests: dict[str, tuple[tuple[int, int], str]] = {}


def dist_dir() -> Path:
    """backend/connector-dist — where the built `cheesehost` binaries live."""
    return Path(__file__).resolve().parents[3] / "connector-dist"


def binary_path(target: str) -> Path | None:
    """The connector we serve for ``target``, or None if we serve none."""
    if target not in TARGETS:
        return None
    binary = dist_dir() / target / "cheesehost"
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
