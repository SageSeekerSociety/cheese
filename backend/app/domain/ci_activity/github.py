"""Read Actions runs, all their job attempts, and directly merged PR facts.

Run listings use fixed creation-time windows, so unchanged pages can earn free
304s through ``forge_etags``. Jobs are streamed: they are read once per attempt,
not kept at the expense of frequently revalidated pages. Queue timelines are no
longer collected; their stored history remains available to the board.

Request hooks count attempts and original response statuses before an ETag 304
is restored to a cached 200. These counts cover this reader, not token minting
or the free quota probe used to obtain its credential. Reporting yields before
each request when its installation has half its REST quota or less left.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core import forge_quota
from app.core.forge_http import forge_client

#: What GitHub serves of one filtered listing or search, at most.
RESULT_CAP = 1000
#: Jobs pages are read with one streamed request per page, so the page count,
#: not the body size, is what matters here.
_PAGE = 100
#: The runs listing is cached as one entry per page by ``forge_etags``, which
#: keeps an answer only under ``MAX_BODY_BYTES`` (256 KiB). A real run object is
#: ~15 KiB, so a 100-run page (the API's own maximum) is ~1.5 MiB and is never
#: kept — every sweep re-downloads it and the free 304 is never earned. Ten
#: runs a page stays around 150 KiB, small enough to cache while still covering
#: a busy five-minute window in a handful of pages.
RUNS_PAGE = 10
#: GitHub creation-time filters have second precision. A still-busier second
#: is explicitly reported as truncated, never claimed as complete coverage.
_NARROWEST = timedelta(seconds=1)

_MERGED_PRS_QUERY = """
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


class CiReadDeferred(CiGitHubError):
    """Reporting leaves the remaining quota for higher-priority work."""


@dataclass(frozen=True)
class QueueEvent:
    """A stored historical event; the reader no longer fetches new timelines."""

    pr_number: int
    kind: str  # added | removed
    at: datetime
    reason: str | None
    actor: str | None
    head_branch: str | None = None


