"""The mail client's pieces that need no server.

The round trip over real IMAP and SMTP is `scripts/check_mail_server.py`, run by
hand against a test server: CI has none, and a test that skips there fails.
"""

import imaplib

import pytest

from app.core.errors import ValidationError
from app.domain.integration import mail
from app.domain.integration.mail import IntegrationError, MailSettings


def test_a_chinese_keyword_travels_as_one_utf8_literal():
    parts, literal = mail._criteria(None, None, "预算", None)
    assert parts == ["SUBJECT"] and literal == "预算".encode()
    with pytest.raises(ValidationError):
        mail._criteria("预算", None, "报告", None)


def test_html_mail_is_read_as_text_without_its_scripts():
    text = mail.html_to_text(
        "<html><style>p{}</style><p>第一段</p><script>x()</script><div>第二段</div>"
    )
    assert "第一段" in text and "第二段" in text and "x()" not in text


class _Box:
    def __init__(self, rows):
        self.rows = rows

    def list(self):
        return "OK", self.rows


def test_the_drafts_folder_is_found_by_its_flag_then_by_its_name():
    flagged = _Box([b'(\\HasNoChildren \\Drafts) "/" "&g0l6Pw-"', b'() "/" INBOX'])
    assert mail._folder(flagged, "\\drafts", mail.DRAFT_FOLDERS) == "&g0l6Pw-"
    named = _Box([b'(\\HasNoChildren) "." "INBOX.Drafts"', b'() "." INBOX'])
    assert mail._folder(named, "\\drafts", mail.DRAFT_FOLDERS) == "INBOX.Drafts"
    assert mail._folder(_Box([b'() "/" INBOX']), "\\drafts", mail.DRAFT_FOLDERS) is None


def test_a_refused_login_is_an_authorization_problem(monkeypatch):
    class Refusing:
        def __init__(self, *a, **k):
            pass

        def login(self, user, password):
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Refusing)
    with pytest.raises(IntegrationError) as caught:
        mail.search(MailSettings("h", 993, "h", 465, "a@x", "old", "ssl"))
    assert caught.value.kind == "auth_failed"
