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

import httpx
import pytest

from app.domain.review.landing_forge import ForgeUnavailable, GitHubLandingReads

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
