"""A mail that the primary SMTP account cannot send goes out through the
fallback account, when one is configured."""

from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.utils import parseaddr

import pytest

from app.core import email as email_module
from app.core.config import settings

pytestmark = pytest.mark.anyio


def _shown(header: str) -> tuple[str, str]:
    """What a reader sees in a sender header: the name, and the address under it.

    Our name is Chinese, so the header carries it encoded; decoding it here is
    what a mail client does before showing either half.
    """
    return parseaddr(str(make_header(decode_header(header))))


def _sender(msg) -> tuple[str, str]:
    return _shown(msg["From"])


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stand-in SMTP servers: hosts listed in ``down`` refuse, others accept
    and record (host, From, To)."""
    state: dict = {"down": set(), "sent": []}

    async def fake_send(msg, *, hostname, **_kwargs):
        if hostname in state["down"]:
            raise OSError(f"{hostname} unavailable")
        state["sent"].append((hostname, *_sender(msg), msg["To"]))

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
        ("primary.example", "知是", "noreply@primary.example", "user@example.com")
    ]


async def test_a_failed_primary_hands_the_mail_to_the_fallback(smtp, fallback):
    smtp["down"].add("primary.example")

    assert await _send() is True
    assert smtp["sent"] == [
        ("fallback.example", "知是", "someone@fallback.example", "user@example.com")
    ]


async def test_both_failing_reports_failure(smtp, fallback):
    smtp["down"].update({"primary.example", "fallback.example"})

    assert await _send() is False


async def test_without_a_fallback_a_failure_is_final(smtp, monkeypatch):
    monkeypatch.setattr(settings, "email_fallback_smtp_host", "")
    smtp["down"].add("primary.example")

    assert await _send() is False
    assert smtp["sent"] == []


def test_the_reader_sees_the_platform_not_the_mailbox():
    """The configured address is a mailbox, not a signature.

    Until 2026-11-15 the header carried the bare address, so every letter showed
    whatever name that mailbox has — the ones users asked about arrived signed
    by the person whose account the platform happens to send through.
    """
    assert _shown(email_module.from_header("noreply@okcheese.com")) == (
        "知是",
        "noreply@okcheese.com",
    )


def test_the_envelope_still_carries_the_configured_address():
    """The SMTP ``MAIL FROM`` is read out of this header, so the name must not
    take the address's place there: a server that sees a name gets a sender it
    never authorized."""
    from aiosmtplib.email import extract_sender

    message = MIMEMultipart("alternative")
    message["From"] = email_module.from_header("someone@fallback.example")
    assert extract_sender(message) == "someone@fallback.example"


def test_a_setting_that_already_names_itself_keeps_one_name():
    """A deployment that wrote ``Alice <a@x>`` into the setting gets ours, not
    two names, and not the whole string as the address."""
    assert _shown(email_module.from_header("Alice <a@x.example>")) == (
        "知是",
        "a@x.example",
    )
