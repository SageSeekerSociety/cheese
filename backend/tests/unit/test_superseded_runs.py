"""被顶替的 workflow 运行（`superseded_runs`，PR #3463 的误报）。

GitHub 的合并框看的是「这个提交上、这个 workflow 最新那次运行」的结论；旧运行
留下的 check run 仍旧挂在同一个 sha 上（`concurrency: cancel-in-progress` 把草稿
档那次取消了）。平台一次读回全部 check run，就把被顶替的那些也算进了「检查未通过」。

这些测试钉住三件事：认「谁顶替了谁」只靠 check run 的 URL 加一次 Actions 运行列表
的读取；读不到那份列表就原样返回（宁可多一条假红，不少一条真红）；全绿和还在跑的
稳态一条多余请求都不发 —— PR 每 60 秒轮询一次。
"""

import httpx
import pytest

from app.domain.review.github_pr import GitHubPRClient, HttpxGitHubPrClient
from app.domain.review.superseded_runs import (
    actions_job_id,
    actions_run_id,
    drop_superseded,
    needs_resolution,
    superseded_runs,
)

_API_BASE = "https://api.github.com"
_HEADERS = {"Authorization": "Bearer t"}
_REF = "deadbeef"


def _check_run(
    name: str,
    *,
    run: int,
    job: int = 1,
    conclusion: str | None = "success",
    status: str = "completed",
) -> dict:
    """A real-shaped Actions check-run: the only workflow hint is the job URL."""
    url = f"https://github.com/acme/widgets/actions/runs/{run}/job/{job}"
    return {
        "id": job,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": url,
        "details_url": url,
    }


def _workflow_run(
    run: int,
    workflow: int,
    *,
    number: int = 1,
    attempt: int = 1,
    created: str = "2026-10-12T07:00:00Z",
) -> dict:
    return {
        "id": run,
        "workflow_id": workflow,
        "run_number": number,
        "run_attempt": attempt,
        "created_at": created,
    }


class _Server:
    """A mock GitHub serving check-runs + the Actions run list."""

    def __init__(self, *, check_runs: list[dict], workflow_runs: list[dict]) -> None:
        self.check_runs = check_runs
        self.workflow_runs = workflow_runs
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/check-runs"):
            return httpx.Response(
                200, json={"check_runs": self.check_runs, "total_count": 1}
            )
        if path.endswith("/actions/runs"):
            return httpx.Response(200, json={"workflow_runs": self.workflow_runs})
        return httpx.Response(404, json={"message": "not found"})

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


async def _drop(runs: list[dict], server) -> list[dict]:
    return await drop_superseded(
        runs, _API_BASE, "acme", "widgets", _REF, _HEADERS, httpx.MockTransport(server)
    )


# ---- 认 URL：check run 只在这一处说出自己属于哪次运行 ------------------------


def test_run_and_job_ids_are_read_out_of_the_actions_url():
    run = _check_run("shell-tests", run=31470222386, job=93711698702)

    assert actions_run_id(run) == 31470222386
    assert actions_job_id(run) == 93711698702


def test_a_non_actions_check_run_has_no_run_and_no_job():
    """Codecov and friends publish check-runs too; their ids are not job ids."""
    run = _check_run("codecov/patch", run=0)
    run["html_url"] = "https://app.codecov.io/gh/acme/widgets/pull/7"
    run["details_url"] = run["html_url"]

    assert actions_run_id(run) is None
    assert actions_job_id(run) is None


def test_details_url_is_the_fallback_when_html_url_says_nothing():
    run = _check_run("shell-tests", run=4242, job=7)
    run["html_url"] = "https://github.com/acme/widgets/runs/1"

    assert actions_run_id(run) == 4242


