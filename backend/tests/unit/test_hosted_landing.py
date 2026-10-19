"""A hosted repository's external CI reports on the merge commit as a commit
status, and that counts as a check that ran there."""

import asyncio

import httpx

from app.domain.review.landing_forge import GitHubLandingReads


class Tokens:
    async def installation_token(self):
        return "read-token", None


def test_a_commit_status_is_a_check_that_ran_on_the_commit():
    def forge(request):
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        assert request.url.path.endswith("/statuses")
        return httpx.Response(
            200,
            json=[
                {
                    "id": 7,
                    "context": "external-ci",
                    "state": "success",
                    "target_url": "https://ci.test/7",
                }
            ],
        )

    reads = GitHubLandingReads(
        None,
        Tokens(),
        "https://forge.test",
        "acme",
        "rocket",
        transport=httpx.MockTransport(forge),
    )
    (ran,) = asyncio.run(reads.checks("revision"))
    assert (ran.name, ran.status, ran.conclusion, ran.url) == (
        "external-ci",
        "completed",
        "success",
        "https://ci.test/7",
    )
