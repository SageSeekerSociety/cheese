"""CI reader coverage, endpoint accounting and reporting's quota priority."""

import json
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core import forge_etags, forge_quota
from app.domain.ci_activity.github import CiGitHubError, CiQuotaRefused, GitHubCi

SINCE = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
UNTIL = SINCE + timedelta(hours=8)


def _reader(handler) -> GitHubCi:
    return GitHubCi(
        api_base="https://api.invalid", transport=httpx.MockTransport(handler)
    )


def _created(request: httpx.Request) -> tuple[datetime, datetime]:
    raw = parse_qs(urlparse(str(request.url)).query)["created"][0]
    a, b = raw.split("..")
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return parse(a), parse(b)


def _runs_handler(runs):
    def handle(request):
        a, b = _created(request)
        page = int(request.url.params["page"])
        matched = [r for r in runs if a <= r["created_at"] <= b]
        served = matched[:1000][(page - 1) * 100 : page * 100]
        return httpx.Response(
            200,
            json={
                "total_count": len(matched),
                "workflow_runs": [
                    {"id": r["id"], "created_at": r["created_at"].isoformat()}
                    for r in served
                ],
            },
        )

    return handle


async def test_a_range_matching_more_runs_than_github_serves_is_read_in_halves():
    runs = [
        {"id": n, "created_at": SINCE + timedelta(seconds=24 * n)} for n in range(1200)
    ]
    listed = await _reader(_runs_handler(runs)).list_runs(
        owner="o", repo="r", token="t", since=SINCE, until=UNTIL
    )
    assert sorted(r["id"] for r in listed.items) == list(range(1200))
    assert not listed.truncated


async def test_a_busy_five_minute_window_is_split_at_second_precision():
    runs = [
        {"id": n, "created_at": SINCE + timedelta(seconds=n % 300)} for n in range(1001)
    ]
    listed = await _reader(_runs_handler(runs)).list_runs(
        owner="o", repo="r", token="t", since=SINCE, until=SINCE + timedelta(minutes=5)
    )
    assert sorted(r["id"] for r in listed.items) == list(range(1001))
    assert not listed.truncated


async def test_exactly_one_thousand_runs_are_not_falsely_truncated():
    runs = [{"id": n, "created_at": SINCE} for n in range(1000)]
    listed = await _reader(_runs_handler(runs)).list_runs(
        owner="o", repo="r", token="t", since=SINCE, until=UNTIL
    )
    assert len(listed.items) == 1000
    assert listed.requests == 10
    assert not listed.truncated


async def test_fractional_bounds_do_not_drop_the_last_whole_second():
    listed = await _reader(
        _runs_handler([{"id": 1, "created_at": SINCE + timedelta(seconds=1)}])
    ).list_runs(
        owner="o",
        repo="r",
        token="t",
        since=SINCE + timedelta(microseconds=500000),
        until=SINCE + timedelta(seconds=1, microseconds=500000),
    )
    assert [r["id"] for r in listed.items] == [1]


async def test_an_unsplittable_second_is_explicitly_incomplete():
    runs = [{"id": n, "created_at": SINCE} for n in range(1001)]
    listed = await _reader(_runs_handler(runs)).list_runs(
        owner="o", repo="r", token="t", since=SINCE, until=SINCE + timedelta(seconds=1)
    )
    assert listed.truncated
    assert listed.requests == 1


async def test_a_quota_refusal_is_raised_as_one():
    def handler(request):
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"},
            json={"message": "API rate limit exceeded"},
        )

    with pytest.raises(CiQuotaRefused):
        await _reader(handler).run_jobs(owner="o", repo="r", token="t", run_id=1)


async def test_a_permission_refusal_is_not_read_as_quota():
    def handler(request):
        return httpx.Response(403, json={"message": "Resource not accessible"})

    with pytest.raises(CiGitHubError) as raised:
        await _reader(handler).run_jobs(owner="o", repo="r", token="t", run_id=1)
    assert not isinstance(raised.value, CiQuotaRefused)
    assert "not accessible" in str(raised.value)


async def test_merged_pr_facts_are_read_without_any_queue_timeline():
    def handler(request):
        body = json.loads(request.content)
        assert body["variables"]["q"].startswith("repo:o/r is:pr is:merged updated:")
        assert "timelineItems" not in body["query"]
        assert "MERGE_QUEUE" not in body["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "search": {
                        "issueCount": 1,
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "number": 4186,
                                "headRefName": "feat/ci-page",
                                "createdAt": SINCE.isoformat(),
                                "mergedAt": (SINCE + timedelta(hours=1)).isoformat(),
                            }
                        ],
                    }
                }
            },
        )

    listed = await _reader(handler).merged_pull_requests(
        owner="o", repo="r", token="t", since=SINCE, until=UNTIL
    )
    assert listed.items[0]["number"] == 4186
    assert listed.items[0]["mergedAt"]
    assert not listed.truncated