# ---- 什么时候值得多读一次运行列表 -------------------------------------------


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([_check_run("a", run=1)], False),
        ([_check_run("a", run=1, conclusion="neutral")], False),
        ([_check_run("a", run=1, conclusion="skipped")], False),
        ([_check_run("a", run=1, status="in_progress", conclusion=None)], False),
        ([_check_run("a", run=1, conclusion="cancelled")], True),
        ([_check_run("a", run=1, conclusion="failure")], True),
        ([_check_run("a", run=1, conclusion="stale")], True),
        ([], False),
    ],
)
def test_needs_resolution_only_for_finished_non_green_runs(runs, expected):
    """被顶替的运行一定是被取消的；还在跑的、已经绿的不可能是旧运行。"""
    assert needs_resolution(runs) is expected


def test_needs_resolution_shrugs_off_a_malformed_entry():
    assert needs_resolution([None, "nope", _check_run("a", run=1)]) is False


def test_a_red_check_that_names_no_actions_run_does_not_trigger_a_read():
    """别的应用发的 check run（codecov 之流）不属于任何 workflow，没有谁能顶替
    它 —— 只用这类检查的仓库不该为这条规矩多花一次调用。"""
    run = _check_run("codecov/patch", run=1, conclusion="failure")
    run["html_url"] = "https://app.codecov.io/gh/acme/widgets/pull/7"
    run["details_url"] = run["html_url"]

    assert needs_resolution([run]) is False


# ---- 谁顶替了谁 -------------------------------------------------------------


def test_the_highest_run_number_of_a_workflow_supersedes_the_older():
    listed = [
        _workflow_run(100, workflow=1, number=1),
        _workflow_run(200, workflow=1, number=2),
        _workflow_run(300, workflow=2, number=1),
    ]

    assert superseded_runs(listed, {100, 200, 300}) == {100}


def test_run_attempt_breaks_a_tie_before_created_at():
    # A higher attempt wins even though it was created earlier: attempt is the
    # re-run counter, created_at only ever breaks a same-attempt tie.
    listed = [
        _workflow_run(100, workflow=1, number=5, attempt=1, created="2026-10-12T09:00"),
        _workflow_run(200, workflow=1, number=5, attempt=2, created="2026-10-12T07:00"),
    ]

    assert superseded_runs(listed, {100, 200}) == {100}


def test_a_run_the_list_does_not_know_is_left_alone():
    """认不出来就不动它 —— 宁可照旧报红，也不凭一次读不到把红抹掉。"""
    listed = [_workflow_run(200, workflow=1, number=2)]

    assert superseded_runs(listed, {200, 999}) == set()


def test_a_run_without_a_workflow_id_is_not_ranked():
    listed = [{"id": 100, "workflow_id": None, "run_number": 1}]

    assert superseded_runs(listed, {100}) == set()


# ---- drop_superseded：读、判、删 --------------------------------------------


@pytest.mark.anyio
async def test_a_superseded_cancelled_run_is_dropped_from_the_check_runs():
    cancelled = _check_run("test", run=100, conclusion="cancelled")
    green = _check_run("test", run=200)
    server = _Server(
        check_runs=[cancelled, green],
        workflow_runs=[
            _workflow_run(100, workflow=1, number=1),
            _workflow_run(200, workflow=1, number=2),
        ],
    )

    kept = await _drop([cancelled, green], server)

    assert [r["html_url"] for r in kept] == [green["html_url"]]
    # The run list is read for this exact commit, paginated like everything else.
    asked = server.urls()
    assert any(
        "/repos/acme/widgets/actions/runs" in url and f"head_sha={_REF}" in url
        for url in asked
    )


@pytest.mark.anyio
async def test_green_and_pending_runs_never_ask_for_the_run_list():
    runs = [
        _check_run("lint", run=100),
        _check_run("e2e", run=200, status="in_progress", conclusion=None),
    ]
    server = _Server(check_runs=runs, workflow_runs=[])

    assert await _drop(runs, server) == runs
    assert server.requests == []


