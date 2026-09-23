"""An account value can belong to one account only (#1483).

Usernames and emails are unique case-insensitively among live accounts, a
provider identity links to one account, and a passkey credential is stored once.
The database enforces each of these, so two requests that arrive together get
one success and one conflict rather than two rows.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import func, select

from app.api.routes.users import _issue_oauth_state_token
from app.core.errors import ConflictError
from app.domain.identity.handles import agent_instance_handle
from app.domain.identity.services import IdentityService
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import OAuthService
from app.domain.passkey import services as passkey_services
from app.domain.passkey.repositories import PasskeyRepository
from app.domain.passkey.services import PasskeyService
from app.domain.user.models import User
from tests.support.consent import OAUTH_CONSENT_FORM, SIGNUP_CONSENT

pytestmark = pytest.mark.anyio


async def _arm_email_code(email: str) -> str:
    """Request a code through the real sending path and read it from the mail;
    the register route checks it."""
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.verification_service import EmailVerificationService

    class _Outbox:
        is_configured = True
        body = ""

        async def send(self, **kwargs) -> bool:
            self.body = kwargs["body_text"]
            return True

    outbox = _Outbox()
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        service = EmailVerificationService(redis)
        service._sender = outbox
        await service.send_verification_code(email)
    finally:
        await redis.aclose()

    match = re.search(r"\b(\d{6})\b", outbox.body)
    assert match, outbox.body
    return match.group(1)


async def _registration(username: str, email: str) -> dict:
    code = await _arm_email_code(email)
    return {
        "username": username,
        "nickname": "someone",
        "email": email,
        "emailCode": code,
        "consent": SIGNUP_CONSENT,
        "password": "TestPassword123!",
    }


async def _count_users(factory, *, username: str) -> int:
    async with factory() as session:
        return await session.scalar(
            select(func.count(User.id)).where(
                func.lower(User.username) == username.lower()
            )
        )


def _oauth_create(client, *, provider_uid: str, username: str, nickname="Prov"):
    token = client.portal.call(
        _issue_oauth_state_token,
        "ruc",
        {
            "id": provider_uid,
            "email": None,
            "name": "Prov User",
            "username": None,
            "preferredUsername": "provuser",
        },
    )
    resp = client.post(
        "/users/oauth/create",
        data={
            **OAUTH_CONSENT_FORM,
            "stateToken": token,
            "username": username,
            "nickname": nickname,
            "passwordMode": "none",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    return {
        k: v[0] for k, v in parse_qs(urlparse(resp.headers["location"]).query).items()
    }


async def test_username_differing_only_in_case_is_taken(client):
    first = client.post("/users", json=await _registration("Casey01", "c1@example.com"))
    assert first.status_code == 200, first.text

    second = client.post(
        "/users", json=await _registration("casey01", "c2@example.com")
    )

    assert second.status_code == 409, second.text
    assert await _count_users(client.test_factory, username="casey01") == 1


async def test_email_differing_only_in_case_is_taken(client):
    first = client.post(
        "/users", json=await _registration("mailer1", "Mix@Example.com")
    )
    assert first.status_code == 200, first.text

    # A case variant of a taken address is turned away when it asks for a
    # code, before a code could be sent to it.
    second = client.post("/users/verify/email", json={"email": "mix@example.com"})

    assert second.status_code == 409, second.text


async def test_concurrent_registrations_of_one_name_let_exactly_one_through(
    python_client,
):
    payloads = [
        await _registration("racer01", "racer-a@example.com"),
        await _registration("RACER01", "racer-b@example.com"),
    ]

    responses = await asyncio.gather(
        *(python_client.post("/users", json=p) for p in payloads)
    )

    assert sorted(r.status_code for r in responses) == [200, 409], [
        r.text for r in responses
    ]
    assert await _count_users(python_client.test_factory, username="racer01") == 1


@pytest.mark.parametrize(
    ("username", "status"),
    [("abc", 422), ("abcd", 200), ("a" * 32, 200), ("a" * 33, 422)],
)
async def test_registration_username_length_bounds(client, username, status):
    resp = client.post(
        "/users",
        json=await _registration(
            username, f"{username}-{uuid.uuid4().hex[:8]}@example.com"
        ),
    )

    assert resp.status_code == status, resp.text


@pytest.mark.parametrize(
    ("username", "error_code"),
    [
        ("abc", "INVALID_USERNAME"),
        ("abcd", None),
        ("b" * 32, None),
        ("b" * 33, "INVALID_USERNAME"),
    ],
)
async def test_oauth_create_username_length_bounds(client, username, error_code):
    params = _oauth_create(client, provider_uid=f"len-{username}", username=username)

    assert params.get("error_code") == error_code, params


async def test_oauth_suggestion_passes_the_username_rule(client):
    token = client.portal.call(
        _issue_oauth_state_token,
        "ruc",
        {"id": "suggest-1", "email": None, "name": "张三", "preferredUsername": "张三"},
    )
    suggested = client.get(f"/users/auth/oauth/state?token={token}").json()["data"]

    params = _oauth_create(
        client,
        provider_uid="suggest-1",
        username=suggested["suggestedUsername"],
        nickname=suggested["suggestedNickname"],
    )

    assert "error_code" not in params, params


async def test_oauth_create_for_a_linked_identity_leaves_no_second_account(client):
    first = _oauth_create(client, provider_uid="linked-1", username="linked_first")
    assert "error_code" not in first, first

    again = _oauth_create(client, provider_uid="linked-1", username="linked_second")

    assert again["error_code"] == "ALREADY_LINKED"
    assert await _count_users(client.test_factory, username="linked_second") == 0


async def test_a_linked_provider_identity_cannot_be_linked_again(client):
    async with client.test_factory() as session:
        users = [
            User(
                username=f"oauth-dup-{i}",
                email=f"oauth-dup-{i}@example.com",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            for i in range(2)
        ]
        session.add_all(users)
        await session.flush()
        service = OAuthService(repo=OAuthConnectionRepository(session))
        await service.create_connection(
            user_id=users[0].id, provider_id="ruc", provider_user_id="dup-uid"
        )

        with pytest.raises(ConflictError) as raised:
            await service.create_connection(
                user_id=users[1].id, provider_id="ruc", provider_user_id="dup-uid"
            )

        assert raised.value.status_code == 409
        # The conflict left the transaction usable.
        assert await service.get_connection_by_provider("ruc", "dup-uid")


async def test_a_passkey_credential_is_registered_once(client, monkeypatch):
    # Stand in for the authenticator: every attestation names one credential.
    monkeypatch.setattr(
        passkey_services,
        "verify_registration_response",
        lambda **_: SimpleNamespace(
            credential_id=b"same-credential",
            credential_public_key=b"key",
            sign_count=0,
        ),
    )

    async with client.test_factory() as session:
        service = PasskeyService(repo=PasskeyRepository(session))
        await service.verify_registration(
            user_id=1, challenge="AAAA", credential={"response": {}}
        )

        with pytest.raises(ConflictError) as raised:
            await service.verify_registration(
                user_id=1, challenge="AAAA", credential={"response": {}}
            )

        assert raised.value.status_code == 409


async def test_concurrent_first_turns_resolve_to_one_agent_user(db_factory):
    handle = agent_instance_handle(uuid.uuid4())

    async def _ensure() -> int:
        async with db_factory() as session:
            user = await IdentityService(session).ensure_agent_user(handle=handle)
            await session.commit()
            return user.id

    ids = await asyncio.gather(_ensure(), _ensure())

    assert ids[0] == ids[1]
    assert await _count_users(db_factory, username=handle) == 1


async def test_a_username_a_team_holds_is_taken(client):
    """Users and teams share one namespace: a handle names one of them."""
    from app.domain.team.models import Team

    async with client.test_factory() as session:
        now = datetime.now(UTC)
        session.add(
            Team(
                name="Namespace Team",
                handle="Shared01",
                intro="",
                description="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    taken = client.post(
        "/users", json=await _registration("shared01", "s1@example.com")
    )

    assert taken.status_code == 409, taken.text
    assert await _count_users(client.test_factory, username="shared01") == 0
