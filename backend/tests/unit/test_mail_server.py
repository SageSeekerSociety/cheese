"""The mail client's pieces that need no server.

The round trip over real IMAP and SMTP is `scripts/check_mail_server.py`, run by
hand against a test server: CI has none, and a test that skips there fails.
"""

import email
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


def _message(subject: str, sender: str, body: str) -> bytes:
    msg = mail.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "me@qq.com"
    msg.set_content(body)
    return msg.as_bytes()


class _IgnoringBox:
    """An IMAP server that, like QQ Mail on dev (2026-09-27), answers a UTF-8
    SUBJECT search with every message it has."""

    def __init__(self, messages: dict[str, bytes]):
        self.messages = messages
        self.literal = None

    def select(self, *_a, **_k):
        return "OK", [b"3"]

    def logout(self):
        return "BYE", []

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [" ".join(self.messages).encode()]
        uids, spec = args
        parts = []
        for uid in uids.split(","):
            raw = self.messages[uid]
            if "HEADER.FIELDS" in spec:
                head = email.message_from_bytes(raw)
                data = f"Subject: {head['Subject']}\r\nFrom: {head['From']}\r\n\r\n"
                parts += [
                    (f"1 (UID {uid} BODY[HEADER] {{9}}".encode(), data.encode()),
                    b")",
                ]
            elif "HEADER]" in spec:
                parts += [(f"1 (UID {uid} BODY[HEADER] {{9}}".encode(), raw), b")"]
            else:
                parts += [(f"1 (UID {uid} BODY[] {{9}}".encode(), raw), b")"]
        return "OK", parts


def test_a_server_that_ignores_the_subject_does_not_leak_the_inbox(monkeypatch):
    box = _IgnoringBox(
        {
            "1431": _message("648951 is your X verification code", "x@x.com", "code"),
            "1433": _message("芝士测试", "a@ruc.edu.cn", "cheese test"),
            "1434": _message("电子发票", "billing@ruc.edu.cn", "发票"),
        }
    )
    monkeypatch.setattr(mail, "_imap", lambda _settings: box)
    found = mail.search(
        MailSettings("h", 993, "h", 465, "a@x", "p", "ssl"), subject="芝士测试"
    )
    assert [m["uid"] for m in found] == ["1433"]


def test_a_keyword_is_checked_in_the_body_too(monkeypatch):
    box = _IgnoringBox(
        {
            "1": _message("周报", "a@x.com", "本周预算 31.5 万"),
            "2": _message("无关", "b@x.com", "今天天气不错"),
        }
    )
    monkeypatch.setattr(mail, "_imap", lambda _settings: box)
    found = mail.search(
        MailSettings("h", 993, "h", 465, "a@x", "p", "ssl"), query="预算"
    )
    assert [m["uid"] for m in found] == ["1"]
