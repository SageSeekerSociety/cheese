"""A session that has just proved who is at it gets sudo tickets without being
asked again, for ten minutes.

``/auth/sudo`` with no ``method`` asks for a ticket on the strength of the
session alone; everything here goes through it and through the operations
that spend what it hands out.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.domain.user.models import UserSession
from tests.integration.conftest import CreatedUser, UserCreator
from tests.integration.test_passkey import _SoftAuthenticator
from tests.support.outbox import Outbox, install_outbox

TRUST = "cheese_trusted_device"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _silent(client: TestClient, token: str, purpose: str):
    return client.post(
        "/users/auth/sudo", headers=_headers(token), json={"purpose": purpose}
    )


def _granted(client: TestClient, token: str, purpose: str) -> str:
    resp = _silent(client, token, purpose)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["sudoTicket"]


def _refused(client: TestClient, token: str, purpose: str) -> bool:
    resp = _silent(client, token, purpose)
    if resp.status_code != 403:
        return False
    assert resp.json()["error"]["name"] == "SudoRequiredError", resp.text
    assert "sudoTicket" not in resp.text
    return True


def _password_sudo(client: TestClient, user: CreatedUser, token: str, purpose: str):
    resp = client.post(
        "/users/auth/sudo",
        headers=_headers(token),
        json={
            "method": "password",
            "credentials": {"password": user.password},
            "purpose": purpose,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["sudoTicket"]


def _password_sign_in(client: TestClient, user: CreatedUser) -> dict:
    resp = client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _current_session(client: TestClient, token: str) -> uuid.UUID:
    resp = client.get("/users/me/sessions", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    return uuid.UUID(
        next(s["id"] for s in resp.json()["data"]["sessions"] if s["current"])
    )


class Clock:
    """Moves one session's sudo window, standing in for waiting."""

    def __init__(self, db_session, portal) -> None:
        self._db = db_session
        self._portal = portal

    def _run(self, work):
        return self._portal.call(work)

    def window_ends(self, session_id: uuid.UUID) -> datetime | None:
        async def read():
            return await self._db.scalar(
                select(UserSession.sudo_until).where(UserSession.id == session_id)
            )

        return self._run(read)

    def pass_time(self, session_id: uuid.UUID, by: timedelta) -> None:
        async def shift():
            await self._db.execute(
                update(UserSession)
                .where(UserSession.id == session_id)
                .values(sudo_until=UserSession.sudo_until - by)
            )
            await self._db.flush()

        self._run(shift)


@pytest.fixture
def clock(db_session, _portal) -> Clock:
    return Clock(db_session, _portal)


@pytest.fixture
def user(user_client: UserCreator) -> CreatedUser:
    return user_client.create_user()


def _ticket_changes_password(client, user, token, ticket, new_password):
    return client.patch(
        f"/users/{user.user_id}/password",
        headers=_headers(token),
        json={"password": new_password, "sudoTicket": ticket},
    )


# --- The window after a verification ---------------------------------------


def test_a_verification_lets_a_second_operation_through_without_asking(
    api_client: TestClient, user: CreatedUser, clock: Clock
):
    token = _password_sign_in(api_client, user)["accessToken"]
    here = _current_session(api_client, token)
    clock.pass_time(here, timedelta(minutes=11))
    assert _refused(api_client, token, "password:change")

    _password_sudo(api_client, user, token, "2fa:enable")
    ticket = _granted(api_client, token, "password:change")

    resp = _ticket_changes_password(api_client, user, token, ticket, "fresh-password-1")
    assert resp.status_code == 200, resp.text


def test_the_window_lasts_ten_minutes(
    api_client: TestClient, user: CreatedUser, clock: Clock
):
    token = _password_sign_in(api_client, user)["accessToken"]
    here = _current_session(api_client, token)
    clock.pass_time(here, timedelta(minutes=11))
    _password_sudo(api_client, user, token, "2fa:enable")

    ends = clock.window_ends(here)
    assert ends is not None
    left = ends - datetime.now(UTC)
    assert timedelta(minutes=9, seconds=50) < left <= timedelta(minutes=10)

    clock.pass_time(here, timedelta(minutes=9, seconds=50))
    assert _granted(api_client, token, "passkey:add")

    clock.pass_time(here, timedelta(seconds=15))
    assert _refused(api_client, token, "passkey:add")


def test_a_ticket_from_the_window_is_spent_once_and_only_on_its_purpose(
    api_client: TestClient, user: CreatedUser
):
    token = _password_sign_in(api_client, user)["accessToken"]

    other_purpose = _granted(api_client, token, "2fa:disable")
    refused = _ticket_changes_password(
        api_client, user, token, other_purpose, "fresh-password-1"
    )
    assert refused.status_code == 403, refused.text

    ticket = _granted(api_client, token, "password:change")
    first = _ticket_changes_password(
        api_client, user, token, ticket, "fresh-password-2"
    )
    assert first.status_code == 200, first.text
    replay = _ticket_changes_password(
        api_client, user, token, ticket, "fresh-password-3"
    )
    assert replay.status_code == 403, replay.text


