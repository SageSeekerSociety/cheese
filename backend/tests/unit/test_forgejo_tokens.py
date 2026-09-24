import uuid
from datetime import UTC, datetime, timedelta
from html import escape
from urllib.parse import parse_qs, urlencode

import httpx
import pytest
from sqlalchemy import select

from app.domain.agent.forgejo_tokens import (
    ForgejoTokenError,
    ForgejoTokens,
    purge_expired_tokens,
    seal_forge_password,
)
from app.domain.project.models import ForgeToken, ProjectForge

pytestmark = pytest.mark.anyio


def binding():
    project_id = uuid.uuid4()
    return ProjectForge(
        project_id=project_id,
        kind="forgejo",
        repo="project-bot/project",
        api_url="https://forge.invalid/api/v1",
        url="https://forge.invalid/project.git",
        account_password=seal_forge_password(project_id, "backend-password"),
        default_branch="main",
    )


def oauth_transport(
    token="issued-token", *, invalid_callback=False, lost_response=False
):
    state = {}

    def respond(request):
        assert request.url.host == "forge.invalid"
        path = request.url.path
        if path == "/user/login":
            return httpx.Response(200 if request.method == "GET" else 303)
        if path == "/login/oauth/authorize":
            state.update(request.url.params)
            return httpx.Response(
                200,
                text="".join(
                    f'<input name="{name}" value="{escape(value)}">'
                    for name, value in state.items()
                ),
            )
        if path == "/login/oauth/grant":
            return httpx.Response(
                303,
                headers={
                    "location": state["redirect_uri"]
                    + "?"
                    + urlencode(
                        {
                            "state": "wrong" if invalid_callback else state["state"],
                            "code": "one-use-code",
                        }
                    )
                },
            )
        assert path == "/login/oauth/access_token"
        fields = parse_qs(request.content.decode())
        assert fields["code"] == ["one-use-code"]
        assert fields["code_verifier"]
        if lost_response:
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(
            200,
            json={
                "access_token": token,
                "expires_in": 3600,
                "refresh_token": "never-returned",
            },
        )

    return httpx.MockTransport(respond)


async def test_lost_exchange_leaves_no_cache_or_password_copy(db_factory):
    tokens = ForgejoTokens(
        binding(), sessions=db_factory, transport=oauth_transport(lost_response=True)
    )
    with pytest.raises(httpx.ReadTimeout):
        await tokens.installation_token()
    async with db_factory() as session:
        assert await session.scalar(select(ForgeToken)) is None
    tokens.transport = oauth_transport()
    assert (await tokens.installation_token())[0] == "issued-token"


async def test_cached_token_encrypted_and_survives_new_minter(db_factory):
    project = binding()
    tokens = ForgejoTokens(project, sessions=db_factory, transport=oauth_transport())
    first = await tokens.installation_token()
    assert (
        3500
        < (datetime.fromisoformat(first[1]) - datetime.now(UTC)).total_seconds()
        <= 3600
    )
    async with db_factory() as session:
        lease = await session.scalar(select(ForgeToken))
        assert lease.value != "issued-token"
        assert "never-returned" not in lease.value
    other = ForgejoTokens(
        project,
        sessions=db_factory,
        transport=httpx.MockTransport(
            lambda _: pytest.fail(
                "cached credential should require no upstream request"
            )
        ),
    )
    assert await other.installation_token() == first


async def test_same_project_cannot_reuse_token_for_changed_repository(db_factory):
    project = binding()
    tokens = ForgejoTokens(
        project, sessions=db_factory, transport=oauth_transport("old-account-token")
    )
    await tokens.installation_token()
    project.repo = "other-bot/project"
    other = ForgejoTokens(
        project, sessions=db_factory, transport=oauth_transport("new-account-token")
    )
    assert (await other.installation_token())[0] == "new-account-token"


async def test_wrong_callback_state_is_refused_before_exchange(db_factory):
    tokens = ForgejoTokens(
        binding(), sessions=db_factory, transport=oauth_transport(invalid_callback=True)
    )
    with pytest.raises(ForgejoTokenError, match="invalid callback"):
        await tokens.installation_token()


async def test_expired_cache_is_removed_without_upstream_calls(db_factory):
    tokens = ForgejoTokens(binding(), sessions=db_factory, transport=oauth_transport())
    await tokens.installation_token()
    async with db_factory() as session:
        lease = await session.scalar(select(ForgeToken))
        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert await purge_expired_tokens(db_factory) == {"deleted": 1}