async def test_a_runs_jobs_are_read_for_every_attempt_and_every_page():
    jobs = [{"id": n, "run_attempt": 1 + n % 2} for n in range(130)]
    asked = []

    def handler(request):
        query = parse_qs(urlparse(str(request.url)).query)
        asked.append(query)
        page = int(query["page"][0])
        return httpx.Response(
            200,
            headers={"etag": f'"jobs-{page}"'},
            json={"total_count": 130, "jobs": jobs[(page - 1) * 100 : page * 100]},
        )

    listed = await _reader(handler).run_jobs(owner="o", repo="r", token="t", run_id=1)
    assert len(listed.items) == 130
    assert all(query["filter"] == ["all"] for query in asked)


async def test_a_graphql_quota_refusal_is_raised_and_counted():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "errors": [
                    {"type": "RATE_LIMITED", "message": "API rate limit exceeded"}
                ]
            },
        )

    reader = _reader(handler)
    with pytest.raises(CiQuotaRefused):
        await reader.merged_pull_requests(
            owner="o", repo="r", token="t", since=SINCE, until=UNTIL
        )
    assert reader.request_counts["graphql_attempts"] == 1
    assert reader.request_counts["rest_attempts"] == 0
    assert reader.request_counts["unanswered"] == 0


async def test_a_runs_jobs_do_not_take_room_in_the_shared_answer_cache(monkeypatch):
    monkeypatch.setattr(forge_etags, "_kept", OrderedDict())
    forge_quota.own("minted-token", 4242, time.time() + 3600)

    def handler(request):
        return httpx.Response(
            200,
            headers={"etag": '"jobs"'},
            json={"total_count": 1, "jobs": [{"id": 1}]},
        )

    await _reader(handler).run_jobs(owner="o", repo="r", token="minted-token", run_id=1)
    assert len(forge_etags._kept) == 0


@pytest.fixture
def owned_token(monkeypatch):
    monkeypatch.setattr(forge_quota, "_owners", {})
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    monkeypatch.setattr(forge_etags, "_kept", OrderedDict())
    forge_quota.own("owned", 4242, time.time() + 3600)
    return "owned"


@pytest.mark.parametrize("remaining", [2500, 1750, 1000])
async def test_reporting_yields_at_half_quota_before_data_reads(owned_token, remaining):
    forge_quota.record(4242, remaining, 5000, time.time() + 3600)
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200, json={"workflow_runs": [], "total_count": 0})

    with pytest.raises(CiGitHubError, match="yielded"):
        await _reader(handler).list_runs(
            owner="o", repo="r", token=owned_token, since=SINCE, until=UNTIL
        )
    assert called == []


async def test_a_low_reading_on_one_page_stops_the_next_page(owned_token):
    asked = []

    def handler(request):
        asked.append(request)
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-remaining": "1750",
                "x-ratelimit-limit": "5000",
                "x-ratelimit-reset": str(int(time.time() + 3600)),
            },
            json={"total_count": 150, "jobs": [{"id": n} for n in range(100)]},
        )

    reader = _reader(handler)
    with pytest.raises(CiGitHubError, match="yielded"):
        await reader.run_jobs(owner="o", repo="r", token=owned_token, run_id=1)
    assert len(asked) == 1
    assert reader.request_counts["rest_attempts"] == 1


async def test_a_not_found_data_request_is_in_the_endpoint_counts():
    reader = _reader(lambda request: httpx.Response(404, json={"message": "gone"}))
    with pytest.raises(CiGitHubError):
        await reader.run_jobs(owner="o", repo="r", token="t", run_id=1)
    counts = reader.request_counts
    assert counts["rest_attempts"] == counts["rest_non_304_responses"] == 1
    assert counts["by_endpoint"] == {"GET /actions/runs/{id}/jobs": 1}


async def test_an_unanswered_request_is_counted_separately():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    reader = _reader(handler)
    with pytest.raises(httpx.ConnectError):
        await reader.list_runs(owner="o", repo="r", token="t", since=SINCE, until=UNTIL)
    assert reader.request_counts["rest_attempts"] == 1
    assert reader.request_counts["unanswered"] == 1
    assert reader.request_counts["rest_non_304_responses"] == 0


async def test_original_304_is_counted_before_cached_body_is_restored(owned_token):
    def handler(request):
        if request.headers.get("if-none-match") == '"runs"':
            return httpx.Response(304, headers={"etag": '"runs"'})
        return httpx.Response(
            200,
            headers={"etag": '"runs"'},
            json={"total_count": 0, "workflow_runs": []},
        )

    reader = _reader(handler)
    for _ in range(2):
        result = await reader.list_runs(
            owner="o", repo="r", token=owned_token, since=SINCE, until=UNTIL
        )
        assert result.items == []
    counts = reader.request_counts
    assert counts["rest_attempts"] == 2
    assert counts["rest_non_304_responses"] == counts["not_modified"] == 1
