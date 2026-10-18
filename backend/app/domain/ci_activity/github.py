"""Reading Actions runs, their jobs, and merge-queue events out of GitHub.

Three reads, nothing written:

* ``list_runs`` — `GET /repos/{o}/{r}/actions/runs?created=A..B`. GitHub serves
  at most 1000 runs for one filtered listing and says how many matched in
  ``total_count``; a range that matches more is split in half and read as two,
  so a busy day is never silently cut at its 1000th run.
* ``run_jobs`` — `GET /actions/runs/{id}/jobs?filter=all`, every attempt's
  jobs, steps included. ``filter=all`` because a re-run's first attempt is the
  evidence of a job that failed and then passed.
* ``queue_events`` — GraphQL search for PRs updated in a range, with their
  ``AddedToMergeQueueEvent`` / ``RemovedFromMergeQueueEvent`` timeline items
  (the first 30 per PR: GraphQL charges by nodes asked for, 50 PRs x 30 items
  is about 16 points a page, and the most any PR here has had is 14).
  The REST timeline has no merge-queue events; GraphQL is the only source. The
  search caps at 1000 results the same way and is split the same way.

Every call goes through `forge_client`, so GitHub's report of the REST quota on
each answer reaches `forge_quota` (GraphQL has a quota of its own, which it does
not track). The sweep never asks for the same page twice, so `forge_etags` earns
it nothing; the jobs reads are streamed past it so their large answers do not
crowd out the pollers' (see `run_jobs`). A quota refusal, REST or GraphQL,
raises `CiQuotaRefused` so the sweep stops at once instead of spending the next
call on hearing the same refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core import forge_quota
from app.core.forge_http import forge_client

#: What GitHub serves of one filtered listing or search, at most.
RESULT_CAP = 1000
_PAGE = 100
_SEARCH_PAGE = 50
#: A range narrower than this is not split further; one that still matches
#: more than the cap is read to the cap and reported as truncated.
_NARROWEST = timedelta(minutes=5)

_QUEUE_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        headRefName
        createdAt
        mergedAt
        timelineItems(
          first: 30
          itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT, REMOVED_FROM_MERGE_QUEUE_EVENT]
        ) {
          pageInfo { hasNextPage }
          nodes {
            __typename
            ... on AddedToMergeQueueEvent { createdAt actor { login } }
            ... on RemovedFromMergeQueueEvent { createdAt reason actor { login } }
          }
        }
      }
    }
  }
}
"""


class CiGitHubError(RuntimeError):
    """GitHub refused, or answered with something this reader cannot read."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class CiQuotaRefused(CiGitHubError):
    """GitHub refused because the installation's quota is spent."""


@dataclass(frozen=True)
class QueueEvent:
    pr_number: int
    kind: str  # added | removed
    at: datetime
    reason: str | None
    actor: str | None
    #: The PR's head branch. A pull_request run names its PR only while the
    #: branch exists, so this is how a run is matched to its PR after a merge.
    head_branch: str | None = None


