"""IMAP and SMTP, the two protocols every mailbox provider speaks.

Blocking stdlib clients, so every public function here runs on a worker thread
(``asyncio.to_thread`` in the service). Failures come back as
``IntegrationError`` with a kind the caller can act on: the password no longer
works, the server cannot be reached, or the server refused the operation.
"""

from __future__ import annotations

import email
import email.header
import email.policy
import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parsedate_to_datetime
from html.parser import HTMLParser

from app.core.errors import BaseError, ValidationError
from app.core.sentences import say

TIMEOUT = 30
DRAFT_FOLDERS = ("Drafts", "草稿箱", "[Gmail]/Drafts", "INBOX.Drafts", "Draft")
SENT_FOLDERS = ("Sent", "Sent Messages", "已发送", "[Gmail]/Sent Mail", "INBOX.Sent")


class IntegrationError(BaseError):
    """A mailbox or Feishu call that did not do what was asked, and why."""

    STATUS = {"auth_failed": 401, "forbidden": 403, "not_found": 404}

    def __init__(self, kind: str, message: str, reply: str = "") -> None:
        super().__init__(self.STATUS.get(kind, 502), message, {"kind": kind})
        self.kind = kind
        #: What the service itself answered, in its own words, without ours.
        self.reply = reply


@dataclass(frozen=True)
class MailSettings:
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    username: str
    password: str
    #: ssl (implicit TLS) | starttls | plain (only for a local test server)
    security: str = "ssl"


def _auth_error(exc: Exception) -> IntegrationError:
    return IntegrationError(
        "auth_failed",
        say("mailLoginRejected", error=str(exc)),
    )


def _unreachable(exc: Exception, host: str) -> IntegrationError:
    return IntegrationError(
        "unreachable", say("mailServerUnreachable", host=host, error=str(exc))
    )


def _imap(settings: MailSettings) -> imaplib.IMAP4:
    try:
        if settings.security == "ssl":
            box: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                settings.imap_host,
                settings.imap_port,
                ssl_context=ssl.create_default_context(),
                timeout=TIMEOUT,
            )
        else:
            box = imaplib.IMAP4(settings.imap_host, settings.imap_port, timeout=TIMEOUT)
            if settings.security == "starttls":
                box.starttls(ssl_context=ssl.create_default_context())
    except (OSError, imaplib.IMAP4.error) as exc:
        raise _unreachable(exc, settings.imap_host) from exc
    try:
        box.login(settings.username, settings.password)
    except imaplib.IMAP4.error as exc:
        raise _auth_error(exc) from exc
    if "ID" in box.capabilities:
        # 163/126 refuse SELECT as an "Unsafe Login" from a client that has not
        # said who it is (RFC 2971).
        imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
        try:
            box._simple_command("ID", '("name" "cheese" "version" "1")')  # type: ignore[attr-defined]
        except imaplib.IMAP4.error:
            pass
    return box


def _logout(box: imaplib.IMAP4) -> None:
    try:
        box.logout()
    except (OSError, imaplib.IMAP4.error):
        pass


def check(settings: MailSettings) -> None:
    """Log in to both sides, so a wrong SMTP password shows up now, not at send."""
    _logout(_imap(settings))
    smtp = _smtp(settings)
    try:
        smtp.quit()
    except smtplib.SMTPException:
        pass


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _select(box: imaplib.IMAP4, folder: str, *, readonly: bool = True) -> None:
    status, data = box.select(_quote(folder), readonly=readonly)
    if status != "OK":
        raise IntegrationError(
            "not_found", say("mailFolderNotFound", folder=folder, reply=str(data))
        )


def _decode(value: str | None) -> str:
    if not value:
        return ""
    return str(email.header.make_header(email.header.decode_header(value)))


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def _body(message: EmailMessage) -> tuple[str, str]:
    """(text, how it was obtained)."""
    part = message.get_body(preferencelist=("plain",))
    if part is not None:
        return part.get_content(), "text/plain"
    part = message.get_body(preferencelist=("html",))
    if part is not None:
        return html_to_text(part.get_content()), "由 HTML 转成的文字"
    return "", "这封邮件没有文字正文"