@dataclass
class Listed:
    """What one ranged read returned, and how many requests it took."""

    items: list[dict[str, Any]]
    requests: int = 0
    truncated: bool = False


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _second_ceiling(moment: datetime) -> datetime:
    if moment.microsecond:
        return moment.replace(microsecond=0) + timedelta(seconds=1)
    return moment


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
        self._attempts: Counter[str] = Counter()
        self._responses: Counter[tuple[str, int]] = Counter()

    @property
    def request_counts(self) -> dict[str, Any]:
        """Reader HTTP attempts, distinct from responses and quota charges."""
        graphql = self._attempts["POST /graphql"]
        total = self._attempts.total()
        return {
            "http_attempts": total,
            "rest_attempts": total - graphql,
            "graphql_attempts": graphql,
            "not_modified": sum(
                n for (_, status), n in self._responses.items() if status == 304
            ),
            "rest_non_304_responses": sum(
                n
                for (endpoint, status), n in self._responses.items()
                if endpoint != "POST /graphql" and status != 304
            ),
            "unanswered": total - self._responses.total(),
            "by_endpoint": dict(self._attempts),
        }

    def _client(self, endpoint: str) -> httpx.AsyncClient:
        async def before(request: httpx.Request) -> None:
            self.ensure_quota(request)
            self._attempts[endpoint] += 1

        async def answered(response: httpx.Response) -> None:
            self._responses[endpoint, response.status_code] += 1

        return forge_client(
            transport=self._transport,
            timeout=30.0,
            event_hooks={"request": [before], "response": [answered]},
        )

    def ensure_quota(self, request: httpx.Request) -> None:
        installation = forge_quota.installation_of(request)
        if installation is None:
            return
        seen = forge_quota.reading(installation)
        if forge_quota.refused_until(installation) is not None or (
            seen is not None
            and seen.limit > 0
            and seen.remaining <= seen.limit * forge_quota.BACKGROUND_HALF_QUOTA
        ):
            # i18n-exempt: internal admin sweep report, not a user refusal
            raise CiReadDeferred(
                "CI reporting yielded to higher-priority quota consumers"
            )

    def check_quota(self, token: str) -> None:
        """Check the cached reading before any data request is attempted."""
        self.ensure_quota(
            httpx.Request("GET", f"{self._api_base}/", headers=self._headers(token))
        )

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
        since, until = _second_ceiling(since), _second_ceiling(until)
        if since >= until:
            return out
        async with self._client("GET /actions/runs") as client:
            await self._runs_in(client, owner, repo, token, since, until, out)
        return out

    async def _runs_in(self, client, owner, repo, token, since, until, out: Listed):
        url = f"{self._api_base}/repos/{owner}/{repo}/actions/runs"
        # GitHub's bounds are inclusive; adjacent half-open windows meet exactly.
        created = f"{_iso(since)}..{_iso(until - timedelta(seconds=1))}"
        page = 1
        while True:
            body = await self._get(
                client,
                url,
                token,
                {"created": created, "per_page": RUNS_PAGE, "page": page},
            )
            out.requests += 1
            total = int(body.get("total_count") or 0)
            if page == 1 and total > RESULT_CAP and until - since > _NARROWEST:
                # Keep split points at the API's second precision as well.
                seconds = int((until - since).total_seconds())
                middle = since + timedelta(seconds=seconds // 2)
                await self._runs_in(client, owner, repo, token, middle, until, out)
                if out.truncated:
                    return
                await self._runs_in(client, owner, repo, token, since, middle, out)
                return
            runs = [r for r in body.get("workflow_runs") or [] if isinstance(r, dict)]
            out.items.extend(runs)
            if total > RESULT_CAP:
                out.truncated = True
                return
            if len(runs) < RUNS_PAGE or page * RUNS_PAGE >= total:
                return
            if page * RUNS_PAGE >= RESULT_CAP:
                out.truncated = True
                return
            page += 1

    async def run_jobs(
        self, *, owner: str, repo: str, token: str, run_id: int
    ) -> Listed:
        """Every job of every attempt; large one-shot answers are not cached."""
        out = Listed(items=[])
        url = f"{self._api_base}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        async with self._client("GET /actions/runs/{id}/jobs") as client:
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

    async def merged_pull_requests(
        self, *, owner: str, repo: str, token: str, since: datetime, until: datetime
    ) -> Listed:
        """Merge facts without fetching any merge-queue timeline nodes."""
        out = Listed(items=[])
        since, until = _second_ceiling(since), _second_ceiling(until)
        if since >= until:
            return out
        async with self._client("POST /graphql") as client:
            await self._prs_in(client, owner, repo, token, since, until, out)
        return out

    async def _prs_in(self, client, owner, repo, token, since, until, out: Listed):
        query = (
            f"repo:{owner}/{repo} is:pr is:merged "
            f"updated:{_iso(since)}..{_iso(until - timedelta(seconds=1))}"
        )
        after: str | None = None
        first = True
        read = 0
        while True:
            resp = await client.post(
                f"{self._api_base}/graphql",
                headers=self._headers(token),
                json={
                    "query": _MERGED_PRS_QUERY,
                    "variables": {"q": query, "after": after},
                },
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
                seconds = int((until - since).total_seconds())
                middle = since + timedelta(seconds=seconds // 2)
                await self._prs_in(client, owner, repo, token, middle, until, out)
                if out.truncated:
                    return
                await self._prs_in(client, owner, repo, token, since, middle, out)
                return
            first = False
            nodes = [n for n in search.get("nodes") or [] if isinstance(n, dict)]
            out.items.extend(nodes)
            read += len(nodes)
            if int(search.get("issueCount") or 0) > RESULT_CAP:
                out.truncated = True
                return
            info = search.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return
            after = info.get("endCursor")
            if after is None or read >= RESULT_CAP:
                out.truncated = True
                return


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
