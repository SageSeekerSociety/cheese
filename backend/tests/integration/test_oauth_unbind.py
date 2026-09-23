"""Unbinding an OAuth connection changes how the account can be signed into,
so it takes a fresh re-authentication, and it must not leave an account nobody
can sign in to: a password, a passkey or another sign-in connection has to
remain."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import create_access_token
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.passkey.repositories import PasskeyRepository
from app.domain.user.models import User
from tests.integration.conftest import UserCreator


class _Account:
    def __init__(self, db: AsyncSession, portal, user_creator: UserCreator) -> None:
        self._db = db
        self._portal = portal
        created = user_creator.create_user()
        self.id = created.user_id
        self._password = created.password
        self.headers = {
            "Authorization": "Bearer "
            + create_access_token(created.user_id, handle=created.username)
        }

    def drop_password(self) -> None:
        async def _run() -> None:
            await self._db.execute(
                update(User).where(User.id == self.id).values(hashed_password=None)
            )
            await self._db.flush()

        self._portal.call(_run)

    def connect(self, provider_id: str) -> int:
        async def _run() -> int:
            conn = await OAuthConnectionRepository(self._db).create(
                user_id=self.id,
                provider_id=provider_id,
                provider_user_id=uuid.uuid4().hex,
            )
            return conn.id

        return self._portal.call(_run)

    def add_passkey(self) -> None:
        async def _run() -> None:
            await PasskeyRepository(self._db).create(
                user_id=self.id,
                credential_id=uuid.uuid4().hex,
                public_key=b"key",
                device_type="singleDevice",
            )

        self._portal.call(_run)

    def sudo_ticket(self, client: TestClient, purpose: str = "oauth:unbind") -> str:
        """A ticket got the way a client gets one: by re-entering the password.

        Tests that drop the password take their tickets first, as the ticket
        outlives the credential that minted it.
        """
        resp = client.post(
            "/users/auth/sudo",
            headers=self.headers,
            json={
                "method": "password",
                "credentials": {"password": self._password},
                "purpose": purpose,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["sudoTicket"]

    def unbind(self, client: TestClient, connection_id: int, ticket: str | None):
        return client.request(
            "DELETE",
            f"/users/{self.id}/oauth/connections/{connection_id}",
            headers=self.headers,
            json={} if ticket is None else {"sudoTicket": ticket},
        )

    def connection_ids(self, client: TestClient) -> set[int]:
        resp = client.get(f"/users/{self.id}/oauth/connections", headers=self.headers)
        assert resp.status_code == 200, resp.text
        return {c["id"] for c in resp.json()["data"]["connections"]}


@pytest.fixture
def account(db_session, _portal, user_client, api_client) -> _Account:
    return _Account(db_session, _portal, user_client)


def test_unbind_needs_a_ticket(account: _Account, api_client: TestClient):
    conn_id = account.connect("github")

    refused = account.unbind(api_client, conn_id, None)

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["name"] == "SudoRequiredError"
    assert conn_id in account.connection_ids(api_client)


def test_a_ticket_for_another_operation_is_refused(
    account: _Account, api_client: TestClient
):
    conn_id = account.connect("github")
    ticket = account.sudo_ticket(api_client, "passkey:delete")

    refused = account.unbind(api_client, conn_id, ticket)

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["name"] == "SudoRequiredError"
    assert conn_id in account.connection_ids(api_client)


def test_a_ticket_is_spent_by_its_first_use(account: _Account, api_client: TestClient):
    first = account.connect("github")
    second = account.connect("google")
    ticket = account.sudo_ticket(api_client)

    assert account.unbind(api_client, first, ticket).status_code == 200

    replay = account.unbind(api_client, second, ticket)
    assert replay.status_code == 403, replay.text
    assert second in account.connection_ids(api_client)


def test_last_sign_in_method_cannot_be_unbound(
    account: _Account, api_client: TestClient
):
    ticket = account.sudo_ticket(api_client)
    account.drop_password()
    conn_id = account.connect("github")
    # A link-only connection is not a way to sign in, so it does not help.
    account.connect("github_app")

    resp = account.unbind(api_client, conn_id, ticket)

    assert resp.status_code == 409
    assert conn_id in account.connection_ids(api_client)


def test_unbind_allowed_with_a_password(account: _Account, api_client: TestClient):
    conn_id = account.connect("github")

    resp = account.unbind(api_client, conn_id, account.sudo_ticket(api_client))

    assert resp.status_code == 200
    assert conn_id not in account.connection_ids(api_client)


def test_unbind_allowed_with_a_passkey(account: _Account, api_client: TestClient):
    ticket = account.sudo_ticket(api_client)
    account.drop_password()
    account.add_passkey()
    conn_id = account.connect("google")

    assert account.unbind(api_client, conn_id, ticket).status_code == 200


def test_unbind_allowed_with_another_sign_in_connection(
    account: _Account, api_client: TestClient
):
    tickets = [account.sudo_ticket(api_client), account.sudo_ticket(api_client)]
    account.drop_password()
    first = account.connect("github")
    second = account.connect("ruc")

    assert account.unbind(api_client, first, tickets[0]).status_code == 200
    # Now the remaining one is the last way in.
    assert account.unbind(api_client, second, tickets[1]).status_code == 409


def test_link_only_connection_can_always_be_unbound(
    account: _Account, api_client: TestClient
):
    ticket = account.sudo_ticket(api_client)
    account.drop_password()
    account.connect("github")
    link_id = account.connect("github_app")

    assert account.unbind(api_client, link_id, ticket).status_code == 200


def test_unknown_connection_is_not_found(account: _Account, api_client: TestClient):
    ticket = account.sudo_ticket(api_client)

    assert account.unbind(api_client, 987654321, ticket).status_code == 404
