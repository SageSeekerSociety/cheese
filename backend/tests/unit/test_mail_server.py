"""The mail client against the protocol itself.

The last test talks to a real IMAP/SMTP server when one is named in
``CHEESE_TEST_MAIL_SERVER`` (``host:imap_port:smtp_port``, two accounts
``alice@local.test`` / ``bob@local.test`` with password ``secret`` — GreenMail's
standalone image serves exactly that). The rest need no server.
"""

import imaplib
import os

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


SERVER = os.environ.get("CHEESE_TEST_MAIL_SERVER")


@pytest.mark.skipif(not SERVER, reason="no test mail server named")
def test_read_draft_and_send_over_real_imap_and_smtp():
    host, imap_port, smtp_port = (SERVER or "::").split(":")

    def box(user):
        return MailSettings(
            host, int(imap_port), host, int(smtp_port), user, "secret", "plain"
        )

    alice, bob = box("alice@local.test"), box("bob@local.test")
    subject = f"季度预算-{os.getpid()}"
    seed = mail.compose(
        sender="bob@local.test",
        to=["alice@local.test"],
        cc=[],
        subject=subject,
        body="请核对第 3 行。",
        attachments=[("预算表.xlsx", b"PK-bytes")],
        in_reply_to=None,
    )
    assert mail.send(bob, seed) == []

    [hit] = [m for m in mail.search(alice, subject=subject)]
    message = mail.read(alice, hit["uid"])
    assert message["body"].strip() == "请核对第 3 行。"
    assert message["attachments"][0]["filename"] == "预算表.xlsx"
    assert mail.attachment(alice, hit["uid"], 0)[2] == b"PK-bytes"

    reply = mail.compose(
        sender="alice@local.test",
        to=["bob@local.test"],
        cc=[],
        subject=f"Re: {subject}",
        body="第 3 行应为 11 万。",
        attachments=[],
        in_reply_to=message["message_id"],
    )
    folder = mail.append_draft(alice, reply)
    assert mail.find_by_message_id(alice, folder, reply["Message-ID"])
    assert not mail.search(bob, subject=f"Re: {subject}"), (
        "a draft reached the recipient"
    )

    assert mail.send(alice, reply) == []
    assert [m["subject"] for m in mail.search(bob, subject=f"Re: {subject}")] == [
        f"Re: {subject}"
    ]
    assert mail.remove_by_message_id(alice, folder, reply["Message-ID"])
    assert not mail.find_by_message_id(alice, folder, reply["Message-ID"])