def test_revoking_the_session_ends_its_window_and_no_other(
    api_client: TestClient, user: CreatedUser
):
    doomed = _password_sign_in(api_client, user)["accessToken"]
    survivor = _password_sign_in(api_client, user)["accessToken"]
    doomed_id = _current_session(api_client, doomed)

    resp = api_client.delete(
        f"/users/me/sessions/{doomed_id}", headers=_headers(survivor)
    )
    assert resp.status_code == 200, resp.text

    # Its access token still lasts out its few minutes; the window does not.
    assert _refused(api_client, doomed, "password:change")
    assert _granted(api_client, survivor, "password:change")


def test_a_silent_request_names_what_it_is_for(api_client: TestClient, user):
    token = _password_sign_in(api_client, user)["accessToken"]

    resp = api_client.post("/users/auth/sudo", headers=_headers(token), json={})

    assert resp.status_code == 400, resp.text
    assert "sudoTicket" not in resp.text


# --- Which sign-ins open it -------------------------------------------------


def test_a_password_sign_in_opens_the_window(api_client: TestClient, user):
    token = _password_sign_in(api_client, user)["accessToken"]

    assert _granted(api_client, token, "password:change")


def _enable_2fa(client: TestClient, user: CreatedUser) -> pyotp.TOTP:
    token = _password_sign_in(client, user)["accessToken"]
    init = client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=_headers(token),
        json={"sudoTicket": _password_sudo(client, user, token, "2fa:enable")},
    )
    secret = init.json()["data"]["secret"]
    confirm = client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=_headers(token),
        json={"secret": secret, "code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200, confirm.text
    return pyotp.TOTP(secret)


def _second_step(client: TestClient, temp_token: str, totp, *, trust: bool = False):
    resp = client.post(
        "/users/auth/verify-2fa",
        json={"temp_token": temp_token, "code": totp.now(), "trust_device": trust},
    )
    assert resp.status_code == 200, resp.text
    return resp


def test_a_sign_in_that_passed_2fa_opens_the_window(api_client: TestClient, user):
    totp = _enable_2fa(api_client, user)
    first = _password_sign_in(api_client, user)

    token = _second_step(api_client, first["tempToken"], totp).json()["data"][
        "accessToken"
    ]

    assert _granted(api_client, token, "2fa:disable")


def test_a_passkey_sign_in_opens_the_window(api_client: TestClient, user):
    token = _password_sign_in(api_client, user)["accessToken"]
    key = _SoftAuthenticator()
    options = api_client.post(
        f"/users/{user.user_id}/passkeys/options",
        headers=_headers(token),
        json={"sudoTicket": _password_sudo(api_client, user, token, "passkey:add")},
    )
    registered = api_client.post(
        f"/users/{user.user_id}/passkeys",
        headers=_headers(token),
        json={"response": key.register(options.json()["data"]["options"]["challenge"])},
    )
    assert registered.status_code == 200, registered.text
    challenge = api_client.post("/users/auth/passkey/options", json={}).json()["data"][
        "options"
    ]["challenge"]

    signed_in = api_client.post(
        "/users/auth/passkey/verify",
        json={"response": key.assertion(challenge, verified_user=True)},
    )
    assert signed_in.status_code == 200, signed_in.text

    assert _granted(api_client, signed_in.json()["data"]["accessToken"], "passkey:add")


def _email_code_sign_in(client: TestClient, outbox: Outbox, user: CreatedUser):
    before = len(outbox.sent)
    client.post("/users/auth/email-code", json={"email": user.email})
    outbox.wait_for(before + 1)
    resp = client.post(
        "/users/auth/email-code/verify",
        json={"email": user.email, "code": outbox.code()},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_an_email_code_sign_in_without_2fa_opens_the_window(
    api_client: TestClient, user, monkeypatch
):
    outbox = install_outbox(monkeypatch)

    token = _email_code_sign_in(api_client, outbox, user)["accessToken"]

    assert _granted(api_client, token, "password:change")


def test_a_trusted_browser_email_code_sign_in_to_a_2fa_account_does_not(
    api_client: TestClient, user, monkeypatch
):
    """Sudo never takes a mailed code from an account with 2FA, so the mailbox
    and this browser together must not be enough to switch 2FA off."""
    outbox = install_outbox(monkeypatch)
    totp = _enable_2fa(api_client, user)
    first = _password_sign_in(api_client, user)
    trusted = _second_step(api_client, first["tempToken"], totp, trust=True)
    api_client.cookies.set(TRUST, trusted.cookies[TRUST])

    data = _email_code_sign_in(api_client, outbox, user)

    assert data["requires2FA"] is False
    assert _refused(api_client, data["accessToken"], "2fa:disable")


def test_a_trusted_browser_password_sign_in_opens_the_window(
    api_client: TestClient, user
):
    totp = _enable_2fa(api_client, user)
    first = _password_sign_in(api_client, user)
    trusted = _second_step(api_client, first["tempToken"], totp, trust=True)
    api_client.cookies.set(TRUST, trusted.cookies[TRUST])

    data = _password_sign_in(api_client, user)

    assert data["requires2FA"] is False
    assert _granted(api_client, data["accessToken"], "2fa:disable")
