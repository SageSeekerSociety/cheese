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


def _refused_for_the_site(resp) -> bool:
    return (
        resp.status_code == 503
        and resp.json()["error"]["data"]["reason"] == "mail_limit_reached"
    )


def test_past_the_limit_nothing_more_is_sent_and_one_alert_goes_out(
    api_client: TestClient, outbox: Outbox, alerts: _Alerts
):
    for _ in range(LIMIT):
        assert _sign_up_code(api_client, _address()).status_code == 200

    refused = [_sign_up_code(api_client, _address()) for _ in range(3)]

    assert all(_refused_for_the_site(r) for r in refused), [r.text for r in refused]
    assert len(outbox.sent) == LIMIT
    alerts.settle()
    assert len(alerts.posted) == 1, alerts.posted


def test_the_limit_is_shared_by_every_kind_of_mail(
    api_client: TestClient,
    user_client: UserCreator,
    outbox: Outbox,
    alerts: _Alerts,
):
    user = user_client.create_user()
    for _ in range(LIMIT):
        _sign_up_code(api_client, _address())

    recovery = api_client.post(
        "/users/recover/password/request", json={"email": user.email}
    )
    sign_in_known = api_client.post(
        "/users/auth/email-code", json={"email": user.email}
    )
    sign_in_unknown = api_client.post(
        "/users/auth/email-code", json={"email": _address()}
    )

    for resp in (recovery, sign_in_known, sign_in_unknown):
        assert _refused_for_the_site(resp), resp.text
    assert sign_in_known.content == sign_in_unknown.content
    outbox.settle(LIMIT)


def test_a_refusal_for_one_address_does_not_spend_the_site_allowance(
    api_client: TestClient, outbox: Outbox
):
    email = _address()
    assert _sign_up_code(api_client, email).status_code == 200
    # Too soon for this address: refused before the site's count is touched.
    assert _sign_up_code(api_client, email).status_code == 400

    assert _sign_up_code(api_client, _address()).status_code == 200
    assert len(outbox.sent) == LIMIT
