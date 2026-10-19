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
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings as platform
from app.domain.review.landing_forge import (
    Check,
    ForgeUnavailable,
    GitHubLandingReads,
    _failed_paths,
    gate_run,
)
from app.domain.review.landing_watch import Batch, BatchCommit, _search_for_culprit
from app.domain.review.main_gate import PLATFORM_GATE_NAMES, MainGate, gate_for

#: The name the platform's own repository gives its batch gate.
GATE = PLATFORM_GATE_NAMES.check

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
        gate=PLATFORM_GATE_NAMES,
    )


def _gate_run(status: str, conclusion: str | None, name: str = GATE):
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


def _reads_failed_log(log, *, redirect=False):
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
            if redirect:
                return httpx.Response(
                    302, headers={"location": "https://logs.test/log"}
                )
            return httpx.Response(200, text=log)
        if request.url.host == "logs.test":
            assert "authorization" not in request.headers
            return httpx.Response(200, text=log)
        return httpx.Response(404)

    return _reads_with(handle)


def test_the_failed_tests_are_read_out_of_the_red_jobs_logs():
    reads = _reads_failed_log(
        "2026-10-18T09:00:00.1Z FAILED backend/tests/test_a.py::test_x - boom\n"
        "2026-10-18T09:00:00.2Z FAILED backend/tests/test_b.py::test_y - boom\n"
        "2026-10-18T09:00:00.3Z ##[error]Process completed with exit code 1.\n"
    )
    assert asyncio.run(reads.failed_paths("d")) == [
        "backend/tests/test_a.py",
        "backend/tests/test_b.py",
    ]
    assert asyncio.run(reads.failed_paths("d", tests_only=True)) == [
        "backend/tests/test_a.py",
        "backend/tests/test_b.py",
    ]


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        pytest.param(
            "2026-10-19T02:53:15.7493805Z F401 [*] `uuid` imported but unused\n"
            "2026-10-19T02:53:15.7494155Z   --> tests/integration/test_app_sign_in.py:15:8\n"
            "2026-10-19T02:53:15.7497248Z F401 [*] `app.core.config.settings` imported but unused\n"
            "2026-10-19T02:53:15.7497696Z   --> tests/integration/test_app_sign_in.py:22:29\n"
            "2026-10-19T02:53:15.7500075Z F401 [*] `tests.conftest.seed_user` imported but unused\n"
            "2026-10-19T02:53:15.7500352Z   --> tests/integration/test_app_sign_in.py:23:28\n",
            ["tests/integration/test_app_sign_in.py"],
            id="ruff-full",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z tests/integration/test_app_sign_in.py:15:24: "
            "F401 [*] `sqlalchemy.select` imported but unused\n",
            ["tests/integration/test_app_sign_in.py"],
            id="ruff-concise",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z /home/runner/work/cheese/cheese/backend/app/main.py\n"
            "2026-10-19T02:53:25.2Z   /home/runner/work/cheese/cheese/backend/app/main.py:15:24"
            ' - \x1b[31merror\x1b[0m: "missing" is not defined (reportUndefinedVariable)\n',
            ["/home/runner/work/cheese/cheese/backend/app/main.py"],
            id="pyright",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z src/views/SignIn.vue(28,12): error TS2339: "
            "Property 'missing' does not exist on type '{}'.\n",
            ["src/views/SignIn.vue"],
            id="vue-tsc",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z collab/server.ts(10,5): error TS2322: "
            "Type 'string' is not assignable to type 'number'.\n",
            ["collab/server.ts"],
            id="tsc",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z /home/runner/work/cheese/cheese/frontend/src/SignIn.vue\n"
            "2026-10-19T02:53:25.2Z   10:5  error  'unused' is assigned a value but never used"
            "  @typescript-eslint/no-unused-vars\n"
            "2026-10-19T02:53:25.3Z   11:5  error  Unexpected any  @typescript-eslint/no-explicit-any\n"
            "2026-10-19T02:53:25.4Z \n"
            "2026-10-19T02:53:25.5Z /home/runner/work/cheese/cheese/frontend/src/Warning.vue\n"
            "2026-10-19T02:53:25.6Z   12:3  warning  Missing prop  vue/require-default-prop\n",
            ["/home/runner/work/cheese/cheese/frontend/src/SignIn.vue"],
            id="eslint-stylish",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z Component boundary violations:\n"
            "2026-10-19T02:53:25.2Z   src/components/SignIn.vue:10:5  cheese/no-api-imports  Import forbidden\n",
            ["src/components/SignIn.vue"],
            id="eslint-boundary-report",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z Design-token violations:\n"
            "2026-10-19T02:53:25.2Z   src/style.css:10:5  Unexpected hex-color (color-no-hex)\n",
            ["src/style.css"],
            id="stylelint-design-tokens",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z   src/style.css:10 Unclosed block\n",
            ["src/style.css"],
            id="stylelint-syntax-error",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z src/views/SignIn.vue\n"
            "2026-10-19T02:53:25.2Z   2: [Chinese outside the catalog] <span>确定</span>\n"
            "2026-10-19T02:53:25.3Z \n"
            "2026-10-19T02:53:25.4Z Chinese a user can read goes through the catalog:\n",
            ["src/views/SignIn.vue"],
            id="i18n-grouped-diagnostic",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z New scenes that are not standalone-ready (this is what the ratchet blocks):\n"
            "2026-10-19T02:53:25.2Z   src/views/SignIn.vue: C\n"
            "2026-10-19T02:53:25.3Z     - reads API\n"
            "2026-10-19T02:53:25.4Z Scenes that stopped being standalone-ready (also what the ratchet blocks):\n"
            "2026-10-19T02:53:25.5Z   src/views/Settings.vue: A -> B\n"
            "2026-10-19T02:53:25.6Z Better than that tree says, nothing to do:\n"
            "2026-10-19T02:53:25.7Z   src/views/Improved.vue: was debt, is standalone-ready now\n",
            ["src/views/Settings.vue", "src/views/SignIn.vue"],
            id="graded-static-diagnostics",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z FAIL: new debt cannot be frozen.\n"
            "2026-10-19T02:53:25.2Z     NEW: src/views/SignIn.vue\n"
            "2026-10-19T02:53:25.3Z     BASELINE GREW: src/views/SignIn.vue -> src/views/Form.vue (baseline line 3)\n",
            ["src/views/Form.vue", "src/views/SignIn.vue"],
            id="tagged-static-diagnostics",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z Better than that tree says, nothing to do:\n"
            "2026-10-19T02:53:25.2Z   src/views/Improved.vue: is a container now; its view src/views/Form.vue is what is frozen\n"
            "2026-10-19T02:53:25.3Z NEW: src/views/Added.vue\n",
            [],
            id="passing-reports-are-not-failures",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z   /repo/backend/app/main.py:15:24 - warning: unused\n"
            "2026-10-19T02:53:25.2Z Run ruff check backend/app/main.py\n"
            "2026-10-19T02:53:25.3Z ##[error]Process completed with exit code 1.\n",
            [],
            id="warnings-and-commands-are-not-failures",
        ),
    ],
)
def test_static_failures_name_their_files(log, expected):
    assert sorted(set(_failed_paths(log))) == expected
    assert _failed_paths(log, tests_only=True) == []


