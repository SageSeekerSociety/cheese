"""Polled GitHub reads that have not changed cost no quota.

The pollers read the same PRs, branches and check runs every pass. GitHub
answers a read that names the ETag it still holds with 304, which is not charged
to the installation's hourly quota; the caller must still get the full answer.

A fake GitHub stands behind a real minter and a real PR client. It charges its
quota for every answer except a 304 and reports the remainder in the headers,
the way GitHub does.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import forge_quota
from app.domain.agent.github_app import GitHubAppTokens
from app.domain.review.github_pr import HttpxGitHubPrClient

INSTALLATION = 152342238


@pytest.fixture
def key_path(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(path)


class FakeGitHub:
    def __init__(self) -> None:
        self.remaining = 5000
        self.minted = 0
        self.pr = {"state": "open", "merged": False, "head_sha": "a" * 40}
        self.version = 1
        self.reads: list[dict] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            if request.method == "GET":
                return httpx.Response(200, json={"permissions": {"contents": "read"}})
            self.minted += 1
            expires = datetime.now(UTC) + timedelta(hours=1)
            return httpx.Response(
                201,
                json={
                    "token": f"ghs_{self.minted}",
                    "expires_at": expires.isoformat(),
                },
            )
        self.reads.append(
            {
                "path": request.url.path,
                "if_none_match": request.headers.get("if-none-match"),
            }
        )
        etag = f'"v{self.version}"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers=self.quota_headers() | {"etag": etag})
        self.remaining -= 1
        body = {
            "number": 7,
            "state": self.pr["state"],
            "merged": self.pr["merged"],
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {"sha": self.pr["head_sha"], "ref": "task/x"},
            "base": {"ref": "main"},
        }
        return httpx.Response(
            200, headers=self.quota_headers() | {"etag": etag}, json=body
        )

    def quota_headers(self) -> dict[str, str]:
        reset = int((datetime.now(UTC) + timedelta(minutes=30)).timestamp())
        return {
            "x-ratelimit-limit": "5000",
            "x-ratelimit-remaining": str(self.remaining),
            "x-ratelimit-reset": str(reset),
            "x-ratelimit-resource": "core",
        }


def _world(key_path: str) -> tuple[FakeGitHub, GitHubAppTokens, HttpxGitHubPrClient]:
    github = FakeGitHub()
    tokens = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=INSTALLATION,
        repository="cheese",
        transport=github.transport(),
    )
    return github, tokens, HttpxGitHubPrClient(transport=github.transport())


async def _status(tokens: GitHubAppTokens, client: HttpxGitHubPrClient):
    token, _ = await tokens.installation_token()
    return await client.pull_request_status(
        owner="o", repo="cheese", number=7, token=token
    )


@pytest.mark.anyio
async def test_an_unchanged_pr_is_answered_in_full_without_spending_quota(key_path):
    github, tokens, client = _world(key_path)
    first = await _status(tokens, client)
    spent = github.remaining

    again = await _status(tokens, client)

    assert github.reads[-1]["if_none_match"] == '"v1"'
    assert again == first
    assert github.remaining == spent
    seen = forge_quota.reading(INSTALLATION)
    assert seen is not None and seen.remaining == spent


@pytest.mark.anyio
async def test_a_changed_pr_replaces_what_was_kept(key_path):
    github, tokens, client = _world(key_path)
    assert (await _status(tokens, client)).merged is False

    github.pr = {"state": "closed", "merged": True, "head_sha": "b" * 40}
    github.version = 2
    changed = await _status(tokens, client)
    assert changed.merged is True and changed.head_sha == "b" * 40

    after = await _status(tokens, client)
    assert github.reads[-1]["if_none_match"] == '"v2"'
    assert after == changed


@pytest.mark.anyio
async def test_a_new_hour_s_token_still_reads_conditionally(key_path):
    """Installation tokens are re-minted hourly; that must not cost every
    unchanged answer a full read."""
    github, tokens, client = _world(key_path)
    await _status(tokens, client)
    tokens._cached.clear()  # the hour's token has expired

    await _status(tokens, client)

    assert github.minted == 2
    assert github.reads[-1]["if_none_match"] == '"v1"'


@pytest.mark.anyio
async def test_a_credential_the_platform_did_not_mint_reads_plainly(key_path):
    github, _tokens, client = _world(key_path)
    for _ in range(2):
        await client.pull_request_status(
            owner="o", repo="cheese", number=7, token="someone-elses"
        )

    assert [read["if_none_match"] for read in github.reads] == [None, None]
