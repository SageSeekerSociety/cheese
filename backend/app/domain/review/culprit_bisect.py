"""Planning a culprit search over the commits a red batch covers.

Pure: it takes the ordered commits and a `probe` that reruns the failing tests
on one commit, and says which commit first made them fail. It never guesses:
a probe that cannot judge ends the search, because treating "unknown" as green
would point at an innocent commit.

**The same plan, put down between probes.** A probe is a CI run, and a sweep
cannot wait for one — so `Search` is the plan as a value: `probe` names the
commit it is waiting on, `advance` takes that commit's verdict, and every field
is plain data a row can hold. `bisect` is that plan driven in a loop, for a
caller that can afford to wait for each answer.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
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


@dataclass(frozen=True, slots=True)
class Search:
    """One search as data: what is decided, and which verdict it waits on.

    ``commits`` is oldest first and its newest commit is known to fail.
    ``base`` is the commit before the oldest one — the last green batch's end —
    and is probed before the oldest commit is named: a probe environment that
    cannot run the tests fails everywhere, and only a passing ``base`` tells
    the two apart.
    """

    commits: tuple[str, ...]
    base: str | None = None
    #: The first failing commit is in ``[lo, hi]``.
    lo: int = 0
    hi: int = 0
    #: The commits probed so far, in order.
    probed: tuple[str, ...] = ()
    #: The commit whose verdict the search is waiting on; "" when none is out.
    pending: str = ""
    #: The pending probe is ``base``, not one of ``commits``.
    at_base: bool = False
    #: Why the search stopped without a culprit ("" while it has not).
    stopped: str = ""

    @property
    def probe(self) -> str:
        """The commit to probe now, or "" when nothing more can be probed."""
        if self.stopped or self.pending:
            return ""
        if len(self.commits) < MIN_COMMITS:
            return ""
        if self.lo < self.hi:
            if len(self.probed) >= MAX_PROBES:
                return ""
            return self.commits[(self.lo + self.hi) // 2]
        # Landed on one commit. The oldest one is named only after the commit
        # before it passed; without a base there is nothing to tell it apart
        # from every probe failing.
        if self.lo == 0:
            if self.base is None or len(self.probed) >= MAX_PROBES:
                return ""
            return self.base
        return ""

    @property
    def culprit(self) -> str | None:
        """The commit that first made the tests fail, once it is known."""
        if self.stopped or self.pending or self.lo != self.hi:
            return None
        if self.lo == 0 and (self.base is None or self.base not in self.probed):
            return None
        return self.commits[self.lo]

    @property
    def finished(self) -> bool:
        return self.stopped != "" or (not self.pending and self.probe == "")

    @property
    def reason(self) -> str:
        """Why the search has no culprit, once it is finished without one."""
        if self.stopped:
            return self.stopped
        if self.culprit is not None or not self.finished:
            return ""
        if len(self.commits) < MIN_COMMITS:
            return "too few commits to bisect"
        if self.lo < self.hi:
            return "probe budget spent"
        return "no passing base to check against"

    def waiting_for(self, sha: str) -> "Search":
        """Hand ``sha`` out to be run: the search now waits on its verdict."""
        return replace(self, pending=sha, at_base=sha == self.base)

    def advance(self, verdict: Verdict) -> "Search":
        """Take the verdict for the commit ``pending`` names.

        A verdict that cannot judge ends the search: it is not a pass, and
        reading it as one would point at the next commit over.
        """
        if not self.pending:
            raise ValueError(  # i18n-exempt: a caller's mistake, not a refusal
                "this search is waiting on no probe"
            )
        probed = (*self.probed, self.pending)
        if verdict == "unknown":
            return replace(
                self, probed=probed, pending="", stopped="a probe could not judge"
            )
        if self.at_base:
            if verdict == "pass":
                return replace(self, probed=probed, pending="", at_base=False)
            return replace(
                self,
                probed=probed,
                pending="",
                at_base=False,
                stopped="the base does not pass",
            )
        mid = (self.lo + self.hi) // 2
        if verdict == "fail":
            return replace(self, probed=probed, pending="", hi=mid)
        return replace(self, probed=probed, pending="", lo=mid + 1)


def begin(commits: list[str], base: str | None = None) -> Search:
    """A search over ``commits`` (oldest first, the newest known to fail)."""
    return Search(
        commits=tuple(commits),
        base=base,
        lo=0,
        hi=len(commits) - 1,
        stopped="" if len(commits) >= MIN_COMMITS else "too few commits to bisect",
    )


async def bisect(
    commits: list[str],
    probe: Callable[[str], Awaitable[Verdict]],
    base: str | None = None,
) -> BisectResult:
    """``commits`` is oldest first and its newest commit is known to fail.

    ``base`` is the commit before the oldest one: the last green batch's end.
    The search finds the first failing commit in the list. When it lands on the
    oldest commit, every probe failed, which is also what a probe environment
    that cannot run the tests looks like; only a passing ``base`` tells the
    two apart, so without one that answer is withheld."""
    search = begin(commits, base)
    while sha := search.probe:
        search = search.waiting_for(sha).advance(await probe(sha))
    return BisectResult(search.culprit, search.probed, search.reason)