def _attachments(message: EmailMessage) -> list[dict]:
    found = []
    for index, part in enumerate(message.iter_attachments()):
        payload = part.get_payload(decode=True) or b""
        found.append(
            {
                "index": index,
                "filename": _decode(part.get_filename()) or f"附件{index + 1}",
                "content_type": part.get_content_type(),
                "size": len(payload),
            }
        )
    return found


def _criteria(
    query: str | None, sender: str | None, subject: str | None, since: date | None
) -> tuple[list[str], bytes | None]:
    """IMAP search keys, and the one non-ASCII value sent as a literal."""
    parts: list[str] = []
    literal: tuple[str, bytes] | None = None
    for key, value in (("FROM", sender), ("SUBJECT", subject), ("TEXT", query)):
        if not value:
            continue
        if value.isascii():
            parts += [key, _quote(value)]
        elif literal is None:
            literal = (key, value.encode())
        else:
            raise ValidationError(say("mailNonAsciiOneField"))
    if since:
        parts += ["SINCE", since.strftime("%d-%b-%Y")]
    if literal is not None:
        return parts + [literal[0]], literal[1]
    return parts or ["ALL"], None


#: How many of the server's newest candidates are checked here, and of those
#: how many are opened to look for a keyword in the body.
SCAN_LIMIT = 300
BODY_CHECK_LIMIT = 40


def _contains(haystack: str, needle: str) -> bool:
    return needle.casefold() in (haystack or "").casefold()


def _headers(box: imaplib.IMAP4, uids: list[str]) -> dict[str, tuple[str, str]]:
    """{uid: (subject, from)} for many messages in one round trip."""
    if not uids:
        return {}
    status, parts = box.uid(
        "FETCH", ",".join(uids), "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])"
    )
    found: dict[str, tuple[str, str]] = {}
    if status != "OK":
        return found
    for part in parts or []:
        if not isinstance(part, tuple):
            continue
        hit = re.search(rb"UID (\d+)", part[0])
        if not hit:
            continue
        header = email.message_from_bytes(part[1], policy=email.policy.default)
        found[hit.group(1).decode()] = (
            _decode(header.get("Subject")),
            _decode(header.get("From")),
        )
    return found


def _verified(
    box: imaplib.IMAP4,
    candidates: list[str],
    *,
    query: str | None,
    sender: str | None,
    subject: str | None,
    limit: int,
) -> list[str]:
    """The candidates that really match, newest first.

    Seen on dev, 2026-09-27: QQ Mail answered `SEARCH CHARSET UTF-8 SUBJECT
    {芝士测试}` with every message in the inbox, and the teammate was handed
    verification codes and receipts it had not asked for. The server's answer
    is a candidate list; the filter that decides is this one.
    """
    headers = _headers(box, candidates)
    matched: list[str] = []
    opened = 0
    for uid in candidates:
        subj, frm = headers.get(uid, ("", ""))
        if subject and not _contains(subj, subject):
            continue
        if sender and not _contains(frm, sender):
            continue
        if query and not (_contains(subj, query) or _contains(frm, query)):
            if opened >= BODY_CHECK_LIMIT:
                continue
            opened += 1
            text, _how = _body(_fetch(box, uid))
            if not _contains(text, query):
                continue
        matched.append(uid)
        if len(matched) >= limit:
            break
    return matched


