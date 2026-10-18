"""Reading CI activity out of GitHub: nothing past the 1000-result cap is lost
silently, a quota refusal is named as one, and queue events keep GitHub's
reason and actor.

GitHub serves at most 1000 runs for one filtered listing. A day on this
repository has had about 2000, so a reader that took one listing per range at
face value would drop half a busy day and the page would read it as a quiet one.
"""

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


@pytest.mark.parametrize(
    ("event_count", "has_next_page"), [(0, False), (2, False), (30, True)]
)
async def test_queue_event_truncation_follows_the_filtered_page(
    event_count: int, has_next_page: bool
):
    # GitHub's totalCount includes timeline items outside the queue filter.
    def handler(request: httpx.Request) -> httpx.Response:
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
                                "timelineItems": {
                                    "totalCount": 70,
                                    "pageInfo": {"hasNextPage": has_next_page},
                                    "nodes": [
                                        {
                                            "__typename": "AddedToMergeQueueEvent",
                                            "createdAt": "2026-10-17T01:00:00Z",
                                        }
                                        for _ in range(event_count)
                                    ],
                                },
                            }
                        ],
                    }
                }
            },
        )

    events, listed = await _reader(handler).queue_events(
        owner="o", repo="r", token="t", since=SINCE, until=UNTIL
    )

    assert len(events) == event_count
    assert listed.truncated is has_next_page


async def test_a_runs_jobs_are_read_for_every_attempt_and_every_page():
    jobs = [{"id": n, "run_attempt": 1 + n % 2} for n in range(130)]
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
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
    # `filter=all`: a re-run's first attempt is the evidence that a job failed
    # and then passed; `latest` would hand back only the second.
    assert all(query["filter"] == ["all"] for query in asked)


async def test_a_graphql_quota_refusal_is_raised_as_one():
    # GraphQL says its quota is spent with HTTP 200 and an error of type
    # RATE_LIMITED, not with a 403.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errors": [
                    {"type": "RATE_LIMITED", "message": "API rate limit exceeded"}
                ]
            },
        )

    with pytest.raises(CiQuotaRefused):
        await _reader(handler).queue_events(
            owner="o", repo="r", token="t", since=SINCE, until=UNTIL
        )


async def test_a_runs_jobs_do_not_take_room_in_the_shared_answer_cache(monkeypatch):
    # The cache holds the pollers' small, re-read answers (each 304 is a free
    # request). A run's jobs are read once per attempt and run to ~140 KB, so
    # keeping them would only push those out.
    monkeypatch.setattr(forge_etags, "_kept", OrderedDict())
    forge_quota.own("minted-token", 4242, time.time() + 3600)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"etag": '"jobs"'},
            json={"total_count": 1, "jobs": [{"id": 1}]},
        )

    await _reader(handler).run_jobs(owner="o", repo="r", token="minted-token", run_id=1)

    assert len(forge_etags._kept) == 0
