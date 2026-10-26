"""Whether a PR's head merges into its base, asked of git itself.

Forgejo's `mergeable` is false while its own merge check is still running as
well as on a conflict (`forgejo_pr.ForgejoClient.status`), and nothing in its
API tells the two apart. So when it says false, the platform runs the test
merge a forge runs: `git merge-tree --write-tree <base> <head>`, which exits 0
when the two merge and 1 when they conflict.

The objects come from one bare cache per repository under the workspace root,
fetched without file contents (`--filter=blob:none`; merge-tree fetches the few
blobs it needs from the forge as it goes). Measured against Forgejo 15.0.9 with
a 5,135-commit repository: the first fetch took 1.9 s and left a 21 MB cache,
a fetch after one more commit 0.25 s, merge-tree 0.06–0.22 s.

The check never runs inside a request or a database transaction: a reader asks
`answer`, which only reads a file, and `start` runs a check in the background
when there is none yet. An answer is kept per (base, head) pair; commits do not
change, so it never goes stale. Until it is on disk, and whenever the check
fails, there is no answer, and nobody may read that as a conflict.
"""

import asyncio
import contextlib
import fcntl
import hashlib
import logging
import os
import shutil
import signal
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from app.core.background import spawn
from app.core.config import settings

logger = logging.getLogger("cheesex.review.merge_check")

Answer = Literal["clean", "conflict"]

#: A fetch or merge that runs longer than this is killed and leaves no answer.
FETCH_TIMEOUT_S = 120
MERGE_TIMEOUT_S = 60
#: Everything under `_root()` stays under this, checked before every fetch and
#: after every check whatever its outcome: the caches checked least recently
#: go first, and a repository whose cache alone is larger is checked and then
#: not kept.
CACHE_LIMIT_BYTES = 2 * 1024**3

#: Where a cache is put together before it is renamed into place, so a cache
#: on disk is either complete or absent (one left behind by a process that
#: died is removed by the next check of that repository, or by eviction).
_BUILDING = ".building"


def _root() -> Path:
    return Path(settings.workspace_root) / ".merge-checks"


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def _cache(url: str) -> Path:
    return _root() / _key(url)


def _answer_file(url: str, base: str, head: str) -> Path:
    """Answers live beside the caches, not in them: a cache that is evicted, or
    one too large to keep, takes no answer with it."""
    return _root() / "answers" / _key(url) / f"{base}..{head}"


def answer(url: str, base: str, head: str) -> Answer | None:
    """The test merge's answer for this pair, or None while there is none."""
    try:
        text = _answer_file(url, base, head).read_text().strip()
    except OSError:
        return None
    return "clean" if text == "clean" else "conflict" if text == "conflict" else None


def start(url: str, token: str, base: str, head: str) -> None:
    """Find the answer in the background (see `decide`)."""
    spawn(asyncio.to_thread(decide, url, token, base, head), name="merge check")


@contextlib.contextmanager
def _locked(cache: Path) -> Iterator[bool]:
    """Hold this cache for one check; False when another process or thread
    holds it. The lock file sits beside the cache, so eviction can remove the
    cache while the lock file stays."""
    with open(cache.with_suffix(".lock"), "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _env(token: str) -> dict[str, str]:
    """git with no configuration of the machine's, never prompting, and the
    credential sent as a header (never in the address or a config file)."""
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: token {token}",
    }


def _git(
    cache: Path, args: list[str], env: dict[str, str], timeout: float
) -> int | None:
    """git's exit code, or None when it ran past ``timeout`` and was killed
    together with the git processes it started (merge-tree starts a fetch for
    each missing blob)."""
    with subprocess.Popen(
        ["git", "-C", str(cache), *args],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ) as proc:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            logger.warning("merge check: git %s ran past %ss", args[0], timeout)
            return None
    conflict = args[0] == "merge-tree" and proc.returncode == 1
    if proc.returncode != 0 and not conflict:
        logger.warning("merge check: git %s exited %s", args[0], proc.returncode)
    return proc.returncode


