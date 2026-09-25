"""Every account holds an email address the platform has verified.

A new account made through a provider proves an address with a code before it
is created; an account still holding a placeholder is told so on sign-in and
adds one; nothing is ever mailed to a placeholder."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.api.routes.users import _issue_oauth_state_token
from app.core.config import settings
from app.core.email import EmailSender, FallbackEmailSender
from app.domain.user.models import User, UserSession
from tests.integration.conftest import CreatedUser, UserCreator
from tests.support.consent import OAUTH_CONSENT_FORM


class Mailbox:
    """What reached the SMTP server."""

    def __init__(self) -> None:
        self.sent: list[tuple[list[str], str]] = []

    def to(self, address: str) -> list[str]:
        return [
            text
            for recipients, text in self.sent
            if address.lower() in (r.lower() for r in recipients)
        ]

    def code_for(self, address: str) -> str:
        mails = self.to(address)
        assert mails, f"no mail reached {address}"
        match = re.search(r"\b(\d{6})\b", mails[-1])
        assert match, mails[-1]
        return match.group(1)

    @property
    def recipients(self) -> list[str]:
        return [r for recipients, _ in self.sent for r in recipients]


@pytest.fixture
def mailbox(monkeypatch) -> Mailbox:
    import aiosmtplib

    box = Mailbox()

    async def deliver(message, **_kwargs):
        recipients = [a.strip() for a in message["To"].split(",")]
        text = "\n".join(
            part.get_payload(decode=True).decode()
            for part in message.walk()
            if part.get_content_maintype() == "text"
        )
        box.sent.append((recipients, text))

    monkeypatch.setattr(aiosmtplib, "send", deliver)
    monkeypatch.setattr(settings, "email_smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "email_from_address", "noreply@example.test")
    monkeypatch.setattr(settings, "email_fallback_smtp_host", "")
    return box


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _wrong(code: str) -> str:
    return str((int(code) + 1) % 1_000_000).zfill(6)


def _q(resp) -> dict:
    assert resp.status_code == 302, f"expected 302, got {resp.status_code}"
    location = resp.headers["location"]
    return {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}


def _state(portal, uid: str, email: str | None = None) -> str:
    return portal.call(
        _issue_oauth_state_token,
        "ruc",
        {
            "id": uid,
            "email": email,
            "name": "Prov User",
            "username": None,
            "preferredUsername": "provuser",
        },
    )


def _send_code(client: TestClient, token: str, email: str):
    return client.post(
        "/users/auth/oauth/email/code", json={"stateToken": token, "email": email}
    )


def _verify(client: TestClient, token: str, email: str, code: str):
    return client.post(
        "/users/auth/oauth/email/verify",
        json={"stateToken": token, "email": email, "code": code},
    )


def _prove(client: TestClient, mailbox: Mailbox, token: str, email: str) -> str:
    """Prove ``email`` for the sign-up behind ``token``; the token to create with."""
    assert _send_code(client, token, email).status_code == 200
    resp = _verify(client, token, email, mailbox.code_for(email))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["stateToken"]


def _create(client: TestClient, token: str, username: str):
    return client.post(
        "/users/oauth/create",
        data={
            **OAUTH_CONSENT_FORM,
            "stateToken": token,
            "username": username,
            "nickname": "prov",
            "passwordMode": "none",
        },
        follow_redirects=False,
    )


def _email_of(db_session, portal, username: str) -> str | None:
    async def lookup():
        return await db_session.scalar(
            select(User.email).where(User.username == username)
        )

    return portal.call(lookup)


def _placeholder_accounts(db_session, portal) -> int:
    async def count():
        return await db_session.scalar(
            select(func.count(User.id)).where(User.email.like("%@placeholder.internal"))
        )

    return portal.call(count)


class TestOAuthSignUpVerifiesAnEmail:
    def test_an_account_is_not_created_without_a_verified_email(
        self, api_client: TestClient, db_session, _portal
    ):
        username = _unique("noemail")
        params = _q(_create(api_client, _state(_portal, _unique("uid")), username))

        assert params["error_code"] == "EMAIL_UNVERIFIED"
        assert _email_of(db_session, _portal, username) is None

    def test_the_provider_email_is_verified_before_the_account_holds_it(
        self, api_client: TestClient, db_session, _portal, mailbox: Mailbox
    ):
        address = f"{_unique('prov')}@school.example.com"
        username = _unique("provmail")
        token = _state(_portal, _unique("uid"), email=address)

        # The provider naming the address does not stand in for proving it.
        assert _q(_create(api_client, token, username))["error_code"] == (
            "EMAIL_UNVERIFIED"
        )

        assert _send_code(api_client, token, address).status_code == 200
        code = mailbox.code_for(address)
        wrong = _verify(api_client, token, address, _wrong(code))
        assert wrong.status_code == 422
        assert wrong.json()["error"]["data"]["reason"] == "invalid_email_code"

        verified = _verify(api_client, token, address, code)
        assert verified.status_code == 200, verified.text
        params = _q(
            _create(api_client, verified.json()["data"]["stateToken"], username)
        )

        assert params["created"] == "true"
        assert _email_of(db_session, _portal, username) == address

    def test_the_person_may_verify_another_address_instead(
        self, api_client: TestClient, db_session, _portal, mailbox: Mailbox
    ):
        chosen = f"{_unique('chosen')}@example.com"
        username = _unique("chosen")
        token = _state(_portal, _unique("uid"), email="provider@school.example.com")

        params = _q(
            _create(api_client, _prove(api_client, mailbox, token, chosen), username)
        )

        assert params["created"] == "true"
        assert _email_of(db_session, _portal, username) == chosen

    def test_without_a_provider_email_the_account_holds_the_verified_one(
        self, api_client: TestClient, db_session, _portal, mailbox: Mailbox
    ):
        address = f"{_unique('none')}@example.com"
        username = _unique("nomail")
        token = _state(_portal, _unique("uid"))

        params = _q(
            _create(api_client, _prove(api_client, mailbox, token, address), username)
        )

        assert params["created"] == "true"
        assert _email_of(db_session, _portal, username) == address
        assert _placeholder_accounts(db_session, _portal) == 0

    def test_a_code_proves_only_the_address_it_was_sent_to(
        self, api_client: TestClient, _portal, mailbox: Mailbox
    ):
        sent_to = f"{_unique('a')}@example.com"
        other = f"{_unique('b')}@example.com"
        token = _state(_portal, _unique("uid"))
        _send_code(api_client, token, sent_to)

        resp = _verify(api_client, token, other, mailbox.code_for(sent_to))

        assert resp.status_code == 422
        assert resp.json()["error"]["data"]["reason"] == "invalid_email_code"

    def test_five_wrong_codes_use_the_code_up(
        self, api_client: TestClient, _portal, mailbox: Mailbox
    ):
        address = f"{_unique('guess')}@example.com"
        token = _state(_portal, _unique("uid"))
        _send_code(api_client, token, address)
        code = mailbox.code_for(address)
        for _ in range(5):
            assert _verify(api_client, token, address, _wrong(code)).status_code == 422

        assert _verify(api_client, token, address, code).status_code == 422

    def test_a_placeholder_address_is_refused(
        self, api_client: TestClient, _portal, mailbox: Mailbox
    ):
        resp = _send_code(
            api_client, _state(_portal, _unique("uid")), "x@placeholder.internal"
        )

        assert resp.status_code == 422
        assert resp.json()["error"]["data"]["reason"] == "invalid_email"
        assert mailbox.sent == []

    def test_an_address_of_another_account_goes_to_that_accounts_check(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        _portal,
        mailbox: Mailbox,
    ):
        owner = user_client.create_user(email=f"{_unique('owner')}@example.com")
        token = _state(_portal, _unique("uid"))
        typed = owner.email.upper()
        _send_code(api_client, token, typed)

        resp = _verify(api_client, token, typed, mailbox.code_for(typed))

        assert resp.status_code == 200, resp.text
        ownership = resp.json()["data"]["ownership"]
        assert ownership["type"] == "password"
        assert ownership["email"] == owner.username
        # Nothing was linked on the address alone, and the sign-up is over.
        bind = api_client.post(
            "/users/oauth/bind",
            data={
                "stateToken": token,
                "username": owner.username,
                "password": owner.password,
            },
            follow_redirects=False,
        )
        assert _q(bind)["error_code"] == "TOKEN_EXPIRED"

        linked = api_client.post(
            "/users/auth/oauth/verify",
            json={"sessionId": ownership["sessionId"], "password": owner.password},
            follow_redirects=False,
        )
        assert _q(linked)["linked"] == "true"


PLACEHOLDER_DOMAINS = ["placeholder.internal", "oauth.ruc.local"]


@pytest.fixture(params=PLACEHOLDER_DOMAINS)
def placeholder_user(request, user_client: UserCreator) -> CreatedUser:
    return user_client.create_user(email=f"{_unique('ph')}@{request.param}")


def _login(client: TestClient, user: CreatedUser):
    resp = client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )
    assert resp.status_code == 200, resp.text
    return resp


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _me(client: TestClient, token: str) -> dict:
    resp = client.get("/users/me", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["user"]


class TestAccountWithoutEmail:
    def test_signing_in_reports_the_missing_email(
        self, api_client: TestClient, placeholder_user: CreatedUser
    ):
        login = _login(api_client, placeholder_user)
        assert login.json()["data"]["user"]["emailMissing"] is True

        refreshed = api_client.post(
            "/users/auth/refresh-token",
            headers={"Cookie": f"cheese_refresh={login.cookies['cheese_refresh']}"},
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["data"]["user"]["emailMissing"] is True
        assert _me(api_client, refreshed.json()["data"]["accessToken"])["emailMissing"]

    def test_the_second_factor_step_reports_it_too(
        self, api_client: TestClient, user_client: UserCreator
    ):
        user = user_client.create_user(email=f"{_unique('ph2fa')}@placeholder.internal")
        token = _login(api_client, user).json()["data"]["accessToken"]
        sudo = api_client.post(
            "/users/auth/sudo",
            headers=_bearer(token),
            json={
                "method": "password",
                "credentials": {"password": user.password},
                "purpose": "2fa:enable",
            },
        )
        init = api_client.post(
            f"/users/{user.user_id}/2fa/enable",
            headers=_bearer(token),
            json={"sudoTicket": sudo.json()["data"]["sudoTicket"]},
        )
        secret = init.json()["data"]["secret"]
        api_client.post(
            f"/users/{user.user_id}/2fa/enable",
            headers=_bearer(token),
            json={"secret": secret, "code": pyotp.TOTP(secret).now()},
        )

        ticket = _login(api_client, user).json()["data"]
        assert ticket["requires2FA"] is True
        done = api_client.post(
            "/users/auth/verify-2fa",
            json={
                "temp_token": ticket["tempToken"],
                "code": pyotp.TOTP(secret).now(),
            },
        )

        assert done.status_code == 200, done.text
        assert done.json()["data"]["user"]["emailMissing"] is True

    def test_an_account_with_its_own_email_is_not_asked(
        self, api_client: TestClient, authenticated_user: CreatedUser
    ):
        assert authenticated_user.token
        assert _me(api_client, authenticated_user.token)["emailMissing"] is False

    def test_adding_a_verified_email_clears_the_requirement(
        self,
        api_client: TestClient,
        placeholder_user: CreatedUser,
        db_session,
        _portal,
        mailbox: Mailbox,
    ):
        token = _login(api_client, placeholder_user).json()["data"]["accessToken"]
        address = f"{_unique('added')}@example.com"
        sent = api_client.post(
            "/users/me/email/code", headers=_bearer(token), json={"email": address}
        )
        assert sent.status_code == 200, sent.text
        code = mailbox.code_for(address)

        wrong = api_client.post(
            "/users/me/email",
            headers=_bearer(token),
            json={"email": address, "code": _wrong(code)},
        )
        assert wrong.status_code == 422
        assert wrong.json()["error"]["data"]["reason"] == "invalid_email_code"
        assert _me(api_client, token)["emailMissing"] is True

        added = api_client.post(
            "/users/me/email",
            headers=_bearer(token),
            json={"email": address, "code": code},
        )
        assert added.status_code == 200, added.text
        assert added.json()["data"]["user"]["emailMissing"] is False
        assert _me(api_client, token)["emailMissing"] is False
        assert _email_of(db_session, _portal, placeholder_user.username) == address

        again = api_client.post(
            "/users/me/email/code",
            headers=_bearer(token),
            json={"email": f"{_unique('again')}@example.com"},
        )
        assert again.status_code == 409
        assert again.json()["error"]["data"]["reason"] == "email_present"

    def test_an_address_held_by_another_account_is_refused(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        placeholder_user: CreatedUser,
        mailbox: Mailbox,
    ):
        holder = user_client.create_user(email=f"{_unique('Holder')}@Example.com")
        token = _login(api_client, placeholder_user).json()["data"]["accessToken"]

        for path, body in (
            ("/users/me/email/code", {"email": holder.email.lower()}),
            ("/users/me/email", {"email": holder.email.lower(), "code": "123456"}),
        ):
            resp = api_client.post(path, headers=_bearer(token), json=body)
            assert resp.status_code == 409, resp.text
            assert resp.json()["error"]["data"]["reason"] == "email_taken"
        assert mailbox.sent == []

    def test_a_placeholder_cannot_be_added(
        self, api_client: TestClient, placeholder_user: CreatedUser, mailbox: Mailbox
    ):
        token = _login(api_client, placeholder_user).json()["data"]["accessToken"]

        resp = api_client.post(
            "/users/me/email/code",
            headers=_bearer(token),
            json={"email": "other@placeholder.internal"},
        )

        assert resp.status_code == 422
        assert mailbox.sent == []


class TestAddingAnEmailNeedsARecentSignIn:
    """A session taken from its owner must not attach an address of its own:
    only a sign-in in the last fifteen minutes may add the first one."""

    def _age_sign_ins(self, db_session, portal, user: CreatedUser, minutes: int):
        async def age():
            await db_session.execute(
                update(UserSession)
                .where(UserSession.user_id == user.user_id)
                .values(created_at=datetime.now(UTC) - timedelta(minutes=minutes))
            )
            await db_session.flush()

        portal.call(age)

    def _send(self, client: TestClient, token: str):
        return client.post(
            "/users/me/email/code",
            headers=_bearer(token),
            json={"email": f"{_unique('fresh')}@example.com"},
        )

    def test_a_recent_sign_in_may_add_one(
        self,
        api_client: TestClient,
        placeholder_user: CreatedUser,
        db_session,
        _portal,
        mailbox: Mailbox,
    ):
        token = _login(api_client, placeholder_user).json()["data"]["accessToken"]
        self._age_sign_ins(db_session, _portal, placeholder_user, 14)

        assert self._send(api_client, token).status_code == 200

    def test_an_older_sign_in_is_refused_even_after_a_refresh(
        self,
        api_client: TestClient,
        placeholder_user: CreatedUser,
        db_session,
        _portal,
        mailbox: Mailbox,
    ):
        login = _login(api_client, placeholder_user)
        self._age_sign_ins(db_session, _portal, placeholder_user, 16)
        refreshed = api_client.post(
            "/users/auth/refresh-token",
            headers={"Cookie": f"cheese_refresh={login.cookies['cheese_refresh']}"},
        )
        assert refreshed.status_code == 200, refreshed.text
        token = refreshed.json()["data"]["accessToken"]

        address = f"{_unique('stale')}@example.com"
        for path, body in (
            ("/users/me/email/code", {"email": address}),
            ("/users/me/email", {"email": address, "code": "123456"}),
        ):
            resp = api_client.post(path, headers=_bearer(token), json=body)
            assert resp.status_code == 403, resp.text
            assert resp.json()["error"]["data"]["reason"] == "reauth_required"
        assert mailbox.sent == []

        # Signing in again opens the window again.
        again = _login(api_client, placeholder_user).json()["data"]["accessToken"]
        assert self._send(api_client, again).status_code == 200


class TestNoMailToPlaceholders:
    @pytest.mark.parametrize("domain", PLACEHOLDER_DOMAINS)
    def test_the_sender_never_hands_a_placeholder_to_smtp(
        self, _portal, mailbox: Mailbox, domain: str
    ):
        placeholder = f"someone@{domain}"
        real = "someone@example.com"

        alone = _portal.call(
            lambda: EmailSender().send(
                to=placeholder, subject="s", body_html="<p>x</p>"
            )
        )
        mixed = _portal.call(
            lambda: EmailSender().send(
                to=[placeholder, real], subject="s", body_html="<p>x</p>"
            )
        )

        assert alone is False
        assert mixed is True
        assert mailbox.recipients == [real]

    def test_the_fallback_account_does_not_send_one_either(
        self, _portal, mailbox: Mailbox
    ):
        sender = FallbackEmailSender(EmailSender(), EmailSender())

        sent = _portal.call(
            lambda: sender.send(
                to="someone@placeholder.internal", subject="s", body_html="<p>x</p>"
            )
        )

        assert sent is False
        assert mailbox.sent == []