@pytest.mark.parametrize("redirect", [False, True])
def test_static_diagnostics_before_long_test_collection_are_kept(redirect):
    log = (
        "2026-10-19T02:53:15.7493805Z F401 [*] `uuid` imported but unused\n"
        "2026-10-19T02:53:15.7494155Z   --> tests/integration/test_app_sign_in.py:15:8\n"
        + "2026-10-19T02:54:00.0Z tests/unit/test_a.py::test_ok\n"
        * 12000
    )
    reads = _reads_failed_log(log, redirect=redirect)
    assert asyncio.run(reads.failed_paths("d")) == [
        "tests/integration/test_app_sign_in.py"
    ]


def test_only_test_failure_lines_are_used_for_test_probes():
    log = (
        "2026-10-19T02:53:15.7Z   --> tests/integration/test_app_sign_in.py:15:8\n"
        "2026-10-19T02:53:16.0Z FAILED tests/unit/test_a.py::test_x - boom\n"
        "2026-10-19T02:53:17.0Z FAIL src/b.spec.ts > b > fails\n"
    )
    assert _failed_paths(log, tests_only=True) == [
        "tests/unit/test_a.py",
        "src/b.spec.ts",
    ]


@pytest.mark.parametrize(
    "test_failure",
    ["", "tests/integration/test_app_sign_in.py", "tests/unit/test_a.py"],
)
async def test_a_static_failure_in_a_test_file_is_not_bisected_by_running_tests(
    test_failure,
):
    reads = _reads_failed_log(
        "2026-10-19T02:53:15.7Z   --> tests/integration/test_app_sign_in.py:15:8\n"
        + (
            f"2026-10-19T02:53:16.0Z FAILED {test_failure}::test_x - boom\n"
            if test_failure
            else ""
        )
    )
    batch = Batch(
        head="d",
        total=6,
        complete=True,
        commits=tuple(BatchCommit(str(i), "feat: x") for i in range(6)),
        failed_paths=tuple(await reads.failed_paths("d")),
        blamed="0",
    )
    searches = []

    def sessions():
        searches.append("started")
        raise AssertionError("a test rerun cannot judge Ruff")

    result = await _search_for_culprit(
        SimpleNamespace(session_factory=sessions), reads, "project", batch, memo={}
    )
    assert result.blamed == "0"
    assert searches == []


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
    assert gate_run(red_then_green, GATE).conclusion == "success"


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
        assert gate_run(runs, GATE).conclusion == "failure"


