"""A browser trusted after two-step verification skips it for 30 days.

Everything goes through the API a browser uses. The trust cookie is put in the
client's jar by hand, for the reason test_sign_in_sessions.py gives for the
refresh cookie: its path is the one the browser sees through the gateway.
"""

import re
import time
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.domain.user.models import UserTrustedDevice
from tests.integration.conftest import CreatedUser, UserCreator
from tests.support.outbox import Outbox, install_outbox

TRUST = "cheese_trusted_device"


def _cookie(resp, name: str) -> str | None:
    jar: SimpleCookie = SimpleCookie()
    for header in resp.headers.get_list("set-cookie"):
        jar.load(header)
    morsel = jar.get(name)
    return morsel.value if morsel is not None and morsel.value else None


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _enable_2fa(client: TestClient, user: CreatedUser) -> pyotp.TOTP:
    sudo = client.post(
        "/users/auth/sudo",
        headers=_headers(user.token),
        json={
            "method": "password",
            "credentials": {"password": user.password},
            "purpose": "2fa:enable",
        },
    )
    assert sudo.status_code == 200, sudo.text
    init = client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=_headers(user.token),
        json={"sudoTicket": sudo.json()["data"]["sudoTicket"]},
    )
    secret = init.json()["data"]["secret"]
    confirm = client.post(
        f"/users/{user.user_id}/2fa/enable",
        headers=_headers(user.token),
        json={"secret": secret, "code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200, confirm.text
    return pyotp.TOTP(secret)


class Browser:
    """One browser: its own trust cookie, set in the client's jar before each
    request it makes."""

    def __init__(self, client: TestClient, user: CreatedUser, totp: pyotp.TOTP):
        self.client = client
        self.user = user
        self.totp = totp
        self.trust: str | None = None

    def _use(self) -> None:
        self.client.cookies.delete(TRUST)
        if self.trust:
            self.client.cookies.set(TRUST, self.trust)

    def password(self, user: CreatedUser | None = None):
        user = user or self.user
        self._use()
        resp = self.client.post(
            "/users/auth/login",
            json={"username": user.username, "password": user.password},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    def second_step(self, temp_token: str, *, trust: bool, totp=None):
        self._use()
        resp = self.client.post(
            "/users/auth/verify-2fa",
            json={
                "temp_token": temp_token,
                "code": (totp or self.totp).now(),
                "trust_device": trust,
            },
        )
        assert resp.status_code == 200, resp.text
        granted = _cookie(resp, TRUST)
        if granted:
            self.trust = granted
        return resp

    def sign_in(self, *, trust: bool = False) -> str:
        """Sign in all the way; returns the access token."""
        data = self.password()
        if data["requires2FA"]:
            data = self.second_step(data["tempToken"], trust=trust).json()["data"]
        return data["accessToken"]

    def asks_for_2fa(self, user: CreatedUser | None = None) -> bool:
        return self.password(user)["requires2FA"]


@pytest.fixture
def user(user_client: UserCreator, api_client: TestClient) -> CreatedUser:
    created = user_client.create_user()
    created.token = user_client.login(api_client, created.username, created.password)
    return created


@pytest.fixture
def totp(api_client: TestClient, user: CreatedUser) -> pyotp.TOTP:
    return _enable_2fa(api_client, user)


@pytest.fixture
def browser(api_client: TestClient, user: CreatedUser, totp: pyotp.TOTP) -> Browser:
    return Browser(api_client, user, totp)


def _sessions(client: TestClient, token: str) -> list[dict]:
    resp = client.get("/users/me/sessions", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["sessions"]


def test_the_trust_cookie_is_http_only_and_sent_to_the_sign_in_routes_only(
    browser: Browser,
):
    temp = browser.password()["tempToken"]
    resp = browser.second_step(temp, trust=True)

    jar: SimpleCookie = SimpleCookie()
    for header in resp.headers.get_list("set-cookie"):
        jar.load(header)
    morsel = jar[TRUST]
    assert morsel["path"] == "/api/users/auth"
    assert morsel["httponly"]
    assert morsel["samesite"].lower() == "lax"
    assert abs(int(morsel["max-age"]) - 30 * 24 * 3600) <= 5
    assert morsel.value not in resp.text


def test_without_the_box_ticked_the_next_sign_in_still_asks(browser: Browser):
    temp = browser.password()["tempToken"]
    resp = browser.second_step(temp, trust=False)

    assert _cookie(resp, TRUST) is None
    assert browser.asks_for_2fa()


def test_with_the_box_ticked_the_next_password_sign_in_skips_the_second_step(
    browser: Browser,
):
    browser.sign_in(trust=True)

    data = browser.password()

    assert data["requires2FA"] is False
    here = next(
        s for s in _sessions(browser.client, data["accessToken"]) if s["current"]
    )
    assert here["loginMethod"] == "password"
    assert here["trusted"] is True


def test_the_session_that_ticked_the_box_is_listed_as_trusted(browser: Browser):
    plain = browser.sign_in()
    trusted = browser.sign_in(trust=True)

    listed = {s["current"]: s["trusted"] for s in _sessions(browser.client, trusted)}
    assert listed == {True: True, False: False}
    assert all(
        not s["trusted"] for s in _sessions(browser.client, plain) if s["current"]
    )


def test_a_trusted_browser_skips_the_second_step_after_an_email_code(
    browser: Browser, monkeypatch
):
    outbox: Outbox = install_outbox(monkeypatch)
    browser.sign_in(trust=True)

    browser.client.post("/users/auth/email-code", json={"email": browser.user.email})
    outbox.wait_for(1)
    browser._use()
    resp = browser.client.post(
        "/users/auth/email-code/verify",
        json={"email": browser.user.email, "code": outbox.code()},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["requires2FA"] is False
    here = next(
        s for s in _sessions(browser.client, data["accessToken"]) if s["current"]
    )
    assert here["loginMethod"] == "email_code"


def test_a_trust_for_another_account_is_ignored(
    browser: Browser, api_client: TestClient, user_client: UserCreator
):
    other = user_client.create_user()
    other.token = user_client.login(api_client, other.username, other.password)
    _enable_2fa(api_client, other)
    browser.sign_in(trust=True)

    assert browser.asks_for_2fa(other)
    # And the owner's trust still holds.
    assert not browser.asks_for_2fa()


def test_a_made_up_cookie_is_ignored(browser: Browser):
    browser.trust = "not-a-token-the-server-issued"

    assert browser.asks_for_2fa()


def _expire_trusts(db_session, _portal, user_id: int, at: datetime) -> None:
    async def expire() -> None:
        await db_session.execute(
            update(UserTrustedDevice)
            .where(UserTrustedDevice.user_id == user_id)
            .values(expires_at=at)
        )
        await db_session.flush()

    _portal.call(expire)


def test_an_expired_trust_is_refused(browser: Browser, db_session, _portal):
    browser.sign_in(trust=True)
    _expire_trusts(
        db_session,
        _portal,
        browser.user.user_id,
        datetime.now(UTC) - timedelta(seconds=1),
    )

    assert browser.asks_for_2fa()


def test_using_a_trust_does_not_extend_it(browser: Browser, db_session, _portal):
    browser.sign_in(trust=True)
    _expire_trusts(
        db_session,
        _portal,
        browser.user.user_id,
        datetime.now(UTC) + timedelta(seconds=2),
    )
    assert not browser.asks_for_2fa()

    time.sleep(2.2)

    assert browser.asks_for_2fa()


# --- Each way a trust ends -------------------------------------------------


def _password_ticket(client: TestClient, user: CreatedUser, token: str, purpose: str):
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


def test_changing_the_password_ends_every_trust(browser: Browser):
    access = browser.sign_in(trust=True)
    client, user = browser.client, browser.user

    resp = client.patch(
        f"/users/{user.user_id}/password",
        headers=_headers(access),
        json={
            "password": "a-brand-new-password-1",
            "sudoTicket": _password_ticket(client, user, access, "password:change"),
        },
    )
    assert resp.status_code == 200, resp.text
    user.password = "a-brand-new-password-1"

    assert browser.asks_for_2fa()


def test_resetting_a_forgotten_password_ends_every_trust(browser: Browser, monkeypatch):
    outbox: Outbox = install_outbox(monkeypatch)
    browser.sign_in(trust=True)
    client, user = browser.client, browser.user

    client.post("/users/recover/password/request", json={"email": user.email})
    outbox.wait_for(1)
    match = re.search(r"token=([\w.-]+)", outbox.sent[-1]["body_text"])
    assert match, outbox.sent[-1]
    resp = client.post(
        "/users/recover/password/verify",
        json={"token": match.group(1), "password": "a-brand-new-password-2"},
    )
    assert resp.status_code == 200, resp.text
    user.password = "a-brand-new-password-2"

    assert browser.asks_for_2fa()


def test_turning_2fa_off_and_on_again_ends_every_trust(browser: Browser):
    access = browser.sign_in(trust=True)
    client, user = browser.client, browser.user
    off = client.post(
        f"/users/{user.user_id}/2fa/disable",
        headers=_headers(access),
        json={"sudoTicket": _password_ticket(client, user, access, "2fa:disable")},
    )
    assert off.status_code == 200, off.text
    user.token = access
    browser.totp = _enable_2fa(client, user)

    assert browser.asks_for_2fa()


def test_signing_the_device_out_from_the_list_ends_its_trust(
    browser: Browser, api_client: TestClient
):
    trusted_access = browser.sign_in(trust=True)
    trusted_id = next(
        s["id"] for s in _sessions(api_client, trusted_access) if s["current"]
    )
    elsewhere = Browser(api_client, browser.user, browser.totp)
    elsewhere_access = elsewhere.sign_in()

    resp = api_client.delete(
        f"/users/me/sessions/{trusted_id}", headers=_headers(elsewhere_access)
    )
    assert resp.status_code == 200, resp.text

    assert browser.asks_for_2fa()


def test_signing_out_other_devices_keeps_only_this_browser_trusted(
    browser: Browser, api_client: TestClient
):
    other_browser = Browser(api_client, browser.user, browser.totp)
    other_browser.sign_in(trust=True)
    here = browser.sign_in(trust=True)

    resp = api_client.delete("/users/me/sessions", headers=_headers(here))
    assert resp.status_code == 200, resp.text

    assert other_browser.asks_for_2fa()
    assert not browser.asks_for_2fa()
