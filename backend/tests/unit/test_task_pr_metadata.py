import json
import time
from typing import cast

import httpx
import pytest

from app.core import forge_quota, metrics
from app.domain.project.github_app import GitHubAppTokens
from app.domain.review.github_pr import (
    GitHubPRClient,
    GitHubPRError,
    GitHubPRRateLimited,
)
from app.domain.review.github_pr.task_pr_metadata import (
    TaskPrMetadata,
    TaskPrMetadataFailure,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolated_quota(monkeypatch):
    monkeypatch.setattr(forge_quota, "_owners", {})
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    monkeypatch.setattr(metrics, "registry", metrics.MetricsRegistry())
    forge_quota.own("token", 7292, time.time() + 3600)


class _Tokens:
    installation_id = 7292

    def __init__(self):
        self.asked = 0

    async def write_token(self):
        self.asked += 1
        return "token", time.time() + 3600


def client(handler, tokens=None):
    return GitHubPRClient(
        "o",
        "r",
        cast(GitHubAppTokens, tokens or _Tokens()),
        transport=httpx.MockTransport(handler),
    )


def metadata(number, state="OPEN", merged=False, head="a" * 40):
    return {"number": number, "state": state, "merged": merged, "headRefOid": head}


async def test_metadata_is_bounded_and_deduplicated_without_per_pr_rest():
    sent = []

    def handle(request):
        sent.append(request)
        assert request.method == "POST" and request.url.path == "/graphql"
        body = json.loads(request.content)
        variables = body["variables"]
        assert variables["owner"] == "o" and variables["repo"] == "r"
        numbers = {k: v for k, v in variables.items() if k.startswith("n")}
        assert len(numbers) <= 10
        assert "headRef{" not in body["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "p" + key[1:]: metadata(value) for key, value in numbers.items()
                    }
                }
            },
        )

    tokens = _Tokens()
    result = await client(handle, tokens).task_pr_metadata([*range(1, 12), 1, 7])
    assert len(sent) == 2
    assert tokens.asked == 1
    assert result == {
        n: TaskPrMetadata(n, "a" * 40, "open", False) for n in range(1, 12)
    }
    counters = metrics.registry.export()["counters"].values()
    assert (
        sum(
            r["value"] for r in counters if r["name"] == "forge_responses_graphql_total"
        )
        == 2
    )
    assert not any(
        r["name"] == "forge_responses_core_rest_non_304_total" for r in counters
    )


async def test_empty_numbers_do_not_mint_a_token_or_send():
    tokens = _Tokens()

    def handle(request):
        pytest.fail("no metadata consumer should mean no request")

    assert await client(handle, tokens).task_pr_metadata([]) == {}
    assert tokens.asked == 0


@pytest.mark.parametrize(
    ("state", "merged", "expected"),
    [("OPEN", False, "open"), ("CLOSED", False, "closed"), ("MERGED", True, "closed")],
)
async def test_state_and_original_head_do_not_depend_on_an_existing_branch(
    state, merged, expected
):
    def handle(request):
        return httpx.Response(
            200,
            json={"data": {"repository": {"p0": metadata(17, state, merged)}}},
        )

    assert await client(handle).task_pr_metadata([17]) == {
        17: TaskPrMetadata(17, "a" * 40, expected, merged)
    }


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        metadata(18),
        metadata(True),
        metadata(17, "UNKNOWN"),
        metadata(17, "MERGED", False),
        metadata(17, "OPEN", True),
        metadata(17, merged=0),
        metadata(17, head=""),
    ],
)
async def test_an_unknown_pr_is_not_closed_or_empty_and_does_not_hide_its_sibling(bad):
    def handle(request):
        return httpx.Response(
            200,
            json={"data": {"repository": {"p0": bad, "p1": metadata(19)}}},
        )

    result = await client(handle).task_pr_metadata([17, 19])
    assert isinstance(result[17], TaskPrMetadataFailure)
    assert result[19] == TaskPrMetadata(19, "a" * 40, "open", False)


async def test_a_partial_error_only_invalidates_its_pr_even_if_it_has_data():
    def handle(request):
        return httpx.Response(
            200,
            json={
                "data": {"repository": {"p0": metadata(17), "p1": metadata(19)}},
                "errors": [
                    {"path": ["repository", "p0", "merged"], "type": "FORBIDDEN"}
                ],
            },
        )

    result = await client(handle).task_pr_metadata([17, 19])
    assert isinstance(result[17], TaskPrMetadataFailure)
    assert result[19] == TaskPrMetadata(19, "a" * 40, "open", False)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"errors": "bad"},
        {"errors": [{"message": "unscoped"}]},
        {"data": {"repository": None}},
    ],
)
async def test_root_errors_do_not_become_missing_prs(payload):
    with pytest.raises(GitHubPRError):
        await client(
            lambda request: httpx.Response(200, json=payload)
        ).task_pr_metadata([17])


async def test_graphql_exhaustion_stops_later_batches_without_overwriting_core():
    forge_quota.record(7292, 4000, 5000, time.time() + 3600)
    sent = []

    def handle(request):
        sent.append(request)
        return httpx.Response(
            200,
            headers={"x-ratelimit-resource": "graphql", "x-ratelimit-remaining": "0"},
            json={"errors": [{"type": "RATE_LIMITED", "message": "wait"}]},
        )

    with pytest.raises(GitHubPRRateLimited):
        await client(handle).task_pr_metadata(list(range(1, 12)))
    assert len(sent) == 1
    assert forge_quota.reading(7292).remaining == 4000


@pytest.mark.parametrize("status", [403, 429])
async def test_quota_refusal_does_not_fall_back_to_individual_rest(status):
    sent = []

    def handle(request):
        sent.append(request)
        return httpx.Response(status, headers={"retry-after": "60"}, json={})

    with pytest.raises(GitHubPRRateLimited):
        await client(handle).task_pr_metadata(list(range(1, 12)))
    assert len(sent) == 1


async def test_a_plain_403_remains_a_permission_error():
    with pytest.raises(GitHubPRError) as failure:
        await client(lambda request: httpx.Response(403, json={})).task_pr_metadata(
            [17]
        )
    assert not isinstance(failure.value, GitHubPRRateLimited)
    assert forge_quota.refused_until(7292) is None


async def test_low_core_quota_defers_before_sending():
    forge_quota.record(7292, 999, 5000, time.time() + 3600)

    def handle(request):
        pytest.fail("core quota must defer before the GraphQL request")

    with pytest.raises(GitHubPRRateLimited):
        await client(handle).task_pr_metadata([17])