def test_a_neutral_gate_counts_as_a_pass():
    from app.domain.review.landing_forge import gate_run

    # The precedence has to matter: with the neutral run first the old
    # first-match code would return the failure.
    runs = [
        Check("CI required", "completed", "failure", "", suite=1),
        Check("CI required", "completed", "neutral", "", suite=1),
    ]
    assert gate_run(runs, GATE).conclusion == "neutral"


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
    *,
    actions: str | None = "write",
    runs=None,
    steps=(),
    status="completed",
    gate: MainGate | None = None,
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
        gate=PLATFORM_GATE_NAMES if gate is None else gate,
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


def test_a_run_still_going_is_not_a_verdict():
    """Waiting is not judging: a search that read this as ``unknown`` would
    stop at its first probe, before the tests had run at all."""
    assert (
        _verdict([("run the named tests", "success")], status="in_progress")
        == "running"
    )


def test_a_project_names_what_its_repository_calls_the_gate():
    gate = gate_for(
        {
            "main_gate": {
                "check": "gate",
                "green_ref": "green",
                "probe_workflow": "probe.yml",
            }
        },
        repo="someone/else",
    )
    assert (gate.check, gate.green_ref, gate.probe_workflow) == (
        "gate",
        "green",
        "probe.yml",
    )


def test_only_the_platforms_own_repository_gets_the_platform_names():
    """Every other repository's CI is unknown to the platform: no gate name,
    no green mark, no probe workflow — so no probe is ever dispatched there."""
    assert gate_for({}, repo=platform.platform_repository) == PLATFORM_GATE_NAMES
    assert gate_for(None, repo="someone/else") == MainGate()


def test_half_a_gate_is_not_a_gate():
    """An empty name would read a check nobody chose; absent is the safe read."""
    assert gate_for({"main_gate": {"check": "   "}}, repo="someone/else") == MainGate()


def test_a_repository_with_no_such_check_has_no_batch_gate():
    """A project that never configured one: the batch reads find no gate, and
    every caller falls back to judging each merge on its own. The nameless run
    is the one that would be mistaken for a gate, so it is what the case uses."""
    assert gate_run([Check("", "completed", "failure", "", suite=1)]) is None
    assert (
        gate_run([Check("some other check", "completed", "failure", "", suite=1)])
        is None
    )


def test_a_repository_without_a_probe_workflow_dispatches_nothing():
    """Only a repository that has the workflow can be probed; anywhere else the
    batch is attributed the way it was before the search existed."""
    reads, posts = _probe_reads(
        runs=[{"id": 7, "name": "culprit-bisect", "display_title": "culprit n"}],
        gate=MainGate(),
    )
    assert asyncio.run(reads.dispatch_bisect("a" * 40, ["t.py"], "n")) is None
    assert posts == []
