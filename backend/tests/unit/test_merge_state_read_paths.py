"""`mergeable_state` 走通两条读路径（#718）。

`compute_merge_state` 的第一个输入来自 REST PR 响应的 `mergeable_state`，
两个 client 形态都得把它带出来：`HttpxGitHubPrClient.pull_request_status`
（轮询用）和 `GitHubPRClient.pr_status`（per-repo，pr_publish / /pr-checks
用）。全 fake payload，真解析逻辑——透过 MockTransport 驱动，不 monkeypatch
解析本身。
"""

from typing import cast

import httpx
import pytest

from app.domain.project.github_app import GitHubAppTokens
from app.domain.review.github_pr import GitHubPRClient, HttpxGitHubPrClient
from app.domain.review.github_pr_status import merge_actor, parse_pull_request_status


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _payload(**extra: object) -> dict:
    base: dict = {
        "state": "open",
        "merged": False,
        "merged_at": None,
        "merge_commit_sha": None,
        "head": {"sha": "deadbeef", "ref": "topic/abc"},
        "base": {"ref": "task/parent"},
        "mergeable": True,
    }
    base.update(extra)
    return base


# ---- 共享的 parser 本身 ------------------------------------------------------


def test_parser_keeps_the_raw_word_lowercased():
    status = parse_pull_request_status(_payload(mergeable_state="UNSTABLE"))

    assert status.mergeable_state == "unstable"


def test_parser_reports_absence_as_none_not_unknown():
    """payload 没带（fake/旧 payload）是 None；"unknown" 是 GitHub 真发的词
    （还没算完）。两者不能塌成一个——unknown 会收敛，None 是取数问题。"""
    absent = parse_pull_request_status(_payload())
    undecided = parse_pull_request_status(_payload(mergeable_state="unknown"))

    assert absent.mergeable_state is None
    assert undecided.mergeable_state == "unknown"


def test_parser_ignores_a_non_string_mergeable_state():
    status = parse_pull_request_status(_payload(mergeable_state=7))

    assert status.mergeable_state is None


def test_parser_reads_who_merged():
    status = parse_pull_request_status(
        _payload(merged=True, merged_by={"id": 4242, "login": "octocat"})
    )

    assert (status.merged_by_github_id, status.merged_by_login) == ("4242", "octocat")


def test_parser_keeps_a_merge_with_no_named_merger():
    status = parse_pull_request_status(_payload(merged=True))

    assert status.merged_by_github_id is None
    assert status.merged_by_login is None


def test_parser_ignores_merged_by_on_an_unmerged_pr():
    """同 `merge_commit_sha`：GitHub 在开着的 PR 上也可能带着这个字段，
    只有 `merged` 为真时它说的才是合并人。"""
    status = parse_pull_request_status(
        _payload(merged=False, merged_by={"id": 4242, "login": "octocat"})
    )

    assert status.merged_by_github_id is None


def test_merge_actor_marks_a_named_nobody():
    """`{}` 与 None 是两件事：问过了、没人，和还没问过。"""
    from app.domain.review.github_pr_status import PullRequestStatus

    status = PullRequestStatus(head_sha="deadbeef", state="closed", merged=True)

    assert merge_actor(status) == {}


# ---- 读路径一：HttpxGitHubPrClient（轮询）-----------------------------------


@pytest.mark.anyio
async def test_poller_lane_surfaces_mergeable_state():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widgets/pulls/7"
        return httpx.Response(200, json=_payload(mergeable_state="behind"))

    client = HttpxGitHubPrClient(transport=httpx.MockTransport(handler))
    status = await client.pull_request_status(
        owner="acme", repo="widgets", number=7, token="t"
    )

    assert status.mergeable_state == "behind"
    assert status.head_sha == "deadbeef"
    assert status.base_ref == "task/parent"


# ---- 读路径二：GitHubPRClient.pr_status（per-repo App 形态）-----------------


class _FakeTokens:
    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"

    async def installation_token(self) -> tuple[str, str]:
        return "ghs_read", "2099-01-01T00:00:00+00:00"


@pytest.mark.anyio
async def test_per_repo_lane_surfaces_mergeable_state():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widgets/pulls/9"
        return httpx.Response(
            200, json=_payload(mergeable_state="dirty", mergeable=False)
        )

    client = GitHubPRClient(
        "acme",
        "widgets",
        cast(GitHubAppTokens, _FakeTokens()),
        transport=httpx.MockTransport(handler),
    )
    status = await client.pr_status(9)

    assert status.mergeable_state == "dirty"
    assert status.mergeable is False
    assert status.head_ref == "topic/abc"
    assert status.base_ref == "task/parent"
