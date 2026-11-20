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
import stat
import subprocess
import time
import uuid
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
#: The git caches together stay under this, checked before every fetch and
#: after every check whatever its outcome: the caches checked least recently
#: go first, and a repository whose cache alone is larger is checked and then
#: not kept. Answers are not part of it (`ANSWERS_KEPT_FOR`).
CACHE_LIMIT_BYTES = 2 * 1024**3

#: How long an answer is kept. A reader needs it only from the check that
#: writes it until the PR's next push or merge, normally minutes to days; two
#: weeks covers a PR left alone over a holiday, and keeps the answers to one
#: per pair a forge refused in that time, a few thousand files at most. An
#: answer pruned early only costs another test merge.
ANSWERS_KEPT_FOR_S = 14 * 24 * 3600

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


def _answers() -> Path:
    """Answers live in a store of their own, apart from the caches: a cache
    that is evicted, or one too large to keep, takes no answer with it, and
    cache eviction never touches an answer (`_prune_answers` does)."""
    return _root() / "answers"


def _answer_file(url: str, base: str, head: str) -> Path:
    """One flat folder, the repository in the file's name: there is no folder
    per repository, so pruning never has one to remove from under a check
    about to write into it."""
    return _answers() / f"{_key(url)}.{base}..{head}"


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
            _prune_answers()


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
    # The answer is the exit code; the tree it prints is not needed. No
    # `--quiet`: Git 2.47, the one the backend image ships, rejects it (exit
    # 129), and `--write-tree` alone is there since 2.38.
    merged = _git(
        cache, ["merge-tree", "--write-tree", base, head], env, MERGE_TIMEOUT_S
    )
    if merged not in (0, 1):
        return None
    result: Answer = "clean" if merged == 0 else "conflict"
    target = _answer_file(url, base, head)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Its own name, so two writers never share one, and published by rename.
    partial = target.with_name(f"{target.name}.{uuid.uuid4().hex}.partial")
    partial.write_text(result)
    partial.replace(target)
    return result


def _prune_answers() -> None:
    """Remove answers older than `ANSWERS_KEPT_FOR_S`. Only files: the folder
    that holds them stays, so a check about to write an answer always finds
    it. A `.partial` file is an answer being written, which takes
    milliseconds; it is removed only past the same age, when no write can
    still be under way, so a crash cannot leave one behind for good."""
    oldest = time.time() - ANSWERS_KEPT_FOR_S
    with contextlib.suppress(OSError):
        for entry in list(_answers().iterdir()):
            with contextlib.suppress(OSError):
                if entry.lstat().st_mtime < oldest:
                    entry.unlink()


def _allocated(path: Path) -> int:
    """The bytes the filesystem gives ``path`` (a file, or a directory with all
    it holds), counted in blocks. What vanishes meanwhile counts as nothing."""
    try:
        own = os.lstat(path)
    except OSError:
        return 0
    total = own.st_blocks * 512
    if stat.S_ISDIR(own.st_mode):
        for folder, dirs, files in os.walk(path):
            for name in (*dirs, *files):
                with contextlib.suppress(OSError):
                    total += os.lstat(os.path.join(folder, name)).st_blocks * 512
    return total


def _evict(*, holding: Path) -> None:
    """Bring the git caches back under `CACHE_LIMIT_BYTES`.

    Counted: every cache, complete or left half-built, by the blocks it takes.
    Removed in this order: half-built caches; then caches, least recently
    checked first; the cache of the check that calls this (``holding``, whose
    lock it holds) last, even when it alone is over the limit: that repository
    was checked in a cache that is not kept, which the log says. A cache
    another check holds right now is skipped, never waited for; that check
    evicts when it ends. Other checks add and remove caches while this runs,
    so an entry that vanished is simply passed over. Answers are not touched.
    """
    root = _root()
    own = {holding, holding.with_name(holding.name + _BUILDING)}
    caches: list[tuple[int, float, Path, int]] = []
    with contextlib.suppress(OSError):
        for entry in root.iterdir():
            if entry == _answers():
                continue
            with contextlib.suppress(OSError):
                info = entry.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    continue
                rank = 2 if entry in own else 0 if entry.name.endswith(_BUILDING) else 1
                caches.append((rank, info.st_mtime, entry, _allocated(entry)))
    total = sum(size for *_, size in caches)
    for _, _, entry, size in sorted(caches, key=lambda c: (c[0], c[1])):
        if total <= CACHE_LIMIT_BYTES:
            return
        if entry in own:
            shutil.rmtree(entry, ignore_errors=True)
            total -= size
            if entry == holding:
                logger.warning(
                    "merge check: cache of %s takes %d bytes, over the %d-byte "
                    "limit; checked without keeping it",
                    entry.name,
                    size,
                    CACHE_LIMIT_BYTES,
                )
            continue
        with _locked(root / entry.name.removesuffix(_BUILDING)) as held:
            if not held:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            total -= size
