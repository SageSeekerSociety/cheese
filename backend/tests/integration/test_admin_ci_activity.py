"""The CI activity page's figures, read through the route from seeded rows.

Each rule here is one a person reading the page could state:

* Only platform admins read it.
* Merge-queue time runs from a PR's FIRST entry to its merge, so an ejection
  and re-entry in between is part of what it took.
* Runner wait counts only jobs that got a runner, and hosted and self-hosted
  are reported apart.
* `CI required` duration is a full-tier run's first attempt, created to last
  job done; a draft's quick tier and a cancelled run are not CI durations.
* A merged PR's CI rounds include its runs GitHub no longer links to it (the
  branch is gone), and not a run on the same branch name after it merged.
* A run that passed only on a re-run is counted as fail-then-pass.
* A day with nothing to measure has no median, not a median of 0.
* The jobs and steps tables can be narrowed to one runner.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.ci_activity.models import (
    CiJob,
    CiJobStep,
    CiMergedPullRequest,
    CiMergeQueueEvent,
    CiWorkflowRun,
)
from app.domain.ci_activity.schemas import CiBoardOut, CiJobsOut, CiStepsOut
from tests.conftest import seed_user
from tests.integration.conftest import session_auth_headers

ADMIN = "ci-activity-admin"
STRANGER = "ci-activity-stranger"
REPO = "owner/ci-activity-board"
REQUIRED = ".github/workflows/required-ci.yml"
# Two hours into today (UTC), so every seeded moment is inside a 1-day window
# and none is in the future.
TODAY = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
T0 = TODAY + timedelta(minutes=10)


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    monkeypatch.setattr(settings, "platform_repository", REPO)
    return ADMIN


def _seed(client, objects: list) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            for obj in objects:
                session.add(obj)
                await session.flush()
            await session.commit()

    asyncio.run(_run())


_ids = iter(range(10_000, 99_999))


def _run(
    *,
    event="pull_request",
    created=T0,
    conclusion="success",
    attempt=1,
    pr=None,
    branch="feat/x",
    path=REQUIRED,
) -> CiWorkflowRun:
    return CiWorkflowRun(
        id=uuid.uuid4(),
        repo=REPO,
        run_id=next(_ids),
        workflow_id=1,
        workflow_name="Required CI",
        workflow_path=path,
        event=event,
        head_branch=branch,
        head_sha="a" * 40,
        pr_number=pr,
        run_attempt=attempt,
        status="completed",
        conclusion=conclusion,
        created_at=created,
        jobs_attempt=attempt,
        ingested_at=created,
    )


def _job(
    run: CiWorkflowRun,
    *,
    name="backend / pure (1)",
    wait_s=60,
    took_s=300,
    kind="hosted",
    runner="GitHub Actions 7",
    labels=("ubuntu-latest",),
    attempt=1,
    conclusion="success",
    start_after_s=0,
) -> CiJob:
    created = run.created_at + timedelta(seconds=start_after_s)
    started = created + timedelta(seconds=wait_s)
    return CiJob(
        id=uuid.uuid4(),
        run_row_id=run.id,
        repo=REPO,
        job_id=next(_ids),
        run_attempt=attempt,
        name=name,
        status="completed",
        conclusion=conclusion,
        created_at=created,
        started_at=started,
        completed_at=started + timedelta(seconds=took_s),
        runner_name=runner,
        runner_kind=kind,
        labels=list(labels),
    )


def _event(
    pr, kind, at, reason=None, actor="someone", branch=None
) -> CiMergeQueueEvent:
    return CiMergeQueueEvent(
        repo=REPO,
        pr_number=pr,
        kind=kind,
        at=at,
        reason=reason,
        actor=actor,
        head_branch=branch,
    )


def _board(client, days=1) -> dict:
    response = client.get(
        f"/admin/ci-activity?days={days}", headers=session_auth_headers(ADMIN)
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    CiBoardOut.model_validate(data)
    return data


def _pull(
    pr, branch, created=T0 - timedelta(minutes=5), merged=T0 + timedelta(minutes=90)
):
    return CiMergedPullRequest(
        repo=REPO,
        pr_number=pr,
        head_branch=branch,
        created_at=created,
        merged_at=merged,
    )


def test_a_stranger_cannot_read_the_page(client, as_admin):
    seed_user(client, STRANGER)

    response = client.get("/admin/ci-activity", headers=session_auth_headers(STRANGER))

    assert response.status_code == 403


def test_merge_queue_time_runs_from_the_first_entry_through_an_ejection(
    client, as_admin
):
    seed_user(client, ADMIN)
    _seed(
        client,
        [
            _event(7, "added", T0),
            _event(
                7,
                "removed",
                T0 + timedelta(minutes=20),
                "failed_checks",
                "github-merge-queue",
            ),
            _event(7, "added", T0 + timedelta(minutes=30)),
            _event(
                7, "removed", T0 + timedelta(minutes=50), "merged", "github-merge-queue"
            ),
        ],
    )

    board = _board(client)

    assert board["headline"]["merge_queue_minutes"]["p50"] == pytest.approx(50)
    assert board["ejections"] == [
        {"reason": "failed_checks", "actor": "github-merge-queue", "count": 1}
    ]


def test_runner_wait_counts_jobs_that_got_a_runner_and_splits_hosted_from_self_hosted(
    client, as_admin
):
    seed_user(client, ADMIN)
    run = _run()
    never_ran = _job(run, wait_s=900, kind=None, runner=None)
    never_ran.started_at = None
    never_ran.completed_at = None
    _seed(
        client,
        [
            run,
            _job(run, wait_s=100),
            _job(run, wait_s=300),
            _job(
                run,
                wait_s=4,
                kind="self_hosted",
                runner="cheese-ci-runner-1",
                labels=("self-hosted", "cheese-ci"),
            ),
            never_ran,
        ],
    )

    waits = _board(client)["headline"]["runner_wait_seconds"]

    assert waits["hosted"]["n"] == 2 and waits["hosted"]["p50"] == pytest.approx(200)
    assert waits["self_hosted"]["n"] == 1 and waits["self_hosted"][
        "p50"
    ] == pytest.approx(4)
    assert waits["all"]["n"] == 3


def test_ci_required_duration_is_a_full_tier_first_attempt_only(client, as_admin):
    seed_user(client, ADMIN)
    full = _run()
    draft = _run()
    cancelled = _run(conclusion="cancelled")
    rerun = _run(attempt=2)
    _seed(
        client,
        [
            full,
            draft,
            cancelled,
            rerun,
            _job(full, name="CI required", wait_s=0, took_s=600),
            _job(draft, name="CI required (draft quick)", wait_s=0, took_s=120),
            _job(cancelled, name="CI required", wait_s=0, took_s=60),
            _job(rerun, name="CI required", wait_s=0, took_s=1200),
            _job(
                rerun,
                name="CI required",
                wait_s=0,
                took_s=60,
                attempt=2,
                start_after_s=7200,
            ),
        ],
    )

    minutes = _board(client)["headline"]["ci_required_minutes"]["pull_request"]

    # The full run took 10 minutes; the re-run's first attempt 20, its second
    # attempt two hours later is not CI time.
    assert minutes["n"] == 2
    assert (minutes["p50"], minutes["max"]) == (pytest.approx(15), pytest.approx(20))


def test_a_merged_prs_rounds_include_runs_github_no_longer_links(client, as_admin):
    seed_user(client, ADMIN)
    merged = T0 + timedelta(minutes=90)
    linked = _run(pr=8, branch="feat/eight")
    unlinked = _run(
        pr=None,
        branch="feat/eight",
        created=T0 + timedelta(minutes=30),
        conclusion="failure",
    )
    superseded = _run(pr=None, branch="feat/eight", conclusion="cancelled")
    queue = _run(
        event="merge_group",
        pr=8,
        branch="gh-readonly-queue/main/pr-8-abc",
        created=T0 + timedelta(minutes=60),
    )
    after = _run(pr=None, branch="feat/eight", created=merged + timedelta(minutes=5))
    _seed(
        client,
        [
            linked,
            unlinked,
            superseded,
            queue,
            after,
            _pull(8, "feat/eight", merged=merged),
            _event(8, "added", T0 + timedelta(minutes=55), branch="feat/eight"),
            _event(
                8,
                "removed",
                merged,
                "merged",
                "github-merge-queue",
                branch="feat/eight",
            ),
        ],
    )

    rounds = _board(client)["headline"]["rounds"]

    assert rounds["prs"] == 1
    assert rounds["pr_rounds"]["mean"] == pytest.approx(2)
    assert rounds["queue_rounds"]["mean"] == pytest.approx(1)
    assert rounds["more_than_one"] == pytest.approx(1)


def test_direct_merge_rounds_exclude_other_uses_of_the_same_branch(client, as_admin):
    seed_user(client, ADMIN)
    _seed(
        client,
        [
            _pull(81, "feat/reused"),
            _pull(82, "feat/no-ci"),
            _run(pr=None, branch="feat/reused", created=T0 - timedelta(days=2)),
            _run(pr=None, branch="feat/reused", created=T0),
            _run(pr=None, branch="feat/reused", created=T0 + timedelta(minutes=20)),
            _run(pr=None, branch="feat/reused", created=T0 + timedelta(minutes=100)),
        ],
    )

    board = _board(client)

    assert board["headline"]["rounds"]["prs"] == 2
    assert board["headline"]["rounds"]["pr_rounds"]["mean"] == pytest.approx(1)
    assert board["headline"]["rounds"]["queue_rounds"]["mean"] == 0
    assert board["headline"]["rounds"]["more_than_one"] == pytest.approx(0.5)
    assert board["headline"]["merge_queue_minutes"]["n"] == 0
    assert board["headline"]["rounds"]["incomplete_prs"] == 2


def test_a_run_that_passed_only_on_a_rerun_is_fail_then_pass(client, as_admin):
    seed_user(client, ADMIN)
    flaky = _run(attempt=2)
    steady = _run()
    _seed(
        client,
        [
            flaky,
            steady,
            _job(flaky, conclusion="failure"),
            _job(flaky, attempt=2, start_after_s=600),
            _job(steady),
        ],
    )

    share = _board(client)["headline"]["fail_then_pass"]

    assert (share["runs"], share["of"]) == (1, 2)


def test_a_day_with_no_merge_has_no_median(client, as_admin):
    seed_user(client, ADMIN)
    _seed(
        client,
        [
            _event(9, "added", T0),
            _event(
                9, "removed", T0 + timedelta(minutes=10), "merged", "github-merge-queue"
            ),
        ],
    )

    daily = _board(client, days=3)["daily"]

    assert [d["merge_queue_p50"] for d in daily[:2]] == [None, None]
    assert daily[2]["merge_queue_p50"] == pytest.approx(10)


def test_the_job_and_step_tables_narrow_to_one_runner(client, as_admin):
    seed_user(client, ADMIN)
    run = _run()
    on_box = _job(
        run,
        name="e2e / e2e",
        took_s=900,
        kind="self_hosted",
        runner="cheese-ci-runner-2",
        labels=("self-hosted",),
    )
    hosted = _job(run, name="e2e / e2e", took_s=300)
    _seed(client, [run, on_box, hosted])
    _seed(
        client,
        [
            CiJobStep(
                job_row_id=on_box.id,
                number=3,
                name="Run e2e",
                conclusion="success",
                started_at=on_box.started_at,
                completed_at=on_box.started_at + timedelta(seconds=800),
            ),
            CiJobStep(
                job_row_id=hosted.id,
                number=3,
                name="Run e2e",
                conclusion="success",
                started_at=hosted.started_at,
                completed_at=hosted.started_at + timedelta(seconds=200),
            ),
        ],
    )
    headers = session_auth_headers(ADMIN)

    jobs = client.get(
        "/admin/ci-activity/jobs?days=1&runner=cheese-ci-runner-2", headers=headers
    ).json()["data"]["jobs"]
    steps = client.get(
        "/admin/ci-activity/steps",
        params={
            "days": 1,
            "workflow": REQUIRED,
            "job": "e2e / e2e",
            "runner": "cheese-ci-runner-2",
        },
        headers=headers,
    ).json()["data"]["steps"]

    assert [(j["name"], j["jobs"], j["duration_s"]["p50"]) for j in jobs] == [
        ("e2e / e2e", 1, pytest.approx(900))
    ]
    assert [(s["name"], s["duration_s"]["p50"]) for s in steps] == [
        ("Run e2e", pytest.approx(800))
    ]


def test_same_job_name_in_different_workflows_stays_separate(client, as_admin):
    seed_user(client, ADMIN)
    backup = _run(path=".github/workflows/backup-freshness.yml")
    backup.workflow_name = "Backup freshness"
    tls = _run(path=".github/workflows/tls-expiry.yml")
    tls.workflow_name = "TLS expiry"
    jobs = [
        _job(
            r,
            name="check",
            took_s=seconds,
            kind="self_hosted",
            runner="cheese-dev-env1",
        )
        for r, seconds in [(backup, 10), (tls, 50)]
    ]
    _seed(client, [backup, tls, *jobs])
    _seed(
        client,
        [
            CiJobStep(
                job_row_id=j.id,
                number=1,
                name="Check",
                conclusion="success",
                started_at=j.started_at,
                completed_at=j.completed_at,
            )
            for j in jobs
        ],
    )
    headers = session_auth_headers(ADMIN)
    rows = client.get(
        "/admin/ci-activity/jobs",
        params={"days": 1, "runner": "cheese-dev-env1"},
        headers=headers,
    ).json()["data"]["jobs"]

    CiJobsOut.model_validate({"jobs": rows})
    assert len(rows) == 2
    assert {
        (r["workflow_path"], r["name"], r["jobs"], r["duration_s"]["p50"]) for r in rows
    } == {(backup.workflow_path, "check", 1, 10), (tls.workflow_path, "check", 1, 50)}
    step_data = client.get(
        "/admin/ci-activity/steps",
        params={
            "days": 1,
            "runner": "cheese-dev-env1",
            "workflow": backup.workflow_path,
            "job": "check",
        },
        headers=headers,
    ).json()["data"]
    CiStepsOut.model_validate(step_data)
    steps = step_data["steps"]
    assert [
        (s["name"], s["duration_s"]["n"], s["duration_s"]["p50"]) for s in steps
    ] == [("Check", 1, 10)]
