"""Reading CI activity out of GitHub: nothing past the 1000-result cap is lost
silently, a quota refusal is named as one, and queue events keep GitHub's
reason and actor.

GitHub serves at most 1000 runs for one filtered listing. A day on this
repository has had about 2000, so a reader that took one listing per range at
face value would drop half a busy day and the page would read it as a quiet one.
"""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

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


async def test_a_range_matching_more_runs_than_github_serves_is_read_in_halves():
    # 1200 runs spread over eight hours; GitHub reports the full count but would
    # serve only 1000 of them for the whole range.
    runs = [
        {"id": n, "created_at": SINCE + timedelta(seconds=24 * n)} for n in range(1200)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        a, b = _created(request)
        page = int(parse_qs(urlparse(str(request.url)).query)["page"][0])
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

    listed = await _reader(handler).list_runs(
        owner="o", repo="r", token="t", since=SINCE, until=UNTIL
    )

    assert sorted(r["id"] for r in listed.items) == list(range(1200))
    assert not listed.truncated


async def test_a_quota_refusal_is_raised_as_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"},
            json={"message": "API rate limit exceeded"},
        )

    with pytest.raises(CiQuotaRefused):
        await _reader(handler).run_jobs(owner="o", repo="r", token="t", run_id=1)


async def test_a_permission_refusal_is_not_read_as_quota():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Resource not accessible"})

    with pytest.raises(CiGitHubError) as raised:
        await _reader(handler).run_jobs(owner="o", repo="r", token="t", run_id=1)

    assert not isinstance(raised.value, CiQuotaRefused)
    assert "not accessible" in str(raised.value)


async def test_queue_events_keep_githubs_reason_and_actor():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["variables"]["q"].startswith(
            "repo:o/r is:pr updated:"
        )
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
                                "timelineItems": {
                                    "totalCount": 2,
                                    "nodes": [
                                        {
                                            "__typename": "AddedToMergeQueueEvent",
                                            "createdAt": "2026-10-17T01:00:00Z",
                                            "actor": {"login": "cheesex-app"},
                                        },
                                        {
                                            "__typename": "RemovedFromMergeQueueEvent",
                                            "createdAt": "2026-10-17T01:20:00Z",
                                            "reason": "failed_checks",
                                            "actor": {"login": "github-merge-queue"},
                                        },
                                    ],
                                },
                            }
                        ],
                    }
                }
            },
        )

    events, _ = await _reader(handler).queue_events(
        owner="o", repo="r", token="t", since=SINCE, until=UNTIL
    )

    assert [
        (e.pr_number, e.kind, e.reason, e.actor, e.head_branch) for e in events
    ] == [
        (4186, "added", None, "cheesex-app", "feat/ci-page"),
        (4186, "removed", "failed_checks", "github-merge-queue", "feat/ci-page"),
    ]
