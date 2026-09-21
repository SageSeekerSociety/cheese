"""Agent email registration must preserve existing identities and reject failures."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.crypto import encrypt_text
from app.core.errors import GatewayUnavailableError
from app.domain.project.forge import ensure_author_email


@pytest.mark.anyio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_registering_an_author_preserves_other_authors(concurrent):
    email = "agent-one@agent.cheese.local"
    rows = [{"email": "agent-two@agent.cheese.local", "verified": True}]
    binding = SimpleNamespace(
        kind="forgejo",
        repo="project-account/project",
        api_url="https://forge.invalid/api/v1",
        account_password=encrypt_text("password"),
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=binding))
    writes = []

    def respond(request):
        assert request.url.path == "/api/v1/user/emails"
        if request.method == "POST":
            writes.append(json.loads(request.content))
            rows.append({"email": email, "verified": True})
            if concurrent:
                return httpx.Response(422)
            return httpx.Response(201, json=rows)
        assert request.method == "GET"
        return httpx.Response(200, json=rows)

    transport = httpx.MockTransport(respond)
    for _ in range(2):
        await ensure_author_email(uuid.uuid4(), session, email, transport=transport)
    assert writes == [{"emails": [email]}]
    assert rows[0]["email"] == "agent-two@agent.cheese.local"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "registered", [[], [{"email": "agent@agent.cheese.local", "verified": False}]]
)
async def test_registration_failure_does_not_report_success(registered):
    binding = SimpleNamespace(
        kind="forgejo",
        repo="project-account/project",
        api_url="https://forge.invalid/api/v1",
        account_password=encrypt_text("password"),
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=binding))

    def respond(request):
        return (
            httpx.Response(200, json=registered)
            if request.method == "GET"
            else httpx.Response(422)
        )

    with pytest.raises(GatewayUnavailableError):
        await ensure_author_email(
            uuid.uuid4(),
            session,
            "agent@agent.cheese.local",
            transport=httpx.MockTransport(respond),
        )
