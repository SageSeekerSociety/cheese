"""The GitHub App minter: agents get short-lived, cached, unshrunk tokens.

Functional: a real RSA key signs the app JWT, a mock GitHub verifies what the
minter sends (which permissions, which installation), and the cache is
observable through how many mints reach "GitHub".

Matching the installation's grants is the load-bearing part. GitHub rejects the
*whole* mint with 422 when any requested permission was never granted, so
asking for exactly the grant map is the one request that always survives — and
it means an admin adding a permission reaches agents with no deploy.
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
# `issues`. Until an org admin adds it, no agent token can carry it, and
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
async def test_mint_asks_for_everything_the_app_grants(rsa_key_pem):
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    minter = GitHubAppTokens(
        app_id=4533864,
        private_key_path=key_path,
        installation_id=152342238,
        transport=_github(mints, public),
    )
    token, expires_at = await minter.installation_token()

    assert token == "ghs_test_1"
    assert expires_at  # ISO string
    [mint] = mints
    assert mint["claims"]["iss"] == "4533864"
    # Nothing is downgraded and nothing is dropped: what the installation holds
    # is what the token carries.
    assert mint["body"] == {"permissions": _CHEESEX_APP_GRANTS}
    assert "/app/installations/152342238/access_tokens" in mint["url"]


@pytest.mark.anyio
async def test_an_agent_token_asks_for_write_when_the_installation_holds_it(
    rsa_key_pem,
):
    """This test used to assert the exact opposite — that every level asked for
    was `read`, and that `workflows` and `administration` were absent whatever
    the installation held. That was the one line between an agent and the App's
    write grants, and it is deliberately gone (Zhifei, 2026-08-27).

    The reason it is not merely relaxed but inverted: with a read-only token an
    agent cannot push its own branch or open its own PR, so the last step of
    every piece of work is handed back to a human or to the backend. The
    platform's stalls have all come from a credential being too small
    (`docs/agent-principles.md` §2), so the credential now carries the
    installation's grants verbatim — including any an admin adds later, which
    is why the generous map below is asserted in full rather than filtered."""
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
    await minter.installation_token()

    [mint] = mints
    assert mint["body"]["permissions"] == generous


@pytest.mark.anyio
async def test_issues_read_arrives_with_the_grant_no_deploy_needed(rsa_key_pem):
    """The org-admin half of this change needs no code half."""
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    granted = _CHEESEX_APP_GRANTS | {"issues": "read"}
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        transport=_github(mints, public, granted=granted),
    )
    await minter.installation_token()

    assert mints[0]["body"]["permissions"]["issues"] == "read"
    assert await minter.granted_permissions() == granted


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
    await minter.installation_token()
    reported = await minter.granted_permissions()

    assert reported == mints[0]["body"]["permissions"]
    assert "issues" not in reported  # honest about what is missing today
    assert len(lookups) == 1


@pytest.mark.anyio
async def test_write_token_is_sent_unnarrowed(rsa_key_pem):
    """The write mint names its permissions instead of taking the grant map, so
    that a revoked one fails loudly at GitHub rather than quietly coming back
    weaker and dying later at `git push`."""
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
            # Without workflows:write GitHub rejects pushing any branch that
            # touches .github/workflows/* — the accept-blocking 422 of ee17b136.
            "workflows": "write",
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
    first, _ = await minter.installation_token()
    second, _ = await minter.installation_token()
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
    first, _ = await minter.installation_token()
    second, _ = await minter.installation_token()
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
        await minter.installation_token()


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
    before = await minter.granted_permissions()
    assert "issues" in before

    outage["on"] = True
    assert await minter.granted_permissions() == before


@pytest.mark.anyio
async def test_project_minter_limits_both_tokens_to_bound_repository(rsa_key_pem):
    key_path, public = rsa_key_pem
    mints: list[dict] = []
    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=2,
        repository="widgets",
        transport=_github(mints, public),
    )
    await minter.installation_token()
    await minter.write_token()
    assert len(mints) == 2
    assert all(mint["body"]["repositories"] == ["widgets"] for mint in mints)
    assert mints[0]["body"]["permissions"] == _CHEESEX_APP_GRANTS


@pytest.mark.anyio
async def test_project_factory_mints_only_its_repository_and_isolates_cache(
    monkeypatch, rsa_key_pem
):
    import uuid
    from types import SimpleNamespace

    key_path, public = rsa_key_pem
    mints: list[dict] = []
    real_client = httpx.AsyncClient
    transport = _github(mints, public)
    monkeypatch.setattr(
        github_app_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**(kwargs | {"transport": transport})),
    )
    monkeypatch.setattr(github_app_module.settings, "github_app_id", 1)
    monkeypatch.setattr(
        github_app_module.settings, "github_app_private_key_path", key_path
    )
    monkeypatch.setattr(github_app_module, "_instances", {})
    repo_name = "acme/widgets"

    async def binding(self, project_id):
        return SimpleNamespace(repo=repo_name, installation_id=2)

    monkeypatch.setattr(
        github_app_module.ProjectGitInstallationRepository, "get_by_project", binding
    )
    first = await github_app_module.github_app_tokens_for_project(uuid.uuid4(), None)
    assert first is not None
    await first.installation_token()
    repo_name = "acme/second"
    second = await github_app_module.github_app_tokens_for_project(uuid.uuid4(), None)
    assert second is not None
    await second.installation_token()
    assert [m["body"]["repositories"] for m in mints] == [["widgets"], ["second"]]


@pytest.mark.anyio
async def test_user_installation_lookup_uses_user_authority_and_all_pages(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer user-test-token"
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 7}]})
        assert request.url.path == "/user/installations/7/repositories"
        repos = (
            [{"full_name": "acme/first"}] * 100
            if request.url.params["page"] == "1"
            else [{"full_name": "acme/last"}]
        )
        return httpx.Response(200, json={"repositories": repos})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        github_app_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    assert await github_app_module.list_user_installations("user-test-token") == [
        {"id": 7}
    ]
    repos = await github_app_module.fetch_user_installation_repos("user-test-token", 7)
    assert len(repos) == 101
    assert repos[-1]["full_name"] == "acme/last"
    assert len(seen) == 3
