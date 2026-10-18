"""Planning a culprit search over the commits a red batch covers.

Pure: it takes the ordered commits and a `probe` that reruns the failing tests
on one commit, and says which commit first made them fail. It never guesses:
a probe that cannot judge ends the search, because treating "unknown" as green
would point at an innocent commit.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["pass", "fail", "unknown"]

#: Fewer commits than this are cheaper to read than to bisect.
MIN_COMMITS = 5
#: Probes one search may spend.
MAX_PROBES = 5


@dataclass(frozen=True, slots=True)
class BisectResult:
    #: The first commit on which the tests fail; None when not located.
    culprit: str | None
    #: The commits probed, in order.
    probed: tuple[str, ...]
    #: Why nothing was located (empty when ``culprit`` is set).
    reason: str = ""


async def bisect(
    commits: list[str], probe: Callable[[str], Awaitable[Verdict]]
) -> BisectResult:
    """``commits`` is oldest first and its newest commit is known to fail.

    The commit before the oldest one is assumed to pass (it is the last green
    batch's end), so the search finds the first failing commit in the list."""
    if len(commits) < MIN_COMMITS:
        return BisectResult(None, (), "too few commits to bisect")
    lo, hi = 0, len(commits) - 1  # the first failing commit is in [lo, hi]
    probed: list[str] = []
    while lo < hi:
        if len(probed) >= MAX_PROBES:
            return BisectResult(None, tuple(probed), "probe budget spent")
        mid = (lo + hi) // 2
        sha = commits[mid]
        probed.append(sha)
        verdict = await probe(sha)
        if verdict == "fail":
            hi = mid
        elif verdict == "pass":
            lo = mid + 1
        else:
            return BisectResult(None, tuple(probed), "a probe could not judge")
    return BisectResult(commits[lo], tuple(probed))
