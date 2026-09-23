"""A mail that the primary SMTP account cannot send goes out through the
fallback account, when one is configured."""

import pytest

from app.core import email as email_module
from app.core.config import settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stand-in SMTP servers: hosts listed in ``down`` refuse, others accept
    and record (host, From, To)."""
    state: dict = {"down": set(), "sent": []}

    async def fake_send(msg, *, hostname, **_kwargs):
        if hostname in state["down"]:
            raise OSError(f"{hostname} unavailable")
        state["sent"].append((hostname, msg["From"], msg["To"]))

    monkeypatch.setattr(email_module.aiosmtplib, "send", fake_send)
    monkeypatch.setattr(settings, "email_smtp_host", "primary.example")
    monkeypatch.setattr(settings, "email_from_address", "noreply@primary.example")
    return state


@pytest.fixture
def fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "email_fallback_smtp_host", "fallback.example")
    monkeypatch.setattr(settings, "email_fallback_smtp_port", 25)
    monkeypatch.setattr(settings, "email_fallback_smtp_username", "fb-user")
    monkeypatch.setattr(settings, "email_fallback_smtp_password", "fb-pass")
    monkeypatch.setattr(
        settings, "email_fallback_from_address", "someone@fallback.example"
    )


async def _send() -> bool:
    return await email_module.get_email_sender().send(
        to="user@example.com", subject="code", body_html="<p>123456</p>"
    )


async def test_the_primary_sends_when_it_works(smtp, fallback):
    assert await _send() is True
    assert smtp["sent"] == [
        ("primary.example", "noreply@primary.example", "user@example.com")
    ]


async def test_a_failed_primary_hands_the_mail_to_the_fallback(smtp, fallback):
    smtp["down"].add("primary.example")

    assert await _send() is True
    assert smtp["sent"] == [
        ("fallback.example", "someone@fallback.example", "user@example.com")
    ]


async def test_both_failing_reports_failure(smtp, fallback):
    smtp["down"].update({"primary.example", "fallback.example"})

    assert await _send() is False


async def test_without_a_fallback_a_failure_is_final(smtp, monkeypatch):
    monkeypatch.setattr(settings, "email_fallback_smtp_host", "")
    smtp["down"].add("primary.example")

    assert await _send() is False
    assert smtp["sent"] == []
