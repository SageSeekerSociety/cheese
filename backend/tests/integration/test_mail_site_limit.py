"""The whole site's hourly allowance of mail that requests can cause."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator
from tests.support.outbox import Outbox, install_outbox

LIMIT = 2


class _Alerts:
    def __init__(self) -> None:
        self.posted: list[str] = []

    async def post(self, text: str) -> None:
        self.posted.append(text)

    def settle(self) -> None:
        """Alerts go out on their own tasks; give any stragglers time to land."""
        time.sleep(0.3)


@pytest.fixture
def outbox(monkeypatch) -> Outbox:
    return install_outbox(monkeypatch)


@pytest.fixture
def alerts(monkeypatch) -> _Alerts:
    from app.core import alerting
    from app.core.config import settings

    box = _Alerts()
    monkeypatch.setattr(settings, "feishu_alert_webhook", "https://alerts.test/hook")
    monkeypatch.setattr(alerting, "_post", box.post)
    monkeypatch.setattr(alerting, "repeated", alerting._Repeats())
    monkeypatch.setattr(alerting, "budget", alerting._Budget())
    return box


@pytest.fixture(autouse=True)
def small_site_limit(monkeypatch):
    """A limit of two, counted under a key no other test shares: the real
    counter is one for the whole Redis, and other tests mail too."""
    import app.domain.user.mail_quota as mail_quota

    monkeypatch.setattr(mail_quota, "MAIL_SITE_HOURLY_LIMIT", LIMIT)
    monkeypatch.setattr(
        mail_quota, "MAIL_SITE_KEY", f"cheese:mail_quota:site-test:{uuid.uuid4().hex}"
    )


def _address() -> str:
    return f"cap-{uuid.uuid4().hex[:12]}@example.com"


def _sign_up_code(client: TestClient, email: str):
    return client.post("/users/verify/email", json={"email": email})


def _sign_in_code(client: TestClient, email: str):
    return client.post("/users/auth/email-code", json={"email": email})


def _recover(client: TestClient, email: str):
    return client.post("/users/recover/password/request", json={"email": email})


def _refused_for_the_site(resp) -> bool:
    return (
        resp.status_code == 503
        and resp.json()["error"]["data"]["reason"] == "mail_limit_reached"
    )


def _fill(client: TestClient, outbox: Outbox) -> None:
    for _ in range(LIMIT):
        assert _sign_up_code(client, _address()).status_code == 200
    assert len(outbox.sent) == LIMIT


def test_past_the_limit_a_sign_up_code_is_refused_and_one_alert_goes_out(
    api_client: TestClient, outbox: Outbox, alerts: _Alerts
):
    _fill(api_client, outbox)

    refused = [_sign_up_code(api_client, _address()) for _ in range(3)]

    assert all(_refused_for_the_site(r) for r in refused), [r.text for r in refused]
    assert len(outbox.sent) == LIMIT
    alerts.settle()
    assert len(alerts.posted) == 1, alerts.posted


def test_requests_that_send_nothing_do_not_spend_the_allowance(
    db_session,
    _portal,
    api_client: TestClient,
    user_client: UserCreator,
    outbox: Outbox,
):
    from sqlalchemy import update

    from app.domain.user.models import User

    placeholder = user_client.create_user()
    placeholder_email = f"oauth-github-{uuid.uuid4().hex[:8]}@placeholder.internal"

    async def make_placeholder() -> None:
        await db_session.execute(
            update(User)
            .where(User.id == placeholder.user_id)
            .values(email=placeholder_email)
        )
        await db_session.flush()

    _portal.call(make_placeholder)

    for _ in range(3):
        assert _sign_in_code(api_client, _address()).status_code == 200
        assert _recover(api_client, _address()).status_code == 200
    assert _sign_in_code(api_client, placeholder_email).status_code == 200
    outbox.settle(0)

    _fill(api_client, outbox)


def test_a_full_allowance_is_invisible_where_accounts_must_not_be_told_apart(
    api_client: TestClient,
    user_client: UserCreator,
    outbox: Outbox,
    alerts: _Alerts,
):
    user = user_client.create_user()
    _fill(api_client, outbox)

    for ask in (_sign_in_code, _recover):
        known = ask(api_client, user.email)
        unknown = ask(api_client, _address())
        assert known.status_code == 200, known.text
        assert known.content == unknown.content

    outbox.settle(LIMIT)
    alerts.settle()
    assert len(alerts.posted) == 1, alerts.posted


def test_a_sudo_code_is_refused_with_the_reason(
    api_client: TestClient, authenticated_user, outbox: Outbox, alerts: _Alerts
):
    _fill(api_client, outbox)

    resp = api_client.post(
        "/users/me/sudo/email-code",
        headers={"Authorization": f"Bearer {authenticated_user.token}"},
    )

    assert _refused_for_the_site(resp), resp.text
    assert len(outbox.sent) == LIMIT


def test_a_refusal_for_one_address_does_not_spend_the_allowance(
    api_client: TestClient, outbox: Outbox
):
    email = _address()
    assert _sign_up_code(api_client, email).status_code == 200
    # Too soon for this address: refused before any mail is sent.
    assert _sign_up_code(api_client, email).status_code == 400

    assert _sign_up_code(api_client, _address()).status_code == 200
    assert len(outbox.sent) == LIMIT


def test_codes_for_an_accounts_first_email_count_against_the_allowance(
    _portal, api_client: TestClient, user_client: UserCreator, outbox: Outbox
):
    from app.api.routes.users import _issue_oauth_state_token

    placeholder = user_client.create_user(
        email=f"oauth-ruc-{uuid.uuid4().hex[:8]}@placeholder.internal"
    )
    token = user_client.login(api_client, placeholder.username, placeholder.password)
    state = _portal.call(
        _issue_oauth_state_token, "ruc", {"id": uuid.uuid4().hex, "email": None}
    )
    _fill(api_client, outbox)

    new_account = api_client.post(
        "/users/auth/oauth/email/code", json={"stateToken": state, "email": _address()}
    )
    adding = api_client.post(
        "/users/me/email/code",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": _address()},
    )

    assert _refused_for_the_site(new_account), new_account.text
    assert _refused_for_the_site(adding), adding.text
    assert len(outbox.sent) == LIMIT
