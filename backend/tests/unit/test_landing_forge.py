"""Reading a merge's deployment from GitHub.

The rules, as stated before the code was written:

- A deployment of the merge commit, or of a commit after it on the branch,
  deployed the merge; one of an earlier commit did not.
- A deployment that failed is reported, unless a later one that also includes
  the merge went through.
- One still running is not reported yet.
- An installation not granted deployments has none to report.
"""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.domain.review.landing_forge import (
    Check,
    ForgeUnavailable,
    GitHubLandingReads,
)

MERGED = "m" * 40
SINCE = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


class _Tokens:
    def __init__(self, granted=("checks", "deployments")):
        self._granted = {name: "read" for name in granted}

    async def installation_token(self):
        return "t", None

    async def granted_permissions(self):
        return self._granted


def _reads(deployments, *, statuses, after=(), granted=("checks", "deployments")):
    """``deployments``: (id, sha, created). ``after``: the shas that come after
    the merge on the branch."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/repos/o/r")
        if path == "/deployments":
            return httpx.Response(
                200,
                json=[
                    {"id": i, "sha": sha, "created_at": at, "environment": "dev"}
                    for i, sha, at in deployments
                ],
            )
        if path.startswith("/deployments/"):
            state = statuses[int(path.split("/")[2])]
            return httpx.Response(200, json=[{"state": state, "log_url": "u"}])
        if path.startswith("/compare/"):
            head = path.split("...")[1]
            return httpx.Response(
                200, json={"status": "ahead" if head in after else "behind"}
            )
        return httpx.Response(404)

    return GitHubLandingReads(
        None,
        _Tokens(granted),
        "https://api.github.test",
        "o",
        "r",
        transport=httpx.MockTransport(handle),
    )


def _deployment(reads):
    return asyncio.run(reads.deployment(MERGED, SINCE))


def test_a_deployment_of_a_later_commit_deployed_the_merge():
    reads = _reads(
        [(1, "later", "2026-10-08T12:10:00Z")], statuses={1: "success"}, after={"later"}
    )
    assert _deployment(reads).environment == "dev"


def test_a_deployment_of_an_earlier_commit_did_not():
    reads = _reads([(1, "earlier", "2026-10-08T12:10:00Z")], statuses={1: "success"})
    assert _deployment(reads) is None


def test_a_failed_deployment_is_reported_unless_a_later_one_went_through():
    failed = _reads([(1, MERGED, "2026-10-08T12:10:00Z")], statuses={1: "failure"})
    recovered = _reads(
        [(1, MERGED, "2026-10-08T12:10:00Z"), (2, "later", "2026-10-08T12:20:00Z")],
        statuses={1: "failure", 2: "success"},
        after={"later"},
    )
    assert _deployment(failed).state == "failure"
    assert _deployment(recovered).state == "success"


def test_a_deployment_still_running_is_not_reported_yet():
    reads = _reads([(1, MERGED, "2026-10-08T12:10:00Z")], statuses={1: "in_progress"})
    assert _deployment(reads) is None


def test_deployments_before_the_merge_landed_are_not_read():
    reads = _reads([(1, MERGED, "2026-10-08T11:00:00Z")], statuses={1: "success"})
    assert _deployment(reads) is None


def test_an_installation_without_deployments_has_none_to_report():
    reads = _reads([], statuses={}, granted=("checks",))
    with pytest.raises(ForgeUnavailable):
        _deployment(reads)


# --- the batched main gate (#4249) -----------------------------------------


def _reads_with(handle):
    return GitHubLandingReads(
        None,
        _Tokens(),
        "https://api.github.test",
        "o",
        "r",
        transport=httpx.MockTransport(handle),
        branch="main",
    )


def _gate_run(status: str, conclusion: str | None, name: str = "CI required"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": "",
    }


def test_the_covering_run_is_the_newest_gate_that_finished():
    """A queued gate and a cancelled one are not answers; the newest finished
    gate is. A re-run's green wins over the red that came before it."""

    def handle(request):
        path = request.url.path.removeprefix("/repos/o/r")
        if path.startswith("/compare/"):
            return httpx.Response(
                200,
                json={
                    "status": "ahead",
                    "commits": [{"sha": "b"}, {"sha": "c"}, {"sha": "d"}],
                },
            )
        if path.endswith("/check-runs"):
            sha = path.split("/")[2]
            return httpx.Response(
                200,
                json={
                    "check_runs": {
                        "b": [_gate_run("completed", "success")],
                        "c": [_gate_run("completed", "cancelled")],
                        "d": [_gate_run("queued", None)],
                    }[sha]
                },
            )
        return httpx.Response(404)

    assert asyncio.run(_reads_with(handle).covering_run("a", anchor="a0"))[0] == "b"


