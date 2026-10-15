"""A person can close their own account (注销), and only their own.

Once closed, nothing signs in as them again — not their password, not a
refresh of an old sign-in, not an access token issued before — while what
they wrote in shared rooms stays, and is recognisably a closed account's.
Their email address is free to register again; their handle is not, because
rooms and rosters still name it."""

import uuid

import pytest
from sqlalchemy import select

from app.core.redis import get_redis_client
from app.domain.block.models import Block
from tests.integration.conftest import post_project, sign_in
from tests.integration.test_account_uniqueness import _arm_email_code
from tests.support.consent import SIGNUP_CONSENT

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _forget_closings(client):
    """User ids start over in every test's rolled-back transaction, so a
    closing recorded by one test would otherwise refuse the next test's
    account that got the same id within the same second."""
    yield

    async def forget() -> None:
        redis = get_redis_client()
        if redis is not None:
            async for key in redis.scan_iter(match="account-closed:*"):
                await redis.delete(key)

    client.portal.call(forget)


def _handle(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ticket(client, token: str) -> str:
    r = client.post(
        "/users/auth/sudo",
        headers=_auth(token),
        json={
            "method": "password",
            "credentials": {"password": PASSWORD},
            "purpose": "account:delete",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["sudoTicket"]


def _close(client, token: str, *, username: str, ticket: str | None):
    body: dict = {"username": username}
    if ticket is not None:
        body["sudoTicket"] = ticket
    return client.request("DELETE", "/users/me", headers=_auth(token), json=body)


def _can_sign_in(client, handle: str) -> bool:
    r = client.post(
        "/users/auth/login", json={"username": handle, "password": PASSWORD}
    )
    return r.status_code == 200


def test_a_closed_account_signs_in_by_no_means(client):
    person = sign_in(client, _handle("closer"))

    closed = _close(
        client,
        person.token,
        username=person.handle,
        ticket=_ticket(client, person.token),
    )

    assert closed.status_code == 200, closed.text
    assert not _can_sign_in(client, person.handle)
    refreshed = client.post(
        "/users/auth/refresh-token",
        headers={"Cookie": f"cheese_refresh={person.refresh}"},
    )
    assert refreshed.status_code != 200, refreshed.text
    # The access token it held before is still within its lifetime.
    assert client.get("/users/me", headers=_auth(person.token)).status_code == 401


def test_its_email_registers_again_but_its_handle_does_not(client):
    person = sign_in(client, _handle("reuse"))
    email = f"{person.handle}@example.com"
    assert (
        _close(
            client,
            person.token,
            username=person.handle,
            ticket=_ticket(client, person.token),
        ).status_code
        == 200
    )

    def signup(username: str, email: str):
        payload = {
            "username": username,
            "nickname": username,
            "email": email,
            "password": "TestPassword123!",
            "consent": SIGNUP_CONSENT,
        }
        payload["emailCode"] = client.portal.call(_arm_email_code, email)
        return client.post("/users", json=payload)

    assert signup(person.handle, f"{person.handle}@example.org").status_code == 409
    fresh = signup(_handle("newcomer"), email)
    assert fresh.status_code == 200, fresh.text


def test_what_it_wrote_in_a_shared_room_stays_and_reads_as_closed(client):
    author = sign_in(client, _handle("writer"))
    reader = sign_in(client, _handle("reader"))
    project = post_project(
        client, json={"name": "Shared"}, headers=_auth(author.token)
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=_auth(author.token),
    ).json()["data"]
    sent = client.post(
        f"/topics/{room['id']}/messages",
        json={"content": "留给大家的话", "request_id": str(uuid.uuid4())},
        headers=_auth(author.token),
    )
    assert sent.status_code == 200, sent.text

    assert (
        _close(
            client,
            author.token,
            username=author.handle,
            ticket=_ticket(client, author.token),
        ).status_code
        == 200
    )

    async def kept() -> list[str]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block.content).where(Block.author == author.handle)
            )
            return [str(c) for c in rows]

    assert any("留给大家的话" in c for c in client.portal.call(kept))
    closed = client.get(
        "/closed-accounts",
        params={"handles": f"{author.handle},{reader.handle}"},
        headers=_auth(reader.token),
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["data"]["handles"] == [author.handle]


def test_nobody_closes_someone_elses_account(client):
    owner = sign_in(client, _handle("owner"))
    other = sign_in(client, _handle("other"))

    typed_theirs = _close(
        client, other.token, username=owner.handle, ticket=_ticket(client, other.token)
    )
    via_admin = client.request(
        "DELETE", f"/admin/users/{owner.handle}", headers=_auth(other.token)
    )

    assert typed_theirs.status_code == 422, typed_theirs.text
    assert via_admin.status_code == 403, via_admin.text
    assert _can_sign_in(client, owner.handle)
    assert _can_sign_in(client, other.handle)


def test_backing_out_closes_nothing(client):
    person = sign_in(client, _handle("hesitant"))

    unconfirmed = _close(client, person.token, username=person.handle, ticket=None)
    mistyped = _close(
        client,
        person.token,
        username="someone-else",
        ticket=_ticket(client, person.token),
    )

    assert unconfirmed.status_code == 403, unconfirmed.text
    assert mistyped.status_code == 422, mistyped.text
    assert _can_sign_in(client, person.handle)
    assert client.get("/users/me", headers=_auth(person.token)).status_code == 200
