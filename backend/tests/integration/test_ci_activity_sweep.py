"""The CI activity sweep: what it stores, what it reads again, and when it stops.

Rules pinned here, each one a person could state without the code:

* A sweep stores each run, job, ran step and queue event once, however often
  it sees them; a run is linked to the PR it tested.
* A re-run's jobs are read again, and the failed first attempt stays: it is the
  evidence that a job failed and then passed.
* One sweep reads at most its budget of jobs, newest runs first; the rest are
  counted as owed.
* The backfill walks back to the window's start and stops there; rows older
  than the window are deleted.
* After a day without sweeps, the next one lists the whole gap and stores it,
  however many runs that is.
* A run GitHub no longer has does not stop the sweeps after it, and a run that
  used no runner costs no request.
* The queue is read from the window's start the first time, then from where
  the last sweep stopped, with a little overlap.
* While the installation's quota is down to the share kept for people, the
  sweep makes no GitHub call and says why.
* A quota refusal mid-sweep keeps what was already stored and says why.

GitHub is a fake `CiSource`; the credential lookup is real down to the App's
token, which is a fake with a quota the test sets.
"""

from datetime import UTC, datetime, timedelta

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import forge_quota
from app.domain.ci_activity import ingest
from app.domain.ci_activity.github import (
    CiGitHubError,
    CiQuotaRefused,
    Listed,
    QueueEvent,
)
from app.domain.ci_activity.models import (
    CiActivitySweep,
    CiJob,
    CiJobStep,
    CiMergedPullRequest,
    CiMergeQueueEvent,
    CiWorkflowRun,
)
from app.domain.ci_activity.store import CiActivityStore
from app.domain.project import forge as project_forge
from app.domain.project.models import Project, ProjectGitInstallation
from tests.integration.conftest import a_team