def _prepare(cache: Path, url: str, env: dict[str, str]) -> bool:
    """A bare repository at ``cache`` that fetches from ``url`` without file
    contents and fetches a missing blob from it on demand.

    Built under another name and renamed into place only once complete, so a
    process that dies halfway leaves no cache that looks ready."""
    if cache.is_dir():
        return True
    building = cache.with_name(cache.name + _BUILDING)
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    steps = [
        ["init", "-q", "--bare"],
        ["config", "core.repositoryformatversion", "1"],
        ["config", "extensions.partialClone", "origin"],
        ["config", "remote.origin.url", url],
        ["config", "remote.origin.promisor", "true"],
        ["config", "remote.origin.partialclonefilter", "blob:none"],
    ]
    for step in steps:
        if _git(building, step, env, FETCH_TIMEOUT_S) != 0:
            shutil.rmtree(building, ignore_errors=True)
            return False
    building.rename(cache)
    return True


def decide(url: str, token: str, base: str, head: str) -> Answer | None:
    """Run the test merge of ``head`` into ``base`` now and keep its answer.

    None when there is no answer: another check holds this repository's cache
    right now (it will write one), or the fetch or the merge failed or ran out
    of time (the next reading starts another)."""
    cache = _cache(url)
    _root().mkdir(parents=True, exist_ok=True)
    with _locked(cache) as held:
        if not held:
            return None
        found = answer(url, base, head)
        if found is not None:
            return found
        _evict(holding=cache)
        try:
            return _test_merge(cache, url, token, base, head)
        finally:
            if cache.is_dir():
                os.utime(cache)
            _evict(holding=cache)


def _test_merge(
    cache: Path, url: str, token: str, base: str, head: str
) -> Answer | None:
    env = _env(token)
    if not _prepare(cache, url, env):
        return None
    fetched = _git(
        cache,
        ["fetch", "-q", "--filter=blob:none", "origin", base, head],
        env,
        FETCH_TIMEOUT_S,
    )
    if fetched != 0:
        return None
    merged = _git(
        cache,
        ["merge-tree", "--write-tree", "--quiet", base, head],
        env,
        MERGE_TIMEOUT_S,
    )
    if merged not in (0, 1):
        return None
    result: Answer = "clean" if merged == 0 else "conflict"
    target = _answer_file(url, base, head)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    partial.write_text(result)
    partial.replace(target)
    return result


def _size(path: Path) -> int:
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(folder, name)).st_size
    return total


def _evict(*, holding: Path) -> None:
    """Bring everything under `_root()` back under `CACHE_LIMIT_BYTES`.

    Counted: every cache, complete or left half-built, and the answers. Removed
    first: half-built caches, then the caches checked least recently. The
    cache of the check that calls this (``holding``, whose lock it holds) goes
    last, and goes too when it alone is over the limit: that repository was
    checked in a cache that is not kept, which the log says. A cache another
    check holds right now is left to that check, which evicts when it ends."""
    root = _root()
    entries = [p for p in root.iterdir() if p.is_dir() and p.name != "answers"]
    sizes = {p: _size(p) for p in entries}
    total = sum(sizes.values()) + _size(root / "answers")
    if total <= CACHE_LIMIT_BYTES:
        return

    def order(path: Path) -> tuple[int, float]:
        own = path in (holding, holding.with_name(holding.name + _BUILDING))
        return (
            2 if own else 0 if path.name.endswith(_BUILDING) else 1,
            path.stat().st_mtime,
        )

    for entry in sorted(entries, key=order):
        if total <= CACHE_LIMIT_BYTES:
            return
        mine = entry in (holding, holding.with_name(holding.name + _BUILDING))
        if mine:
            shutil.rmtree(entry, ignore_errors=True)
            total -= sizes[entry]
            if entry == holding:
                logger.warning(
                    "merge check: cache of %s is %d bytes, over the %d-byte "
                    "limit; checked without keeping it",
                    entry.name,
                    sizes[entry],
                    CACHE_LIMIT_BYTES,
                )
            continue
        name = entry.name.removesuffix(_BUILDING)
        with _locked(root / name) as held:
            if not held:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            total -= sizes[entry]