def search(
    settings: MailSettings,
    *,
    folder: str = "INBOX",
    query: str | None = None,
    sender: str | None = None,
    subject: str | None = None,
    since: date | None = None,
    limit: int = 20,
) -> list[dict]:
    box = _imap(settings)
    try:
        _select(box, folder)
        criteria, literal = _criteria(query, sender, subject, since)
        try:
            if literal is not None:
                # IMAP carries non-ASCII search text as one UTF-8 literal, which
                # imaplib appends after the last argument.
                box.literal = literal  # type: ignore[attr-defined]
                status, data = box.uid("SEARCH", "CHARSET", "UTF-8", *criteria)
            else:
                status, data = box.uid("SEARCH", *criteria)
        except imaplib.IMAP4.error as exc:
            raise IntegrationError(
                "error", say("mailSearchUnsupported", error=str(exc))
            ) from exc
        if status != "OK":
            raise IntegrationError("error", say("mailSearchFailed", reply=str(data)))
        newest = [u.decode() for u in (data[0] or b"").split()][::-1]
        if query or sender or subject:
            uids = _verified(
                box,
                newest[:SCAN_LIMIT],
                query=query,
                sender=sender,
                subject=subject,
                limit=limit,
            )
        else:
            uids = newest[:limit]
        results = []
        for uid in uids:
            status, parts = box.uid(
                "FETCH", uid, "(BODY.PEEK[HEADER] BODYSTRUCTURE RFC822.SIZE)"
            )
            header_bytes = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            header = email.message_from_bytes(header_bytes, policy=email.policy.default)
            structure = b" ".join(
                p[0] if isinstance(p, tuple) else p for p in parts if p
            ).lower()
            results.append(
                {
                    "uid": uid,
                    "folder": folder,
                    "subject": _decode(header.get("Subject")),
                    "from": _decode(header.get("From")),
                    "to": _decode(header.get("To")),
                    "date": _iso(header.get("Date")),
                    "message_id": header.get("Message-ID", ""),
                    "has_attachments": b'"attachment"' in structure,
                }
            )
        return results
    finally:
        _logout(box)


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value


def _fetch(box: imaplib.IMAP4, uid: str) -> EmailMessage:
    status, parts = box.uid("FETCH", uid, "(BODY.PEEK[])")
    raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
    if status != "OK" or raw is None:
        raise IntegrationError("not_found", say("mailMessageNotFound", uid=uid))
    message = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    return message


def read(settings: MailSettings, uid: str, *, folder: str = "INBOX") -> dict:
    box = _imap(settings)
    try:
        _select(box, folder)
        message = _fetch(box, uid)
        text, how = _body(message)
        return {
            "uid": uid,
            "folder": folder,
            "subject": _decode(message.get("Subject")),
            "from": _decode(message.get("From")),
            "to": _decode(message.get("To")),
            "cc": _decode(message.get("Cc")),
            "date": _iso(message.get("Date")),
            "message_id": message.get("Message-ID", ""),
            "body": text,
            "body_source": how,
            "attachments": _attachments(message),
        }
    finally:
        _logout(box)


def attachment(
    settings: MailSettings, uid: str, index: int, *, folder: str = "INBOX"
) -> tuple[str, str, bytes]:
    box = _imap(settings)
    try:
        _select(box, folder)
        message = _fetch(box, uid)
        for i, part in enumerate(message.iter_attachments()):
            if i == index:
                name = _decode(part.get_filename()) or f"附件{index + 1}"
                payload = part.get_payload(decode=True)
                data = payload if isinstance(payload, bytes) else b""
                return name, part.get_content_type(), data
        raise IntegrationError(
            "not_found", say("mailAttachmentNotFound", number=index + 1)
        )
    finally:
        _logout(box)


