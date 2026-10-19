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
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.domain.review.landing_forge import (
    Check,
    ForgeUnavailable,
    GitHubLandingReads,
    _failed_paths,
    named_runs,
)
from app.domain.review.landing_watch import Batch, BatchCommit, _search_for_culprit

#: The check a repository runs on its branch in batches.
GATE = "rocket-build"
GATES = frozenset({GATE})

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


# --- a branch checked in batches (#4249) ------------------------------------


def _reads_with(handle):
    return GitHubLandingReads(
        None,
        _Tokens(),
        "https://api.github.test",
        "o",
        "r",
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(200, json={"statuses": []})
                if request.url.path.endswith("/status")
                else handle(request)
            )
        ),
        branch="main",
    )


def _gate_run(status: str, conclusion: str | None, name: str = GATE):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": "",
    }


def _later(runs_by_sha):
    """The branch after "a": the commits of ``runs_by_sha``, oldest first."""

    def handle(request):
        path = request.url.path.removeprefix("/repos/o/r")
        if path.startswith("/compare/"):
            return httpx.Response(
                200,
                json={
                    "status": "ahead",
                    "commits": [{"sha": sha} for sha in runs_by_sha],
                },
            )
        if path.endswith("/check-runs"):
            return httpx.Response(
                200, json={"check_runs": runs_by_sha[path.split("/")[2]]}
            )
        return httpx.Response(404)

    return _reads_with(handle)


def test_the_covering_run_is_the_first_after_the_commit_that_was_not_cancelled():
    reads = _later(
        {
            "b": [_gate_run("completed", "cancelled")],
            "c": [_gate_run("completed", "failure")],
            "d": [_gate_run("completed", "success")],
        }
    )
    assert asyncio.run(reads.run_after("a", GATES))[0] == "c"


def test_a_covering_run_still_going_is_waited_for():
    reads = _later(
        {
            "b": [_gate_run("queued", None)],
            "c": [_gate_run("completed", "success")],
        }
    )
    assert asyncio.run(reads.run_after("a", GATES)) is None


def test_a_later_green_run_is_found_past_a_newer_red_one():
    reads = _later(
        {
            "b": [_gate_run("completed", "success")],
            "c": [_gate_run("completed", "failure")],
            "d": [_gate_run("in_progress", None)],
        }
    )
    assert asyncio.run(reads.run_after("a", GATES, passed=True))[0] == "b"


def test_a_branch_whose_pushes_run_no_such_check_has_no_later_run():
    reads = _later({"b": [_gate_run("completed", "success", name="lint")]})
    assert asyncio.run(reads.run_after("a", GATES)) is None
    assert asyncio.run(reads.run_after("a", GATES, passed=True)) is None


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
            "2026-10-19T02:53:25.1Z # command-line-arguments\n"
            "2026-10-19T02:53:25.2Z ./go-diagnostic.go:5:28: fmt.Printf format %d "
            'has arg "not a number" of wrong type string\n',
            ["./go-diagnostic.go"],
            id="go-vet",
        ),
        pytest.param(
            "2026-10-19T02:53:25.1Z   internal/device/device.go\n"
            "2026-10-19T02:53:25.2Z ##[error]gofmt -w the files above\n",
            ["internal/device/device.go"],
            id="gofmt",
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


def test_a_rerun_that_passed_wins_over_the_red_it_replaced():

    red_then_green = [
        Check(GATE, "completed", "failure", ""),
        Check(GATE, "completed", "success", ""),
    ]
    assert named_runs(red_then_green, GATES).conclusion == "success"


def test_a_red_gate_beats_a_cancelled_one_whatever_the_order():

    for runs in (
        [
            Check(GATE, "completed", "cancelled", ""),
            Check(GATE, "completed", "failure", ""),
        ],
        [
            Check(GATE, "completed", "failure", ""),
            Check(GATE, "completed", "cancelled", ""),
        ],
    ):
        assert named_runs(runs, GATES).conclusion == "failure"


def test_a_neutral_gate_counts_as_a_pass():

    # The precedence has to matter: with the neutral run first the old
    # first-match code would return the failure.
    runs = [
        Check(GATE, "completed", "failure", ""),
        Check(GATE, "completed", "neutral", ""),
    ]
    assert named_runs(runs, GATES).conclusion == "neutral"
