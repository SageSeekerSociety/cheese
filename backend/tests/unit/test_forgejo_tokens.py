import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.crypto import encrypt_text
from app.domain.agent.forgejo_tokens import ForgejoTokens, revoke_expired_tokens
from app.domain.project.models import ForgeToken, ProjectForge

pytestmark = pytest.mark.anyio


def binding():
    return ProjectForge(
        project_id=uuid.uuid4(),
        kind="forgejo",
        repo="project-bot/project",
        api_url="https://forge.invalid/api/v1",
        url="https://forge.invalid/project.git",
        account_password=encrypt_text("backend-password"),
        default_branch="main",
    )


async def test_mint_reservation_survives_lost_response_and_retries_revocation(
    db_factory,
):
    project = binding()
    issued_names = []

    async def lost_response(request):
        # Inspect through another transaction: the reservation is durable already.
        async with db_factory() as session:
            lease = await session.scalar(select(ForgeToken))
            assert lease is not None
            assert lease.value is None
            issued_names.append(lease.token_name)
        raise httpx.ReadTimeout("response was lost", request=request)

    tokens = ForgejoTokens(
        project, sessions=db_factory, transport=httpx.MockTransport(lost_response)
    )
    with pytest.raises(httpx.ReadTimeout):
        await tokens.installation_token()
    async with db_factory() as session:
        lease = await session.scalar(select(ForgeToken))
        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    result = await revoke_expired_tokens(
        db_factory, transport=httpx.MockTransport(lambda _: httpx.Response(503))
    )
    assert result == {"revoked": 0, "failed": 1}

    def deleted(request):
        assert request.method == "DELETE"
        assert request.url.path.endswith("/tokens/" + issued_names[0])
        return httpx.Response(204)

    result = await revoke_expired_tokens(
        db_factory, transport=httpx.MockTransport(deleted)
    )
    assert result == {"revoked": 1, "failed": 0}
    async with db_factory() as session:
        assert await session.scalar(select(ForgeToken)) is None


async def test_cached_token_encrypted_and_survives_new_minter(db_factory):
    project = binding()
    transport = httpx.MockTransport(
        lambda _: httpx.Response(201, json={"sha1": "issued-token"})
    )
    tokens = ForgejoTokens(project, sessions=db_factory, transport=transport)
    first = await tokens.installation_token()
    async with db_factory() as session:
        lease = await session.scalar(select(ForgeToken))
        assert lease.value != "issued-token"
        assert lease.account_password != "backend-password"
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
        project,
        sessions=db_factory,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(201, json={"sha1": "old-account-token"})
        ),
    )
    await tokens.installation_token()
    project.repo = "other-bot/project"
    other = ForgejoTokens(
        project,
        sessions=db_factory,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(201, json={"sha1": "new-account-token"})
        ),
    )
    assert (await other.installation_token())[0] == "new-account-token"