@pytest.mark.anyio
async def test_an_unreadable_run_list_leaves_the_check_runs_alone():
    """`actions: read` could be refused, or GitHub could just be down. A missing
    red is worse than a stale one, so a failed read changes nothing."""
    cancelled = _check_run("test", run=100, conclusion="cancelled")

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Resource not accessible"})

    kept = await drop_superseded(
        [cancelled],
        _API_BASE,
        "acme",
        "widgets",
        _REF,
        _HEADERS,
        httpx.MockTransport(forbidden),
    )

    assert kept == [cancelled]


@pytest.mark.anyio
async def test_a_superseded_run_on_a_later_page_is_still_dropped():
    """A workflow that ran many times in one day can push the first page's
    worth of rows past the run we care about; the read has to keep paging."""
    cancelled = _check_run("test", run=900, conclusion="cancelled")
    filler = [_workflow_run(i, workflow=99, number=i) for i in range(1, 101)]
    pages = {
        1: filler,
        2: [
            _workflow_run(900, workflow=1, number=1),
            _workflow_run(901, workflow=1, number=2),
        ],
    }
    seen_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": [cancelled]})
        page = int(dict(request.url.params)["page"])
        seen_pages.append(page)
        return httpx.Response(200, json={"workflow_runs": pages.get(page, [])})

    kept = await drop_superseded(
        [cancelled],
        _API_BASE,
        "acme",
        "widgets",
        _REF,
        _HEADERS,
        httpx.MockTransport(handler),
    )

    assert seen_pages == [1, 2]
    assert kept == []


# ---- 端到端：同一份读法喂给判定和卡面 ---------------------------------------


@pytest.mark.anyio
async def test_a_superseded_cancelled_pair_lets_a_clean_pr_read_green():
    """PR #3463: the draft run's cancelled check-runs are all that stood between
    a clean PR and a green card."""
    server = _Server(
        check_runs=[
            _check_run("CI required", run=100, conclusion="cancelled"),
            _check_run("CI required", run=200),
        ],
        workflow_runs=[
            _workflow_run(100, workflow=1, number=1),
            _workflow_run(200, workflow=1, number=2),
        ],
    )
    state, tail = await HttpxGitHubPrClient(
        transport=httpx.MockTransport(server)
    ).check_state(owner="acme", repo="widgets", ref=_REF, token="t")

    assert (state, tail) == ("success", "全部 1 项检查通过")


@pytest.mark.anyio
async def test_a_genuinely_cancelled_check_is_named_but_never_called_a_pass():
    """最新的那次运行自己取消了 —— 那不是「没通过」，也不能说成通过。"""
    server = _Server(
        check_runs=[_check_run("shell-tests", run=200, conclusion="cancelled")],
        workflow_runs=[_workflow_run(200, workflow=1, number=2)],
    )
    state, tail = await HttpxGitHubPrClient(
        transport=httpx.MockTransport(server)
    ).check_state(owner="acme", repo="widgets", ref=_REF, token="t")

    assert state == "pending"
    assert tail == "已取消：shell-tests: cancelled"


@pytest.mark.anyio
async def test_the_display_path_hands_the_card_the_same_runs():
    """`GitHubPRClient.check_runs` feeds the card's counters — 被顶替的那些一个
    都不该出现在这里。"""
    server = _Server(
        check_runs=[
            _check_run("CI required", run=100, conclusion="cancelled"),
            _check_run("CI required", run=200),
        ],
        workflow_runs=[
            _workflow_run(100, workflow=1, number=1),
            _workflow_run(200, workflow=1, number=2),
        ],
    )
    client = GitHubPRClient(
        "acme", "widgets", _ReadOnlyTokens(), transport=httpx.MockTransport(server)
    )

    runs = await client.check_runs(_REF)

    assert [(r["name"], r["conclusion"]) for r in runs] == [("CI required", "success")]


class _ReadOnlyTokens:
    async def installation_token(self) -> tuple[str, str]:
        return "ghs_read", "2099-01-01T00:00:00+00:00"
