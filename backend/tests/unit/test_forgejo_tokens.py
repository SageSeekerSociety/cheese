import json
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
from app.domain.project.models import ForgeToken, Project, ProjectForge
from app.domain.team.models import Team

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
    assert await purge_expired_tokens(db_factory) == {"deleted": 1, "revoked": 0}


def account_tokens(listed: list[dict], created: list[dict], deleted: list[str]):
    """Forgejo's access-token API for the project account, beside its OAuth
    flow: what is created, listed and deleted is recorded."""
    oauth = oauth_transport()

    def respond(request):
        if not request.url.path.startswith("/api/v1/users/project-bot/tokens"):
            return oauth.handle_request(request)
        assert request.headers["authorization"].startswith("Basic ")
        if request.method == "POST":
            body = json.loads(request.content)
            created.append(body)
            return httpx.Response(201, json={"sha1": f"read-{len(created)}"})
        if request.method == "GET":
            return httpx.Response(200, json=listed)
        deleted.append(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(204)

    return httpx.MockTransport(respond)


async def test_a_read_only_token_is_scoped_to_reading_and_kept_apart(db_factory):
    created: list[dict] = []
    tokens = ForgejoTokens(
        binding(), sessions=db_factory, transport=account_tokens([], created, [])
    )

    reading, _ = await tokens.read_token()
    again, _ = await tokens.read_token()
    working, _ = await tokens.installation_token()

    assert reading == again == "read-1"
    assert working == "issued-token"
    [made] = created
    assert made["scopes"] and all(s.startswith("read:") for s in made["scopes"])


async def test_a_read_only_token_past_its_time_is_revoked_upstream(db_factory):
    now = int(datetime.now(UTC).timestamp())
    listed = [
        {"id": 1, "name": f"cheese-read-{now - 60}-a1b2c3"},
        {"id": 2, "name": f"cheese-read-{now + 3600}"},
        {"id": 3, "name": "someone-elses-token"},
    ]
    deleted: list[str] = []
    tokens = ForgejoTokens(
        binding(), sessions=db_factory, transport=account_tokens(listed, [], deleted)
    )
    await tokens.read_token()
    assert deleted == ["1"]


async def test_the_purge_revokes_read_only_tokens_it_drops(db_factory):
    project = binding()
    async with db_factory() as session:
        now = datetime.now(UTC)
        team = Team(
            name=f"team-{uuid.uuid4().hex[:8]}",
            handle=f"t-{uuid.uuid4().hex[:12]}",
            intro="intro",
            description="description",
            avatar_id=1,
            created_at=now,
            updated_at=now,
        )
        session.add(team)
        await session.flush()
        session.add(Project(id=project.project_id, name="Project", team_id=team.id))
        await session.flush()
        session.add(project)
        await session.commit()
    listed: list[dict] = []
    deleted: list[str] = []
    transport = account_tokens(listed, [], deleted)
    await ForgejoTokens(project, sessions=db_factory, transport=transport).read_token()
    async with db_factory() as session:
        lease = await session.scalar(select(ForgeToken))
        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    listed.append(
        {"id": 7, "name": f"cheese-read-{int(datetime.now(UTC).timestamp()) - 1}"}
    )

    assert await purge_expired_tokens(db_factory, transport) == {
        "deleted": 1,
        "revoked": 1,
    }
    assert deleted == ["7"]