def test_a_repository_whose_main_pushes_run_no_gate_has_no_covering_run():
    def handle(request):
        path = request.url.path.removeprefix("/repos/o/r")
        if path.startswith("/compare/"):
            return httpx.Response(
                200, json={"status": "ahead", "commits": [{"sha": "b"}]}
            )
        if path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        return httpx.Response(404)

    assert asyncio.run(_reads_with(handle).covering_run("a")) is None


def test_the_green_mark_is_read_from_its_ref_and_absent_is_none():
    def handle(request):
        path = request.url.path.removeprefix("/repos/o/r")
        if path == "/git/ref/heads/main-green":
            return httpx.Response(200, json={"object": {"sha": "green"}})
        return httpx.Response(404)

    assert asyncio.run(_reads_with(handle).main_green()) == "green"
    assert (
        asyncio.run(_reads_with(lambda request: httpx.Response(404)).main_green())
        is None
    )


def _compare_then_commits(commits):
    def handle(request):
        path = request.url.path.removeprefix("/repos/o/r")
        if path.startswith("/compare/"):
            return httpx.Response(
                200,
                json={
                    "status": "ahead",
                    "total_commits": len(commits),
                    "commits": [{"sha": sha} for sha in commits],
                },
            )
        if path.startswith("/commits/"):
            sha = path.split("/")[2]
            return httpx.Response(
                200,
                json={
                    "commit": {"message": f"feat: {sha}\n\nbody"},
                    "files": [{"filename": f"{sha}.py"}],
                },
            )
        return httpx.Response(404)

    return _reads_with(handle)


def test_reading_a_batch_reports_every_commit_and_what_it_changed():
    batch = asyncio.run(_compare_then_commits(["b", "c"]).commits_between("a", "d"))
    assert batch.complete
    assert batch.total == 2
    assert [(change.sha, change.subject) for change in batch.changes] == [
        ("b", "feat: b"),
        ("c", "feat: c"),
    ]
    assert batch.changes[0].files == ("b.py",)


def test_a_batch_whose_commits_were_not_all_read_is_not_complete():
    """Past the cap some commits are never looked at; nothing may be pinned on
    one of the rest."""
    shas = [f"c{i}" for i in range(30)]
    batch = asyncio.run(_compare_then_commits(shas).commits_between("a", "z"))
    assert batch.total == 30
    assert len(batch.changes) == 20
    assert not batch.complete


def test_a_commit_the_forge_refuses_to_describe_makes_the_batch_incomplete():
    def handle(request):
        path = request.url.path.removeprefix("/repos/o/r")
        if path.startswith("/compare/"):
            return httpx.Response(
                200,
                json={
                    "status": "ahead",
                    "total_commits": 2,
                    "commits": [{"sha": "b"}, {"sha": "c"}],
                },
            )
        if path == "/commits/c":
            return httpx.Response(403)
        return httpx.Response(
            200,
            json={
                "commit": {"message": "feat: b"},
                "files": [{"filename": "b.py"}],
            },
        )

    batch = asyncio.run(_reads_with(handle).commits_between("a", "c"))
    assert [change.sha for change in batch.changes] == ["b"]
    assert not batch.complete


def test_the_failed_tests_are_read_out_of_the_red_jobs_logs():
    def handle(request):
        path = request.url.path.removeprefix("/repos/o/r")
        if path.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "backend tests (unit)",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/o/r/actions/runs/1/job/2",
                        },
                        _gate_run("completed", "failure"),
                    ]
                },
            )
        if path == "/actions/jobs/2/logs":
            return httpx.Response(
                200,
                text=(
                    "2026-10-18T09:00:00.1Z FAILED backend/tests/test_a.py::test_x - boom\n"
                    "2026-10-18T09:00:00.2Z FAILED backend/tests/test_b.py::test_y - boom\n"
                    "2026-10-18T09:00:00.3Z ##[error]Process completed with exit code 1.\n"
                ),
            )
        return httpx.Response(404)

    assert asyncio.run(_reads_with(handle).failed_tests("d")) == [
        "backend/tests/test_a.py",
        "backend/tests/test_b.py",
    ]


def test_ancestry_is_what_the_compare_says():
    def handle(request):
        path = request.url.path.removeprefix("/repos/o/r")
        if path == "/compare/a...b":
            return httpx.Response(200, json={"status": "ahead"})
        return httpx.Response(200, json={"status": "behind"})

    reads = _reads_with(handle)
    assert asyncio.run(reads.is_ancestor("a", "b"))
    assert not asyncio.run(reads.is_ancestor("b", "a"))
    assert asyncio.run(reads.is_ancestor("a", "a"))


def test_a_rerun_that_passed_wins_over_the_red_it_replaced():
    from app.domain.review.landing_forge import gate_run

    red_then_green = [
        Check("CI required", "completed", "failure", "", suite=1),
        Check("CI required", "completed", "success", "", suite=1),
    ]
    assert gate_run(red_then_green).conclusion == "success"


