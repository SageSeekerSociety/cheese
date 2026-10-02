"""Background work leaves an exhausted GitHub App installation alone.

An installation has one hourly REST quota for everything the platform does with
it. Once GitHub has said the quota is gone (or under the share kept for
people), background work must not call it again before the reset GitHub named:
each call would be refused anyway and only lengthens the outage for the people
waiting on it.

A fake GitHub stands behind a real minter; every request it receives is
recorded, so "no call" is observable as an empty list.
"""

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import forge_quota
from app.core.forge_http import forge_client
from app.domain.agent.github_app import GitHubAppTokens
from app.domain.project.forge import installation_serves_background

INSTALLATION = 152342238


@pytest.fixture
def clock(monkeypatch):
    """The quota memory's clock, starting now and moved by hand; the memory
    itself starts empty."""
    now = [time.time()]
    monkeypatch.setattr(forge_quota, "time", SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(forge_quota, "_owners", {})
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    return now


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
    """Mints tokens, reports a quota on `/rate_limit`, and answers repository
    reads with whatever `repo_answer` holds."""

    def __init__(self, clock: list[float]):
        self.clock = clock
        self.calls: list[str] = []
        self.rate_limit = {"remaining": 4000, "limit": 5000}
        self.rate_limit_status = 200
        self.repo_answer = httpx.Response(200, json={})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/app/installations/"):
            if request.method == "GET":
                return httpx.Response(200, json={"permissions": {"contents": "read"}})
            expires = datetime.now(UTC) + timedelta(hours=1)
            return httpx.Response(
                201,
                json={"token": "ghs_installation", "expires_at": expires.isoformat()},
            )
        self.calls.append(path)
        if path == "/rate_limit":
            if self.rate_limit_status != 200:
                return self.refusal()
            core = self.rate_limit | {"reset": int(self.clock[0]) + 1800}
            return httpx.Response(200, json={"resources": {"core": core}})
        return self.repo_answer

    def refusal(self, *, wait_s: int = 300) -> httpx.Response:
        return httpx.Response(
            403,
            headers={
                "x-ratelimit-limit": "5000",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(int(self.clock[0]) + wait_s),
            },
            json={"message": "API rate limit exceeded for installation ID"},
        )


def _tokens(github: FakeGitHub, key_path: str) -> GitHubAppTokens:
    return GitHubAppTokens(
        app_id=1,
        private_key_path=key_path,
        installation_id=INSTALLATION,
        repository="cheese",
        transport=github.transport(),
    )


async def _read_repository(tokens: GitHubAppTokens, github: FakeGitHub) -> int:
    """One call made with the installation's token, as any caller makes it."""
    token, _ = await tokens.installation_token()
    async with forge_client(transport=github.transport()) as client:
        response = await client.get(
            "https://api.github.com/repos/o/cheese/branches/main",
            headers={"Authorization": f"Bearer {token}"},
        )
    return response.status_code


@pytest.mark.anyio
async def test_a_quota_refusal_closes_the_installation_until_the_named_reset(
    clock, key_path
):
    github = FakeGitHub(clock)
    tokens = _tokens(github, key_path)
    # `/rate_limit` reports plenty — what it says must not overrule a refusal
    # GitHub has just given.
    github.repo_answer = github.refusal(wait_s=300)
    assert await _read_repository(tokens, github) == 403
    github.calls.clear()

    assert await installation_serves_background(tokens) is False
    clock[0] += 290
    assert await installation_serves_background(tokens) is False
    assert github.calls == []

    clock[0] += 20
    assert await installation_serves_background(tokens) is True


@pytest.mark.anyio
async def test_a_low_quota_reported_on_any_answer_keeps_background_work_away(
    clock, key_path
):
    github = FakeGitHub(clock)
    tokens = _tokens(github, key_path)
    github.repo_answer = httpx.Response(
        200,
        headers={
            "x-ratelimit-limit": "5000",
            "x-ratelimit-remaining": "300",
            "x-ratelimit-reset": str(int(clock[0]) + 600),
            "x-ratelimit-resource": "core",
        },
        json={},
    )
    await _read_repository(tokens, github)
    github.calls.clear()

    assert await installation_serves_background(tokens) is False
    assert github.calls == []

    clock[0] += 601
    assert await installation_serves_background(tokens) is True


@pytest.mark.anyio
async def test_once_the_quota_is_below_the_reserve_the_gate_stops_asking(
    clock, key_path
):
    github = FakeGitHub(clock)
    tokens = _tokens(github, key_path)
    github.rate_limit = {"remaining": 900, "limit": 5000}

    assert await installation_serves_background(tokens) is False
    github.calls.clear()
    assert await installation_serves_background(tokens) is False
    assert github.calls == []


@pytest.mark.anyio
async def test_a_refused_quota_probe_is_a_no(clock, key_path):
    github = FakeGitHub(clock)
    tokens = _tokens(github, key_path)
    github.rate_limit_status = 403

    assert await installation_serves_background(tokens) is False


@pytest.mark.anyio
async def test_a_permission_refusal_is_not_mistaken_for_a_spent_quota(clock, key_path):
    github = FakeGitHub(clock)
    tokens = _tokens(github, key_path)
    github.repo_answer = httpx.Response(
        403, json={"message": "Resource not accessible"}
    )
    await _read_repository(tokens, github)

    assert await installation_serves_background(tokens) is True
