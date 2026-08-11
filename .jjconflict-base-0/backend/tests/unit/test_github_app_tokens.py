"""The GitHub App minter: sandboxes get read-only, short-lived, cached tokens.

Functional: a real RSA key signs the app JWT, a mock GitHub verifies what the
minter sends (narrowed permissions, correct installation), and the cache is
observable through how many mints reach "GitHub".
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.domain.agent.github_app import GitHubAppError, GitHubAppTokens


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


def _github(mints: list[dict], public_pem: bytes, *, expires_in_s: float = 3600):
    """A fake GitHub that verifies the app JWT and records each mint body."""

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["authorization"]
        assert auth.startswith("Bearer ")
        claims = pyjwt.decode(auth[7:], public_pem, algorithms=["RS256"])
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
async def test_mint_is_narrowed_readonly_and_signed_by_the_app(rsa_key_pem):
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
    # The app identifies itself, and asks for LESS than the app has: no
    # contents/pull_requests — a sandbox can look at CI, never touch code.
    assert mint["claims"]["iss"] == "4533864"
    assert mint["body"] == {
        "permissions": {"actions": "read", "checks": "read", "metadata": "read"}
    }
    assert "/app/installations/152342238/access_tokens" in mint["url"]


@pytest.mark.anyio
async def test_token_is_cached_until_near_expiry(rsa_key_pem):
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(mints, public),
    )
    first, _ = await minter.readonly_token()
    second, _ = await minter.readonly_token()
    assert first == second
    assert len(mints) == 1  # the burst cost one upstream mint


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
