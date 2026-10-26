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
#: All caches together stay under this; the ones checked least recently go first.
CACHE_LIMIT_BYTES = 2 * 1024**3


def _root() -> Path:
    return Path(settings.workspace_root) / ".merge-checks"


def _cache(url: str) -> Path:
    return _root() / hashlib.sha256(url.encode()).hexdigest()[:32]


def _answer_file(cache: Path, base: str, head: str) -> Path:
    return cache / "answers" / f"{base}..{head}"


def answer(url: str, base: str, head: str) -> Answer | None:
    """The test merge's answer for this pair, or None while there is none."""
    try:
        text = _answer_file(_cache(url), base, head).read_text().strip()
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
    """A bare repository that fetches from ``url`` without file contents and
    fetches a missing blob from it on demand."""
    if (cache / "HEAD").exists():
        return True
    cache.mkdir(parents=True, exist_ok=True)
    steps = [
        ["init", "-q", "--bare"],
        ["config", "core.repositoryformatversion", "1"],
        ["config", "extensions.partialClone", "origin"],
        ["config", "remote.origin.url", url],
        ["config", "remote.origin.promisor", "true"],
        ["config", "remote.origin.partialclonefilter", "blob:none"],
    ]
    for step in steps:
        if _git(cache, step, env, FETCH_TIMEOUT_S) != 0:
            shutil.rmtree(cache, ignore_errors=True)
            return False
    return True


def decide(url: str, token: str, base: str, head: str) -> Answer | None:
    """Run the test merge of ``head`` into ``base`` now and keep its answer.

    None when there is no answer: another check holds this repository's cache
    right now (it will write one), or the fetch or the merge failed or ran out
    of time (the next reading starts another)."""
    cache = _cache(url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with _locked(cache) as held:
        if not held:
            return None
        found = answer(url, base, head)
        if found is not None:
            return found
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
        target = _answer_file(cache, base, head)
        target.parent.mkdir(exist_ok=True)
        partial = target.with_suffix(".partial")
        partial.write_text(result)
        partial.replace(target)
        os.utime(cache)
    _evict(keep=cache)
    return result


def _size(path: Path) -> int:
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(folder, name)).st_size
    return total


def _evict(*, keep: Path) -> None:
    """Remove the caches checked least recently until all of them fit in
    `CACHE_LIMIT_BYTES`; a cache a check holds right now stays."""
    caches = [p for p in _root().iterdir() if p.is_dir()]
    sizes = {p: _size(p) for p in caches}
    total = sum(sizes.values())
    for cache in sorted(caches, key=lambda p: p.stat().st_mtime):
        if total <= CACHE_LIMIT_BYTES:
            return
        if cache == keep:
            continue
        with _locked(cache) as held:
            if not held:
                continue
            shutil.rmtree(cache, ignore_errors=True)
            total -= sizes[cache]
