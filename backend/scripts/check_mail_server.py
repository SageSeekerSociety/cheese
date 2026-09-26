"""Round-trip the mail client over real IMAP and SMTP.

    uv run python -m scripts.check_mail_server 127.0.0.1:13143:13025

Needs two accounts, ``alice@local.test`` and ``bob@local.test``, password
``secret`` — what GreenMail's standalone image serves with
``-Dgreenmail.users=alice:secret@local.test,bob:secret@local.test``. Reads,
fetches an attachment, stores a draft, checks the draft did not reach anyone,
sends it, and removes it from Drafts. Exits non-zero at the first mismatch.
"""

import os
import sys

from app.domain.integration import mail
from app.domain.integration.mail import MailSettings


def main(server: str) -> None:
    host, imap_port, smtp_port = server.split(":")

    def box(user: str) -> MailSettings:
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

    [hit] = mail.search(alice, subject=subject)
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
    assert not mail.search(bob, subject=f"Re: {subject}"), "a draft was delivered"

    assert mail.send(alice, reply) == []
    received = [m["subject"] for m in mail.search(bob, subject=f"Re: {subject}")]
    assert received == [f"Re: {subject}"], received
    assert mail.remove_by_message_id(alice, folder, reply["Message-ID"])
    assert not mail.find_by_message_id(alice, folder, reply["Message-ID"])
    print("ok: read, attachment, draft, send, draft removed")


if __name__ == "__main__":
    main(sys.argv[1])
