import json
import time

import httpx
import pytest

from app.core import forge_quota, metrics
from app.domain.review.github_pr import GitHubPrError, HttpxGitHubPrClient
from app.domain.review.github_pr.protocol import (
    BranchComparison,
    BranchComparisonFailure,
    BranchComparisonsDeferred,
)


@pytest.fixture(autouse=True)
def isolated_quota(monkeypatch):
    monkeypatch.setattr(forge_quota, "_owners", {})
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    monkeypatch.setattr(metrics, "registry", metrics.MetricsRegistry())
    forge_quota.own("token", 7291, time.time() + 3600)


def compared(head="task-head", ahead=0):
    return {"compare": {"baseTarget": {"oid": head}, "behindBy": ahead, "aheadBy": 99}}


async def read(handler, pairs):
    client = HttpxGitHubPrClient(transport=httpx.MockTransport(handler))
    return await client.branch_comparisons(
        owner="o", repo="r", token="token", pairs=pairs
    )


@pytest.mark.anyio
async def test_missing_heads_use_bounded_graphql_without_branch_rest_probes():
    sent = []

    def handler(request):
        sent.append(request)
        assert request.method == "POST" and request.url.path == "/graphql"
        variables = json.loads(request.content)["variables"]
        aliases = {f"r{key[6:]}": None for key in variables if key.startswith("branch")}
        return httpx.Response(200, json={"data": {"repository": aliases}})

    pairs = [(f"task/{index}", "main") for index in range(51)]
    result = await read(handler, pairs)
    assert len(sent) == 2
    assert result == dict.fromkeys(pairs)
    rows = list(metrics.registry.export()["counters"].values())
    assert (
        sum(r["value"] for r in rows if r["name"] == "forge_responses_graphql_total")
        == 2
    )
    assert not any(r["name"] == "forge_responses_core_rest_non_304_total" for r in rows)


@pytest.mark.anyio
async def test_empty_candidates_make_no_http_request():
    def handler(request):
        pytest.fail(f"unexpected request: {request.url}")

    assert await read(handler, []) == {}


@pytest.mark.anyio
async def test_comparison_direction_head_identity_and_variable_values():
    pair = ('task/a"quoted', "a" * 40)
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        assert body["variables"]["branch0"] == "refs/heads/" + pair[0]
        assert body["variables"]["base0"] == pair[1]
        assert pair[0] not in body["query"]
        # target is deliberately not the comparison's baseTarget.
        value = {
            "target": {"oid": "different-snapshot"},
            **compared("compared-head", 3),
        }
        return httpx.Response(200, json={"data": {"repository": {"r0": value}}})

    assert await read(handler, [pair, pair]) == {
        pair: BranchComparison("compared-head", 3)
    }
    assert len(sent) == 1 and "branch1" not in sent[0]["variables"]


@pytest.mark.anyio
@pytest.mark.parametrize("ahead", [0, 1, 9])
async def test_zero_and_independent_commits_are_not_confused(ahead):
    def handler(request):
        return httpx.Response(
            200, json={"data": {"repository": {"r0": compared(ahead=ahead)}}}
        )

    assert (await read(handler, [("task/a", "main")]))[
        ("task/a", "main")
    ] == BranchComparison("task-head", ahead)


@pytest.mark.anyio
async def test_a_bad_base_does_not_block_a_valid_sibling():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "type": "NOT_FOUND",
                        "path": ["repository", "r0", "compare"],
                        "message": "base unavailable",
                    }
                ],
                "data": {
                    "repository": {"r0": {"compare": None}, "r1": compared("good", 2)}
                },
            },
        )

    result = await read(handler, [("bad", "gone-base"), ("good", "main")])
    assert result[("bad", "gone-base")] == BranchComparisonFailure("base unavailable")
    assert result[("good", "main")] == BranchComparison("good", 2)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "value", [{}, {"compare": None}, compared(ahead=True), {"compare": {"behindBy": 0}}]
)
async def test_unusable_comparisons_are_failures_not_missing_heads(value):
    def handler(request):
        return httpx.Response(200, json={"data": {"repository": {"r0": value}}})

    result = await read(handler, [("task/a", "main")])
    assert isinstance(result[("task/a", "main")], BranchComparisonFailure)


@pytest.mark.anyio
async def test_an_omitted_alias_is_not_a_missing_head():
    def handler(request):
        return httpx.Response(200, json={"data": {"repository": {}}})

    assert isinstance(
        (await read(handler, [("a", "main")]))[("a", "main")], BranchComparisonFailure
    )


@pytest.mark.anyio
async def test_missing_repository_is_a_request_failure():
    def handler(request):
        return httpx.Response(200, json={"data": {"repository": None}})

    with pytest.raises(GitHubPrError):
        await read(handler, [("a", "main")])


@pytest.mark.anyio
async def test_graphql_quota_refusal_stops_later_batches_and_keeps_core_reading():
    sent = []
    reset = time.time() + 60
    forge_quota.record(7291, 4000, 5000, reset)

    def handler(request):
        sent.append(request)
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-resource": "graphql",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-limit": "5000",
                "x-ratelimit-reset": str(int(reset)),
            },
            json={"errors": [{"type": "RATE_LIMITED", "message": "quota spent"}]},
        )

    with pytest.raises(BranchComparisonsDeferred):
        await read(handler, [(f"task/{n}", "main") for n in range(51)])
    assert len(sent) == 1
    assert forge_quota.reading(7291).remaining == 4000
    assert forge_quota.refused_until(7291) is not None


@pytest.mark.anyio
@pytest.mark.parametrize("status", [403, 429])
async def test_http_refusal_does_not_fall_back_to_individual_reads(status):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(status, json={"message": "refused"})

    with pytest.raises(BranchComparisonsDeferred):
        await read(handler, [(f"task/{n}", "main") for n in range(51)])
    assert len(sent) == 1 and sent[0].url.path == "/graphql"
