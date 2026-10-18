"""The culprit search: which commit first made the named tests fail.

Rules, as stated before the code:

- Eight commits, the fifth the first bad one: three probes, midpoints.
- A probe that cannot judge ends the search; unknown is never green.
- Landing on the oldest commit needs a passing base: a probe environment that
  cannot run the tests fails everywhere and must not name the oldest commit.
- Fewer than five commits are not worth a search: no probe at all.
"""

import asyncio

from app.domain.review.culprit_bisect import bisect

COMMITS = [f"c{i}" for i in range(8)]


def _run(commits, verdicts, base=None):
    seen: list[str] = []

    async def probe(sha):
        seen.append(sha)
        return verdicts(sha)

    return asyncio.run(bisect(commits, probe, base)), seen


def test_the_first_bad_commit_is_found_by_midpoints():
    result, seen = _run(COMMITS, lambda sha: "fail" if int(sha[1:]) >= 4 else "pass")
    assert result.culprit == "c4"
    assert seen == ["c3", "c5", "c4"]


def test_an_unknown_probe_locates_nothing():
    result, seen = _run(COMMITS, lambda sha: "unknown")
    assert result.culprit is None
    assert seen == ["c3"]


def test_unknown_after_progress_still_locates_nothing():
    result, _ = _run(COMMITS, lambda sha: "pass" if sha == "c3" else "unknown")
    assert result.culprit is None


def test_four_commits_are_not_searched():
    result, seen = _run(COMMITS[:4], lambda sha: "fail")
    assert result.culprit is None
    assert seen == []


def test_the_budget_is_five_probes():
    commits = [f"c{i}" for i in range(200)]
    result, seen = _run(commits, lambda sha: "pass")
    assert result.culprit is None
    assert len(seen) == 5


def test_every_probe_failing_names_nobody_without_a_base():
    result, _ = _run(COMMITS, lambda sha: "fail")
    assert result.culprit is None


def test_every_probe_failing_names_nobody_when_the_base_fails_too():
    result, seen = _run(COMMITS, lambda sha: "fail", base="b0")
    assert result.culprit is None
    assert seen[-1] == "b0"


def test_the_oldest_commit_is_named_when_the_base_passes():
    result, _ = _run(COMMITS, lambda sha: "pass" if sha == "b0" else "fail", base="b0")
    assert result.culprit == "c0"