def compose(
    *,
    sender: str,
    to: list[str],
    cc: list[str],
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes]],
    in_reply_to: str | None,
    message_id: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = message_id or make_msgid(
        domain=sender.rpartition("@")[2] or None
    )
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    for name, data in attachments:
        maintype, _, subtype = _guess(name).partition("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return message


def _guess(name: str) -> str:
    import mimetypes

    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _folder(box: imaplib.IMAP4, flag: str, names: tuple[str, ...]) -> str | None:
    """The folder the server marks with `flag` (RFC 6154), else a usual name."""
    status, rows = box.list()
    if status != "OK":
        return None
    listed: list[tuple[str, str]] = []
    for row in rows or []:
        text = row.decode() if isinstance(row, bytes) else str(row)
        match = re.match(
            r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', text
        )
        if match is None:
            continue
        name = match.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"')
        listed.append((match.group("flags").lower(), name))
    for flags, name in listed:
        if flag in flags.split():
            return name
    by_lower = {name.lower(): name for _flags, name in listed}
    for want in names:
        if want.lower() in by_lower:
            return by_lower[want.lower()]
    return None


def append_draft(settings: MailSettings, message: EmailMessage) -> str:
    """Store the draft in the mailbox's own Drafts folder; returns that folder."""
    box = _imap(settings)
    try:
        folder = _folder(box, "\\drafts", DRAFT_FOLDERS)
        if folder is None:
            status, data = box.create(_quote("Drafts"))
            if status != "OK":
                raise IntegrationError(
                    "not_found", say("mailNoDraftsFolder", reply=str(data))
                )
            folder = "Drafts"
        status, data = box.append(
            _quote(folder),
            "(\\Draft \\Seen)",
            imaplib.Time2Internaldate(datetime.now().astimezone()),
            message.as_bytes(),
        )
        if status != "OK":
            raise IntegrationError("error", say("mailDraftNotSaved", reply=str(data)))
        return folder
    finally:
        _logout(box)


def remove_by_message_id(settings: MailSettings, folder: str, message_id: str) -> bool:
    box = _imap(settings)
    try:
        _select(box, folder, readonly=False)
        status, data = box.uid("SEARCH", "HEADER", "Message-ID", _quote(message_id))
        uids = (data[0] or b"").split() if status == "OK" else []
        for uid in uids:
            box.uid("STORE", uid.decode(), "+FLAGS", "(\\Deleted)")
        if uids:
            box.expunge()
        return bool(uids)
    finally:
        _logout(box)


def find_by_message_id(settings: MailSettings, folder: str, message_id: str) -> bool:
    box = _imap(settings)
    try:
        _select(box, folder)
        status, data = box.uid("SEARCH", "HEADER", "Message-ID", _quote(message_id))
        return status == "OK" and bool((data[0] or b"").split())
    finally:
        _logout(box)


def _smtp(settings: MailSettings) -> smtplib.SMTP:
    try:
        if settings.security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=TIMEOUT,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT)
            if settings.security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
    except (OSError, smtplib.SMTPException) as exc:
        raise _unreachable(exc, settings.smtp_host) from exc
    try:
        smtp.login(settings.username, settings.password)
    except smtplib.SMTPAuthenticationError as exc:
        raise _auth_error(exc) from exc
    except smtplib.SMTPNotSupportedError:
        pass
    except (OSError, smtplib.SMTPException) as exc:
        raise _unreachable(exc, settings.smtp_host) from exc
    return smtp


def send(settings: MailSettings, message: EmailMessage) -> list[str]:
    """Send exactly this message; returns the recipients the server refused."""
    smtp = _smtp(settings)
    try:
        refused = smtp.send_message(message)
    except smtplib.SMTPRecipientsRefused as exc:
        raise IntegrationError(
            "error",
            say("mailAllRecipientsRefused", recipients=", ".join(exc.recipients)),
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise IntegrationError("error", say("mailSendFailed", error=str(exc))) from exc
    finally:
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            pass
    return sorted(refused)


def save_sent(settings: MailSettings, message: EmailMessage) -> str | None:
    """Best effort: a copy in Sent, for providers that do not keep one themselves."""
    box = _imap(settings)
    try:
        folder = _folder(box, "\\sent", SENT_FOLDERS)
        if folder is None:
            return None
        box.append(
            _quote(folder),
            "(\\Seen)",
            imaplib.Time2Internaldate(datetime.now().astimezone()),
            message.as_bytes(),
        )
        return folder
    finally:
        _logout(box)


def addresses(raw: list[str]) -> list[str]:
    parsed = [addr for _name, addr in getaddresses(raw) if "@" in addr]
    if len(parsed) != len([r for r in raw if r.strip()]):
        raise ValidationError(say("mailRecipientInvalid"))
    return parsed