REPO = "owner/ci-activity-test"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(run_id: int, *, age: timedelta, **extra) -> dict:
    created = NOW - age
    return {
        "id": run_id,
        "workflow_id": 7,
        "name": "Required CI",
        "path": ".github/workflows/required-ci.yml",
        "event": "pull_request",
        "head_branch": f"feat/{run_id}",
        "head_sha": f"{run_id:040d}",
        "pull_requests": [{"number": run_id // 10}],
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "created_at": _iso(created),
        "run_started_at": _iso(created),
        "updated_at": _iso(created + timedelta(minutes=9)),
        "html_url": f"https://github.invalid/runs/{run_id}",
        **extra,
    }


def _job(job_id: int, run: dict, *, attempt: int = 1, **extra) -> dict:
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    return {
        "id": job_id,
        "run_id": run["id"],
        "run_attempt": attempt,
        "name": "backend / pure (1)",
        "status": "completed",
        "conclusion": "success",
        "created_at": _iso(created),
        "started_at": _iso(created + timedelta(seconds=40)),
        "completed_at": _iso(created + timedelta(minutes=6)),
        "runner_name": "GitHub Actions 1000",
        "runner_group_name": "GitHub Actions",
        "labels": ["ubuntu-latest"],
        "steps": [
            {
                "number": 1,
                "name": "Set up job",
                "conclusion": "success",
                "started_at": _iso(created + timedelta(seconds=41)),
                "completed_at": _iso(created + timedelta(seconds=43)),
            },
            {
                "number": 2,
                "name": "Upload coverage",
                "conclusion": "skipped",
                "started_at": None,
                "completed_at": None,
            },
        ],
        **extra,
    }


class _GitHub:
    """Stands in for `GitHubCi`: serves what the test put in it, and counts."""

    def __init__(self) -> None:
        self.runs: dict[int, dict] = {}
        self.jobs: dict[int, list[dict]] = {}
        self.events: list[QueueEvent] = []
        self.listings: list[tuple[datetime, datetime]] = []
        self.jobs_read: list[int] = []
        self.refuse_jobs_after: int | None = None
        self.gone: set[int] = set()
        self.queue_reads: list[tuple[datetime, datetime]] = []
        self.pulls: list[dict] = []

    async def list_runs(self, *, owner, repo, token, since, until) -> Listed:
        assert (owner, repo, token) == ("owner", "ci-activity-test", "app-token")
        self.listings.append((since, until))
        found = [
            r
            for r in self.runs.values()
            if since
            <= datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            < until
        ]
        return Listed(items=found, requests=1)

    async def run_jobs(self, *, owner, repo, token, run_id) -> Listed:
        if (
            self.refuse_jobs_after is not None
            and len(self.jobs_read) >= self.refuse_jobs_after
        ):
            raise CiQuotaRefused("GitHub quota spent (HTTP 403)")
        self.jobs_read.append(run_id)
        if run_id in self.gone:
            raise CiGitHubError("GitHub HTTP 404: Not Found", 404)
        return Listed(items=self.jobs.get(run_id, []), requests=1)

    async def queue_events(self, *, owner, repo, token, since, until):
        self.queue_reads.append((since, until))
        return list(self.events), Listed(items=list(self.pulls), requests=1)


class _Tokens:
    installation_id = 9917

    def __init__(self, remaining: int = 4000, limit: int = 5000) -> None:
        self.quota = (remaining, limit)

    async def installation_token(self) -> tuple[str, str]:
        return "app-token", "2026-10-17T13:00:00+00:00"

    async def core_quota(self) -> tuple[int, int]:
        return self.quota


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> _Tokens:
    fake = _Tokens()

    async def _for_project(project_id, session):
        return fake

    monkeypatch.setattr(project_forge, "github_app_tokens_for_project", _for_project)
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    monkeypatch.setattr(ingest, "WINDOW", timedelta(days=2))
    return fake


async def _connect(session: AsyncSession) -> None:
    project = Project(team_id=await a_team(session), name="P", owner_handle="ci-owner")
    session.add(project)
    await session.flush()
    session.add(
        ProjectGitInstallation(
            project_id=project.id, installation_id=9917, repo=REPO, account="owner"
        )
    )
    await session.commit()


async def _count(session: AsyncSession, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _sweep(session, github, *, at=NOW, jobs_budget=50, backfill_slices=20):
    return await ingest.sweep_once(
        session,
        source=github,
        repo=REPO,
        now=at,
        jobs_budget=jobs_budget,
        backfill_slices=backfill_slices,
    )


def test_each_run_job_step_and_queue_event_is_stored_once(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    pr_run = _run(41, age=timedelta(hours=1))
    queue_run = _run(
        52,
        age=timedelta(minutes=30),
        event="merge_group",
        head_branch="gh-readonly-queue/main/pr-4186-" + "ab" * 20,
        pull_requests=[],
    )
    github.runs = {41: pr_run, 52: queue_run}
    github.jobs = {
        41: [_job(410, pr_run)],
        52: [
            _job(
                520,
                queue_run,
                runner_name="cheese-ci-runner-1",
                runner_group_name="Default",
                labels=["self-hosted", "cheese-ci"],
            )
        ],
    }
    github.events = [
        QueueEvent(4186, "added", NOW - timedelta(minutes=40), None, "cheesex-app"),
        QueueEvent(
            4186, "removed", NOW - timedelta(minutes=5), "merged", "github-merge-queue"
        ),
    ]

    async def _run_it() -> None:
        await _connect(db_session)
        first = await _sweep(db_session, github)
        assert first["error"] == ""
        second = await _sweep(db_session, github, at=NOW + timedelta(minutes=5))
        assert second["error"] == ""

        assert await _count(db_session, CiWorkflowRun) == 2
        assert await _count(db_session, CiJob) == 2
        # The skipped step has no time and is not kept.
        assert await _count(db_session, CiJobStep) == 2
        assert await _count(db_session, CiMergeQueueEvent) == 2
        assert second["jobs_read"] == 0, "a run whose jobs are stored is not read again"

        runs = {r.run_id: r for r in await db_session.scalars(select(CiWorkflowRun))}
        assert runs[41].pr_number == 4
        assert runs[52].pr_number == 4186
        kinds = {
            j.job_id: j.runner_kind for j in await db_session.scalars(select(CiJob))
        }
        assert kinds == {410: "hosted", 520: "self_hosted"}

    _portal.call(_run_it)


def test_direct_merges_are_stored_without_fabricating_queue_events(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    github.pulls = [
        {
            "number": 71,
            "headRefName": "feat/direct",
            "createdAt": _iso(NOW - timedelta(days=1)),
            "mergedAt": _iso(NOW - timedelta(minutes=5)),
        },
        {
            "number": 72,
            "headRefName": "feat/open",
            "createdAt": _iso(NOW - timedelta(days=1)),
            "mergedAt": None,
        },
    ]

    async def _run_it() -> None:
        await _connect(db_session)
        for _ in range(2):
            report = await _sweep(db_session, github)
            assert report["error"] == ""
        rows = list(await db_session.scalars(select(CiMergedPullRequest)))
        assert [(r.pr_number, r.head_branch, r.merged_at) for r in rows] == [
            (71, "feat/direct", NOW - timedelta(minutes=5))
        ]
        assert await _count(db_session, CiMergeQueueEvent) == 0
        await CiActivityStore(db_session).prune(REPO, NOW)
        assert await _count(db_session, CiMergedPullRequest) == 0

    _portal.call(_run_it)


def test_a_rerun_is_read_again_and_its_failed_first_attempt_stays(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    run = _run(61, age=timedelta(hours=1), conclusion="failure")
    github.runs = {61: run}
    github.jobs = {61: [_job(610, run, conclusion="failure")]}

    async def _run_it() -> None:
        await _connect(db_session)
        await _sweep(db_session, github)

        github.runs[61] = {**run, "run_attempt": 2, "conclusion": "success"}
        github.jobs[61] = [
            _job(610, run, conclusion="failure"),
            _job(611, run, attempt=2),
        ]
        report = await _sweep(db_session, github, at=NOW + timedelta(minutes=5))

        assert report["jobs_read"] == 2
        jobs = {
            (j.run_attempt, j.conclusion)
            for j in await db_session.scalars(select(CiJob))
        }
        assert jobs == {(1, "failure"), (2, "success")}
        stored = await db_session.scalar(select(CiWorkflowRun))
        assert (stored.run_attempt, stored.jobs_attempt) == (2, 2)

    _portal.call(_run_it)


def test_one_sweep_reads_at_most_its_budget_of_jobs_newest_first(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    for n in range(5):
        run = _run(70 + n, age=timedelta(minutes=10 * (5 - n)))
        github.runs[run["id"]] = run
        github.jobs[run["id"]] = [_job(700 + n, run)]

    async def _run_it() -> None:
        await _connect(db_session)
        report = await _sweep(db_session, github, jobs_budget=2)

        assert github.jobs_read == [74, 73]
        assert report["jobs_owed"] == 3

    _portal.call(_run_it)


def test_the_backfill_stops_at_the_window_start_and_older_rows_are_deleted(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    inside = _run(81, age=timedelta(days=1, hours=20))
    github.runs = {81: inside}
    github.jobs = {81: [_job(810, inside)]}
    github.events = [
        QueueEvent(11, "added", NOW - timedelta(days=3), None, "x"),
        QueueEvent(12, "added", NOW - timedelta(hours=2), None, "x"),
    ]
    window_start = NOW - timedelta(days=2)

    async def _run_it() -> None:
        await _connect(db_session)
        db_session.add(
            CiWorkflowRun(
                **{
                    "repo": REPO,
                    "run_id": 80,
                    "workflow_id": 7,
                    "workflow_name": "Required CI",
                    "workflow_path": ".github/workflows/required-ci.yml",
                    "event": "push",
                    "head_sha": "0" * 40,
                    "run_attempt": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": NOW - timedelta(days=3),
                    "jobs_attempt": 1,
                    "ingested_at": NOW - timedelta(days=3),
                }
            )
        )
        await db_session.commit()

        for minutes in range(0, 60, 5):
            await _sweep(
                db_session,
                github,
                at=NOW + timedelta(minutes=minutes),
                backfill_slices=3,
            )

        earliest = min(since for since, _ in github.listings)
        assert earliest >= window_start, "nothing older than the window is listed"
        last = await db_session.scalar(
            select(CiActivitySweep).order_by(CiActivitySweep.started_at.desc())
        )
        assert last.runs_listed_from <= window_start + timedelta(minutes=55)
        stored = set(await db_session.scalars(select(CiWorkflowRun.run_id)))
        assert stored == {81}, "the run older than the window is deleted"
        prs = set(await db_session.scalars(select(CiMergeQueueEvent.pr_number)))
        assert prs == {12}, "the queue event older than the window is deleted"

    _portal.call(_run_it)


def test_no_github_call_while_the_quota_is_down_to_the_share_kept_for_people(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    github.runs = {91: _run(91, age=timedelta(hours=1))}
    tokens.quota = (500, 5000)

    async def _run_it() -> None:
        await _connect(db_session)
        report = await _sweep(db_session, github)

        assert report["error"]
        assert github.listings == [] and github.jobs_read == []
        assert await _count(db_session, CiWorkflowRun) == 0
        recorded = await db_session.scalar(select(CiActivitySweep))
        assert recorded is not None and recorded.report["error"] == report["error"]

    _portal.call(_run_it)


def test_a_quota_refusal_mid_sweep_keeps_what_was_stored(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    for n in range(3):
        run = _run(100 + n, age=timedelta(minutes=10 * (3 - n)))
        github.runs[run["id"]] = run
        github.jobs[run["id"]] = [_job(1000 + n, run)]
    github.refuse_jobs_after = 1

    async def _run_it() -> None:
        await _connect(db_session)
        report = await _sweep(db_session, github)

        assert report["error"].startswith("CiQuotaRefused")
        assert await _count(db_session, CiWorkflowRun) == 3
        assert set(await db_session.scalars(select(CiJob.job_id))) == {1002}
        assert report["jobs_owed"] == 2

    _portal.call(_run_it)


def test_a_day_without_sweeps_is_listed_and_stored_in_full(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()

    async def _run_it() -> None:
        await _connect(db_session)
        assert (await _sweep(db_session, github))["error"] == ""

        # A busy day this repository has had: more runs than one INSERT can bind.
        later = NOW + timedelta(days=1)
        for n in range(2000):
            run = _run(5000 + n, age=-timedelta(seconds=40 * n + 60))
            github.runs[run["id"]] = run
        report = await _sweep(db_session, github, at=later, jobs_budget=0)

        assert report["error"] == ""
        assert report["runs_upserted"] == 2000
        assert min(since for since, _ in github.listings[1:]) <= NOW
        assert await _count(db_session, CiActivitySweep) == 2

    _portal.call(_run_it)


def test_a_run_github_no_longer_has_does_not_stop_the_sweeps_after_it(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    gone = _run(201, age=timedelta(minutes=10))
    older = _run(200, age=timedelta(minutes=20))
    skipped = _run(199, age=timedelta(minutes=30), conclusion="skipped")
    github.runs = {201: gone, 200: older, 199: skipped}
    github.jobs = {200: [_job(2000, older)]}
    github.gone = {201}

    async def _run_it() -> None:
        await _connect(db_session)
        first = await _sweep(db_session, github)
        second = await _sweep(db_session, github, at=NOW + timedelta(minutes=5))

        assert first["error"] == "" and second["error"] == ""
        assert github.jobs_read == [201, 200], "each read once; the skipped run never"
        assert set(await db_session.scalars(select(CiJob.job_id))) == {2000}
        assert len(github.queue_reads) > 0

    _portal.call(_run_it)


def test_the_queue_is_read_from_the_window_start_then_from_the_last_sweep(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()

    async def _run_it() -> None:
        await _connect(db_session)
        await _sweep(db_session, github)
        first = list(github.queue_reads)
        github.queue_reads.clear()
        await _sweep(db_session, github, at=NOW + timedelta(minutes=5))

        assert first[0][0] == NOW - ingest.WINDOW
        assert first[-1][1] == NOW
        assert github.queue_reads[0][0] == NOW - ingest.QUEUE_OVERLAP
        assert github.queue_reads[-1][1] == NOW + timedelta(minutes=5)

    _portal.call(_run_it)


def test_a_busy_day_of_queue_events_is_stored_in_full(
    db_session: AsyncSession, _portal: BlockingPortal, tokens
):
    github = _GitHub()
    github.events = [
        QueueEvent(pr, kind, NOW - timedelta(hours=1, seconds=pr), None, "x")
        for pr in range(3000)
        for kind in ("added", "removed")
    ]

    async def _run_it() -> None:
        await _connect(db_session)
        report = await _sweep(db_session, github)

        assert report["error"] == ""
        assert await _count(db_session, CiMergeQueueEvent) == 6000

    _portal.call(_run_it)


def test_a_database_error_ends_the_sweep_with_a_recorded_reason(
    db_session: AsyncSession, _portal: BlockingPortal, tokens, monkeypatch
):
    github = _GitHub()
    github.runs = {301: _run(301, age=timedelta(minutes=10))}

    async def _refused(self, repo, events):
        raise DBAPIError("INSERT ...", {}, Exception("disk full"))

    monkeypatch.setattr(CiActivityStore, "add_queue_events", _refused)

    async def _run_it() -> None:
        await _connect(db_session)
        report = await _sweep(db_session, github)

        assert "disk full" in report["error"]
        recorded = await db_session.scalar(select(CiActivitySweep))
        assert recorded is not None and recorded.report["error"] == report["error"]
        assert await _count(db_session, CiWorkflowRun) == 1

    _portal.call(_run_it)
