"""Running a culprit search across sweeps: what it asks for, and when it stops.

The rules, stated before the code:

- A batch with fewer commits than ``MIN_COMMITS`` is not searched at all.
- A probe that cannot judge stops the search and names nobody.
- The first commit on which the tests fail is the culprit, whichever commit
  of the batch that is.
- A search spends at most ``MAX_PROBES`` probes.
- A probe still running is not a verdict: the search waits for it.
- A forge that cannot run a probe leaves the batch to the attribution that
  existed before the search.
"""

import uuid

from sqlalchemy import select

from app.domain.project.models import Project
from app.domain.review import culprit_bisect, culprit_search
from app.domain.review.culprit_models import CulpritSearch
from tests.integration.conftest import a_team

#: Oldest first, eight of them: the midpoint of the whole batch is index 3.
COMMITS = [f"{i:04d}" + "a" * 36 for i in range(8)]


class _Reads:
    """A forge that runs probes: each dispatch gets a run, each run a verdict."""

    def __init__(self, verdict):
        self._verdict = verdict
        self.dispatched: list[str] = []
        self._runs: dict[str, str] = {}

    async def dispatch_bisect(self, sha, tests, nonce):
        self.dispatched.append(sha)
        run = f"run-{len(self.dispatched)}"
        self._runs[run] = sha
        return run

    async def bisect_verdict(self, run_id):
        return self._verdict(self._runs[run_id])


class _SlowReads(_Reads):
    """Every run reports ``running`` once before it reports its verdict."""

    def __init__(self, verdict):
        super().__init__(verdict)
        self._asked: set[str] = set()

    async def bisect_verdict(self, run_id):
        if run_id not in self._asked:
            self._asked.add(run_id)
            return "running"
        return await super().bisect_verdict(run_id)


class _NoProbes(_Reads):
    """A repository that cannot run a probe (no workflow, no permission)."""

    async def dispatch_bisect(self, sha, tests, nonce):
        return None


async def _project(db_factory) -> uuid.UUID:
    async with db_factory() as session:
        project = Project(team_id=await a_team(session), name="Culprit search")
        session.add(project)
        await session.commit()
        return project.id


async def _run(db_factory, reads, commits, project_id, *, head="head", base=None):
    """Advance the search until it stops, as the sweeps would."""
    for _ in range(40):
        finding = await culprit_search.advance(
            db_factory,
            reads,
            project_id=project_id,
            head=head,
            base=base,
            commits=list(commits),
            tests=["tests/unit/test_x.py"],
        )
        if finding.state != "searching":
            return finding
    raise AssertionError("the search never stopped")


async def test_a_batch_too_short_to_bisect_is_not_searched(db_factory):
    project_id = await _project(db_factory)
    reads = _Reads(lambda sha: "fail")
    finding = await _run(db_factory, reads, COMMITS[:4], project_id)
    assert finding.state == "unavailable"
    assert reads.dispatched == []


async def test_a_probe_that_cannot_judge_names_nobody(db_factory):
    project_id = await _project(db_factory)
    reads = _Reads(lambda sha: "unknown")
    finding = await _run(db_factory, reads, COMMITS, project_id)
    assert finding.state == "unlocated"
    assert finding.culprit is None
    assert reads.dispatched == [COMMITS[3]]


async def test_a_verdict_outside_the_protocol_is_not_read_as_a_pass(db_factory):
    """A forge that answers something else has not judged the probe.

    The protocol lists pass/fail/unknown/running. Anything else is normalized
    to unknown, so the search stops and names nobody — the planner advances a
    midpoint on any answer that is not ``fail``, and a stray value must not be
    allowed to walk past the commit that broke the batch.
    """
    project_id = await _project(db_factory)
    reads = _Reads(lambda sha: "green-ish")
    finding = await _run(db_factory, reads, COMMITS, project_id)
    assert finding.state == "unlocated"
    assert finding.culprit is None
    assert reads.dispatched == [COMMITS[3]]


async def test_the_commit_that_first_failed_is_named(db_factory):
    project_id = await _project(db_factory)
    first_bad = COMMITS.index(COMMITS[4])
    reads = _Reads(lambda sha: "fail" if COMMITS.index(sha) >= first_bad else "pass")
    finding = await _run(db_factory, reads, COMMITS, project_id)
    assert finding.state == "located"
    assert finding.culprit == COMMITS[4]


async def test_the_oldest_commit_is_named_only_after_the_base_passed(db_factory):
    project_id = await _project(db_factory)
    reads = _Reads(lambda sha: "pass" if sha == "green" else "fail")
    finding = await _run(db_factory, reads, COMMITS, project_id, base="green")
    assert finding.state == "located"
    assert finding.culprit == COMMITS[0]
    # The base answers once: asking it again would spend a run and let a flaky
    # second answer take back a culprit the first one already named.
    assert reads.dispatched.count("green") == 1


async def test_the_search_spends_at_most_five_probes(db_factory):
    project_id = await _project(db_factory)
    many = [f"{i:04d}" + "b" * 36 for i in range(200)]
    reads = _Reads(lambda sha: "pass")
    finding = await _run(db_factory, reads, many, project_id)
    assert finding.state == "unlocated"
    assert finding.culprit is None
    assert len(reads.dispatched) == culprit_bisect.MAX_PROBES


async def test_a_probe_still_running_is_waited_for_not_read_as_a_verdict(db_factory):
    project_id = await _project(db_factory)
    reads = _SlowReads(lambda sha: "fail" if COMMITS.index(sha) >= 4 else "pass")
    finding = await _run(db_factory, reads, COMMITS, project_id)
    assert finding.state == "located"
    assert finding.culprit == COMMITS[4]
    # Each commit is probed once: a run still going is read again, not
    # dispatched again, and never counted as a verdict.
    assert len(reads.dispatched) == len(set(reads.dispatched))


async def test_a_forge_that_cannot_probe_falls_back(db_factory):
    project_id = await _project(db_factory)
    reads = _NoProbes(lambda sha: "fail")
    finding = await _run(db_factory, reads, COMMITS, project_id)
    assert finding.state == "unavailable"
    # Nothing was learned, and no row is left behind for a search nobody ran.
    async with db_factory() as session:
        assert await session.scalar(select(CulpritSearch)) is None


class _RecordingReads(_Reads):
    """Remembers which tests each probe was dispatched with."""

    def __init__(self, verdict):
        super().__init__(verdict)
        self.test_sets: list[tuple[str, ...]] = []

    async def dispatch_bisect(self, sha, tests, nonce):
        self.test_sets.append(tuple(tests))
        return await super().dispatch_bisect(sha, tests, nonce)


async def test_probes_keep_the_tests_the_search_started_with(db_factory):
    """A re-run of the batch's gate can name a different set of failing tests;
    every probe has to ask the same question for the answers to compare."""
    project_id = await _project(db_factory)
    reads = _RecordingReads(lambda sha: "fail" if COMMITS.index(sha) >= 4 else "pass")
    finding = await culprit_search.advance(
        db_factory,
        reads,
        project_id=project_id,
        head="head",
        base=None,
        commits=COMMITS,
        tests=["tests/unit/test_old.py"],
    )
    while finding.state == "searching":
        finding = await culprit_search.advance(
            db_factory,
            reads,
            project_id=project_id,
            head="head",
            base=None,
            commits=COMMITS,
            tests=["tests/unit/test_new.py"],
        )
    assert finding.state == "located"
    assert set(reads.test_sets) == {("tests/unit/test_old.py",)}