@dataclass
class Listed:
    """What one ranged read returned, and how many requests it took."""

    items: list[dict[str, Any]]
    requests: int = 0
    truncated: bool = False


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class GitHubCi:
    """The reads above, against one API base."""

    def __init__(
        self,
        api_base: str = "https://api.github.com",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._transport = transport

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get(self, client: httpx.AsyncClient, url: str, token: str, params):
        resp = await client.get(url, headers=self._headers(token), params=params)
        _raise_for(resp)
        return resp.json()

    async def list_runs(
        self, *, owner: str, repo: str, token: str, since: datetime, until: datetime
    ) -> Listed:
        """Every run created in ``[since, until)``, newest first."""
        out = Listed(items=[])
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            await self._runs_in(client, owner, repo, token, since, until, out)
        return out

    async def _runs_in(self, client, owner, repo, token, since, until, out: Listed):
        url = f"{self._api_base}/repos/{owner}/{repo}/actions/runs"
        # `created=A..B` is inclusive at both ends; one second off the top
        # makes consecutive ranges meet without overlapping.
        created = f"{_iso(since)}..{_iso(until - timedelta(seconds=1))}"
        page = 1
        while True:
            body = await self._get(
                client,
                url,
                token,
                {"created": created, "per_page": _PAGE, "page": page},
            )
            out.requests += 1
            total = int(body.get("total_count") or 0)
            if page == 1 and total > RESULT_CAP and until - since > _NARROWEST:
                middle = since + (until - since) / 2
                await self._runs_in(client, owner, repo, token, middle, until, out)
                await self._runs_in(client, owner, repo, token, since, middle, out)
                return
            runs = [r for r in body.get("workflow_runs") or [] if isinstance(r, dict)]
            out.items.extend(runs)
            if len(runs) < _PAGE:
                return
            if page * _PAGE >= RESULT_CAP:
                out.truncated = True
                return
            page += 1

    async def run_jobs(
        self, *, owner: str, repo: str, token: str, run_id: int
    ) -> Listed:
        """Every job of every attempt of one run.

        Streamed, which `forge_client` sends without `forge_etags`: a run's jobs
        are read once per attempt, so a kept answer would never be asked for
        again, and at ~140 KB each they would push the pollers' small, re-read
        answers (and the free 304s those earn) out of the shared cache.
        """
        out = Listed(items=[])
        url = f"{self._api_base}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            page = 1
            while True:
                params = {"filter": "all", "per_page": _PAGE, "page": page}
                async with client.stream(
                    "GET", url, headers=self._headers(token), params=params
                ) as resp:
                    await resp.aread()
                _raise_for(resp)
                body = resp.json()
                out.requests += 1
                jobs = [j for j in body.get("jobs") or [] if isinstance(j, dict)]
                out.items.extend(jobs)
                if not jobs or len(out.items) >= int(body.get("total_count") or 0):
                    return out
                page += 1

    async def queue_events(
        self, *, owner: str, repo: str, token: str, since: datetime, until: datetime
    ) -> tuple[list[QueueEvent], Listed]:
        """Merge-queue events of every PR updated in ``[since, until)``.

        The events returned are all of each such PR's queue events, not only
        the ones inside the range: a PR updated today may have entered the
        queue yesterday, and the store keeps each event once.
        """
        out = Listed(items=[])
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            await self._prs_in(client, owner, repo, token, since, until, out)
        events: list[QueueEvent] = []
        for node in out.items:
            number = node.get("number")
            timeline = node.get("timelineItems") or {}
            if not isinstance(number, int):
                continue
            items = timeline.get("nodes") or []
            if (timeline.get("pageInfo") or {}).get("hasNextPage"):
                out.truncated = True
            for item in items:
                event = _queue_event(number, item, node.get("headRefName"))
                if event is not None:
                    events.append(event)
        return events, out

    async def _prs_in(self, client, owner, repo, token, since, until, out: Listed):
        query = (
            f"repo:{owner}/{repo} is:pr "
            f"updated:{_iso(since)}..{_iso(until - timedelta(seconds=1))}"
        )
        after: str | None = None
        first = True
        while True:
            resp = await client.post(
                f"{self._api_base}/graphql",
                headers=self._headers(token),
                json={"query": _QUEUE_QUERY, "variables": {"q": query, "after": after}},
            )
            _raise_for(resp)
            body = resp.json()
            if errors := body.get("errors"):
                spent = any(
                    isinstance(e, dict) and e.get("type") == "RATE_LIMITED"
                    for e in errors
                )
                # i18n-exempt: GitHub's own words, for the admin sweep report
                said = f"GraphQL errors: {str(errors)[:300]}"
                raise CiQuotaRefused(said) if spent else CiGitHubError(said)
            out.requests += 1
            search = (body.get("data") or {}).get("search") or {}
            if (
                first
                and int(search.get("issueCount") or 0) > RESULT_CAP
                and until - since > _NARROWEST
            ):
                middle = since + (until - since) / 2
                await self._prs_in(client, owner, repo, token, middle, until, out)
                await self._prs_in(client, owner, repo, token, since, middle, out)
                return
            first = False
            out.items.extend(
                n for n in search.get("nodes") or [] if isinstance(n, dict)
            )
            info = search.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return
            after = info.get("endCursor")


def _queue_event(number: int, item: Any, head_branch: Any) -> QueueEvent | None:
    if not isinstance(item, dict):
        return None
    kind = {
        "AddedToMergeQueueEvent": "added",
        "RemovedFromMergeQueueEvent": "removed",
    }.get(str(item.get("__typename")))
    at = parse_time(item.get("createdAt"))
    if kind is None or at is None:
        return None
    actor = item.get("actor") or {}
    login = actor.get("login") if isinstance(actor, dict) else None
    reason = item.get("reason") if kind == "removed" else None
    return QueueEvent(
        pr_number=number,
        kind=kind,
        at=at,
        reason=str(reason) if reason else None,
        actor=str(login) if login else None,
        head_branch=str(head_branch) if head_branch else None,
    )


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    if forge_quota.rate_limited(resp):
        # i18n-exempt: GitHub's own words, for the admin sweep report
        raise CiQuotaRefused(f"GitHub quota spent (HTTP {resp.status_code})")
    # i18n-exempt: GitHub's own words, for the admin sweep report
    raise CiGitHubError(
        f"GitHub HTTP {resp.status_code}: {resp.text[:300]}", resp.status_code
    )