def test_a_red_gate_beats_a_cancelled_one_whatever_the_order():
    from app.domain.review.landing_forge import gate_run

    for runs in (
        [
            Check("CI required", "completed", "cancelled", "", suite=1),
            Check("CI required", "completed", "failure", "", suite=1),
        ],
        [
            Check("CI required", "completed", "failure", "", suite=1),
            Check("CI required", "completed", "cancelled", "", suite=1),
        ],
    ):
        assert gate_run(runs).conclusion == "failure"


def test_a_neutral_gate_counts_as_a_pass():
    from app.domain.review.landing_forge import gate_run

    # The precedence has to matter: with the neutral run first the old
    # first-match code would return the failure.
    runs = [
        Check("CI required", "completed", "failure", "", suite=1),
        Check("CI required", "completed", "neutral", "", suite=1),
    ]
    assert gate_run(runs).conclusion == "neutral"


# --- the culprit probe -------------------------------------------------------
#
# - The dispatch names the default branch (never a commit), the probed commit,
#   the tests and a nonce; its run is found by the nonce in the run's name.
# - Without `actions: write` nothing is posted.
# - Only the step that runs the named tests failing is a failure; any other
#   break, or a run still going, is unknown.


class _ActionsTokens(_Tokens):
    def __init__(self, actions: str | None = "write"):
        super().__init__(granted=("checks",))
        if actions:
            self._granted["actions"] = actions


def _probe_reads(
    *, actions: str | None = "write", runs=None, steps=(), status="completed"
):
    posts: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/repos/o/r")
        if request.method == "POST":
            assert path == "/actions/workflows/culprit-bisect.yml/dispatches"
            posts.append(json.loads(request.content))
            return httpx.Response(204)
        if path == "/actions/workflows/culprit-bisect.yml/runs":
            return httpx.Response(200, json={"workflow_runs": runs or []})
        if path == "/actions/runs/7":
            return httpx.Response(200, json={"status": status})
        if path == "/actions/runs/7/jobs":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "steps": [
                                {"name": name, "conclusion": conclusion}
                                for name, conclusion in steps
                            ]
                        }
                    ]
                },
            )
        return httpx.Response(404)

    reads = GitHubLandingReads(
        None,
        _ActionsTokens(actions),
        "https://api.github.test",
        "o",
        "r",
        transport=httpx.MockTransport(handle),
        branch="main",
    )
    return reads, posts


def test_the_dispatch_names_the_branch_the_commit_and_the_nonce(monkeypatch):
    monkeypatch.setattr("app.domain.review.landing_forge._RUN_LOOKUP_WAIT_S", 0)
    reads, posts = _probe_reads(
        runs=[{"id": 7, "name": "culprit-bisect", "display_title": "culprit n-1"}]
    )
    run = asyncio.run(
        reads.dispatch_bisect("a" * 40, ["backend/tests/unit/test_x.py"], "n-1")
    )
    assert run == "7"
    assert posts == [
        {
            "ref": "main",
            "inputs": {
                "sha": "a" * 40,
                "tests": "backend/tests/unit/test_x.py",
                "nonce": "n-1",
            },
        }
    ]


def test_another_nonce_is_not_our_run(monkeypatch):
    monkeypatch.setattr("app.domain.review.landing_forge._RUN_LOOKUP_WAIT_S", 0)
    reads, _ = _probe_reads(
        runs=[{"id": 7, "name": "culprit-bisect", "display_title": "culprit other"}]
    )
    assert asyncio.run(reads.dispatch_bisect("a" * 40, ["t.py"], "n-1")) is None


def test_without_actions_write_nothing_is_posted():
    for actions in (None, "read"):
        reads, posts = _probe_reads(
            actions=actions,
            runs=[{"id": 7, "name": "culprit-bisect", "display_title": "culprit n"}],
        )
        assert asyncio.run(reads.dispatch_bisect("a" * 40, ["t.py"], "n")) is None
        assert posts == []


def _verdict(steps, status="completed"):
    reads, _ = _probe_reads(steps=steps, status=status)
    return asyncio.run(reads.bisect_verdict("7"))


def test_the_named_tests_step_decides_the_verdict():
    named = "run the named tests"
    assert _verdict([("install", "success"), (named, "success")]) == "pass"
    assert _verdict([("install", "success"), (named, "failure")]) == "fail"


def test_a_break_before_the_tests_is_unknown():
    assert _verdict([("install backend dependencies", "failure")]) == "unknown"
    assert _verdict([("run the named tests", "skipped")]) == "unknown"
    assert (
        _verdict([("run the named tests", "success")], status="in_progress")
        == "unknown"
    )
