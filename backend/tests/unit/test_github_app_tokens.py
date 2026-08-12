"""The GitHub App minter: sandboxes get read-only, short-lived, cached tokens.

Functional: a real RSA key signs the app JWT, a mock GitHub verifies what the
minter sends (narrowed permissions, correct installation), and the cache is
observable through how many mints reach "GitHub".

The permission narrowing is the load-bearing part. GitHub rejects the *whole*
mint with 422 when any requested permission was never granted to the
installation, so what the sandbox asks for has to be intersected with what the
App actually holds — and the intersection must never let a write level through.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.domain.agent import github_app as github_app_module
from app.domain.agent.github_app import GitHubAppError, GitHubAppTokens

# What `cheesex-app` was actually granted on SageSeekerSociety, read off
# `GET /orgs/{org}/installations` on 2026-08-12. Note what is NOT here:
# `issues`. Until an org admin adds it, no sandbox token can carry it, and
# asking anyway would 422 the mint and take CI-log reading down with it.
_CHEESEX_APP_GRANTS = {
    "actions": "read",
    "checks": "read",
    "contents": "write",
    "metadata": "read",
    "pull_requests": "write",
    "workflows": "write",
}


@pytest.fixture
def rsa_key_pem(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "app.pem"
    path.write_bytes(pem)
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return str(path), public


def _github(
    mints: list[dict],
    public_pem: bytes,
    *,
    expires_in_s: float = 3600,
    granted: dict[str, str] | None = None,
    grant_lookups: list[str] | None = None,
    outage: dict[str, bool] | None = None,
):
    """A fake GitHub: describes the installation, and mints against it.

    Both endpoints verify the app JWT. `mints` records each mint body, and
    `grant_lookups` (when passed) records each "what does this installation
    hold" call, so the caching of both is observable. Flipping `outage["on"]`
    makes the describe call start failing mid-test.
    """
    grants = _CHEESEX_APP_GRANTS if granted is None else granted

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["authorization"]
        assert auth.startswith("Bearer ")
        claims = pyjwt.decode(auth[7:], public_pem, algorithms=["RS256"])
        if request.method == "GET":
            if grant_lookups is not None:
                grant_lookups.append(str(request.url))
            if outage and outage["on"]:
                return httpx.Response(500, text="upstream hiccup")
            return httpx.Response(200, json={"permissions": grants})
        body = json.loads(request.content)
        mints.append({"claims": claims, "body": body, "url": str(request.url)})
        return httpx.Response(
            201,
            json={
                "token": f"ghs_test_{len(mints)}",
                "expires_at": _iso_in(expires_in_s),
            },
        )

    return httpx.MockTransport(handler)


def _iso_in(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


@pytest.mark.anyio
async def test_mint_asks_for_read_on_everything_the_app_grants(rsa_key_pem):
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    minter = GitHubAppTokens(
        app_id=4533864,
        private_key_path=key_path,
        installation_id=152342238,
        transport=_github(mints, public),
    )
    token, expires_at = await minter.readonly_token()

    assert token == "ghs_test_1"
    assert expires_at  # ISO string
    [mint] = mints
    assert mint["claims"]["iss"] == "4533864"
    # contents and pull_requests are DOWNGRADED (the App holds write), issues
    # is dropped entirely (the App holds nothing), workflows is never wanted.
    assert mint["body"] == {
        "permissions": {
            "actions": "read",
            "checks": "read",
            "contents": "read",
            "metadata": "read",
            "pull_requests": "read",
        }
    }
    assert "/app/installations/152342238/access_tokens" in mint["url"]


@pytest.mark.anyio
async def test_a_sandbox_token_never_asks_for_write(rsa_key_pem):
    """Even when the installation would happily hand over write on everything."""
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    generous = dict.fromkeys(
        ["actions", "checks", "contents", "issues", "metadata", "pull_requests"],
        "write",
    ) | {"administration": "admin", "workflows": "write"}
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(mints, public, granted=generous),
    )
    await minter.readonly_token()

    [mint] = mints
    asked = mint["body"]["permissions"]
    assert set(asked.values()) == {"read"}, asked
    # The two that would let an agent rewrite the repo or its CI, specifically.
    assert "workflows" not in asked
    assert "administration" not in asked


@pytest.mark.anyio
async def test_issues_read_arrives_with_the_grant_no_deploy_needed(rsa_key_pem):
    """The org-admin half of this change needs no code half."""
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(
            mints, public, granted=_CHEESEX_APP_GRANTS | {"issues": "read"}
        ),
    )
    await minter.readonly_token()

    assert mints[0]["body"]["permissions"]["issues"] == "read"
    assert await minter.sandbox_permissions() == {
        "actions": "read",
        "checks": "read",
        "contents": "read",
        "issues": "read",
        "metadata": "read",
        "pull_requests": "read",
    }


@pytest.mark.anyio
async def test_reported_permissions_match_what_was_minted(rsa_key_pem):
    """What the route tells a sandbox is what the token can really do — and
    saying so costs no extra round trip to GitHub."""
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    lookups: list[str] = []
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(mints, public, grant_lookups=lookups),
    )
    await minter.readonly_token()
    reported = await minter.sandbox_permissions()

    assert reported == mints[0]["body"]["permissions"]
    assert "issues" not in reported  # honest about what is missing today
    assert len(lookups) == 1


@pytest.mark.anyio
async def test_write_token_is_sent_unnarrowed(rsa_key_pem):
    """The backend's own write mint must fail loudly at GitHub if a grant was
    revoked, not quietly come back weaker and die later at `git push`."""
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    lookups: list[str] = []
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(mints, public, grant_lookups=lookups),
    )
    await minter.write_token()

    assert mints[0]["body"] == {
        "permissions": {
            "contents": "write",
            "metadata": "read",
            "pull_requests": "write",
        }
    }
    assert lookups == []  # nothing to intersect against


@pytest.mark.anyio
async def test_token_is_cached_until_near_expiry(rsa_key_pem):
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    lookups: list[str] = []
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(mints, public, grant_lookups=lookups),
    )
    first, _ = await minter.readonly_token()
    second, _ = await minter.readonly_token()
    assert first == second
    assert len(mints) == 1  # the burst cost one upstream mint
    assert len(lookups) == 1  # and one permission lookup, not one per call


@pytest.mark.anyio
async def test_near_expiry_token_is_reminted(rsa_key_pem):
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    # Expires in 10 min — inside the refresh margin (a long turn could outlive
    # it), so the next call must NOT reuse it.
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(mints, public, expires_in_s=600),
    )
    first, _ = await minter.readonly_token()
    second, _ = await minter.readonly_token()
    assert len(mints) == 2
    assert second != first


@pytest.mark.anyio
async def test_github_refusal_surfaces_as_an_error(rsa_key_pem):
    key_path, _ = rsa_key_pem
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="bad credentials")
        ),
    )
    with pytest.raises(GitHubAppError, match="401"):
        await minter.readonly_token()


@pytest.mark.anyio
async def test_a_blip_reading_grants_does_not_shrink_the_token(
    rsa_key_pem, monkeypatch
):
    """A flaky lookup must not silently downgrade a sandbox to fewer
    permissions than it had a minute ago."""
    key_path, public = rsa_key_pem
    monkeypatch.setattr(github_app_module, "_GRANTS_TTL_S", 0)  # every call refetches
    mints: list[dict] = []
    outage = {"on": False}
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(
            mints,
            public,
            granted=_CHEESEX_APP_GRANTS | {"issues": "read"},
            outage=outage,
        ),
    )
    before = await minter.sandbox_permissions()
    assert "issues" in before

    outage["on"] = True
    assert await minter.sandbox_permissions() == before
