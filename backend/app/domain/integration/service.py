"""Connect, lend and use a person's mailbox or Feishu account.

Who may do what: only the owner manages a connection, names the projects whose
AI teammates may use it, and sends a drafted mail. A teammate in a granted
project may search, read, fetch attachments, write drafts, and read and write
Feishu documents — always as the owner's account, never as the platform's.

The Feishu app those documents are reached through is the platform's, configured
once by a platform administrator (``FeishuApp``, ``/admin/integrations/feishu``).
A connection that carries its own ``app_id`` and ``app_secret`` — made back when
every member created their own app — keeps using those.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import uuid
from datetime import UTC, date, datetime
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.integration import mail
from app.domain.integration.feishu import FeishuClient, FeishuSettings
from app.domain.integration.mail import IntegrationError, MailSettings
from app.domain.integration.models import (
    FEISHU_APP_ROW_ID,
    FeishuApp,
    Integration,
    MailDraft,
)
from app.domain.library import service as library

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def seal(row: Integration, secret: dict) -> None:
    row.secret = encrypt(
        Purpose.INTEGRATION_SECRET, json.dumps(secret), bound_to=str(row.id)
    )


def unseal(row: Integration) -> dict:
    return json.loads(
        decrypt(Purpose.INTEGRATION_SECRET, row.secret, bound_to=str(row.id))
    )


#: What the platform app's secret is sealed to. That row is a singleton with a
#: fixed integer key, so binding to a name says what a ciphertext belongs to
#: where "1" would only say it belongs to some row.
FEISHU_APP_BINDING = "platform-feishu-app"


def seal_app(row: FeishuApp, secret: dict) -> None:
    row.secret = encrypt(
        Purpose.INTEGRATION_SECRET, json.dumps(secret), bound_to=FEISHU_APP_BINDING
    )


def unseal_app(row: FeishuApp) -> dict:
    if not row.secret:
        return {}
    return json.loads(
        decrypt(Purpose.INTEGRATION_SECRET, row.secret, bound_to=FEISHU_APP_BINDING)
    )


def feishu_app_view(row: FeishuApp | None) -> dict:
    """The platform app as the admin page sees it. Never carries the secret.

    ``configured`` is the answer to the only question the page asks first, and
    ``None`` — no administrator has saved one — is an answer, not an error.
    """
    if row is None:
        return {
            "configured": False,
            "app_id": "",
            "domain": "feishu",
            "updated_by": "",
            "updated_at": None,
        }
    return {
        "configured": True,
        "app_id": row.app_id,
        "domain": row.domain,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class FeishuAppService:
    """The one Feishu app the platform holds: read by everybody, written by an
    administrator. Nothing here is per person."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def current(self) -> FeishuApp | None:
        return await self._session.get(FeishuApp, FEISHU_APP_ROW_ID)

    async def save(
        self, *, app_id: str, app_secret: str, domain: str, by: str
    ) -> FeishuApp:
        """Save the password-style update: an empty ``app_secret`` keeps the one
        already stored, so the page can be saved without retyping the secret it
        never shows."""
        if domain not in ("feishu", "lark"):
            raise ValidationError(say("feishuDomainInvalid"))
        row = await self.current()
        if row is None:
            row = FeishuApp(id=FEISHU_APP_ROW_ID, secret="")
        if app_secret:
            seal_app(row, {"app_secret": app_secret})
        elif not row.secret:
            raise ValidationError(say("feishuAppSecretRequired"))
        row.app_id = app_id
        row.domain = domain
        row.updated_by = by
        self._session.add(row)
        await self._session.flush()
        return row


def mail_settings(row: Integration) -> MailSettings:
    config = row.config
    return MailSettings(
        imap_host=config["imap_host"],
        imap_port=int(config["imap_port"]),
        smtp_host=config["smtp_host"],
        smtp_port=int(config["smtp_port"]),
        username=config["username"],
        password=unseal(row)["password"],
        security=config.get("security", "ssl"),
    )


def feishu_settings(row: Integration, app: FeishuApp | None = None) -> FeishuSettings:
    """Which app credentials this connection is used with.

    A connection carrying its own ``app_id`` (and the sealed ``app_secret`` next
    to it) is used with those — it was made when every member created an app, and
    nothing about it is migrated. Every other connection goes through the app the
    platform administrator configured, read here at the moment of the call so a
    rotated secret reaches it; with none configured there is nothing to call with
    and the person is told whose job that is.
    """
    secret = unseal(row)
    own_app_id = row.config.get("app_id")
    if own_app_id:
        app_id = own_app_id
        app_secret = secret.get("app_secret") or ""
        domain = row.config.get("domain", "feishu")
    elif app is not None:
        app_id = app.app_id
        app_secret = unseal_app(app).get("app_secret") or ""
        domain = app.domain
    else:
        raise ValidationError(say("feishuAppNotConfigured"))
    return FeishuSettings(
        app_id=app_id,
        app_secret=app_secret,
        domain=domain,
        user_access_token=secret.get("user_access_token"),
        user_token_expires_at=secret.get("user_token_expires_at"),
        refresh_token=secret.get("refresh_token"),
        folders=list(row.config.get("folders") or []),
    )


async def feishu_settings_for(
    session: AsyncSession, row: Integration
) -> FeishuSettings:
    """``feishu_settings`` with the platform app read from the database.

    The read is skipped for a connection that carries its own credentials: those
    deployments have no platform app at all.
    """
    app = (
        None if row.config.get("app_id") else await FeishuAppService(session).current()
    )
    return feishu_settings(row, app)


def keep_feishu_tokens(row: Integration, settings: FeishuSettings) -> None:
    """A refreshed user token is written back, or the next call refreshes again."""
    secret = unseal(row)
    fresh = {
        **secret,
        "user_access_token": settings.user_access_token,
        "user_token_expires_at": settings.user_token_expires_at,
        "refresh_token": settings.refresh_token,
    }
    if fresh != secret:
        seal(row, fresh)


def public(row: Integration) -> dict:
    config = {k: v for k, v in row.config.items()}
    authorized = False
    if row.provider == "feishu":
        authorized = bool(unseal(row).get("refresh_token"))
    return {
        "id": str(row.id),
        "provider": row.provider,
        "label": row.label,
        "owner_handle": row.owner_handle,
        "config": config,
        "grants": row.grants,
        "status": row.status,
        "last_error": row.last_error,
        "last_checked_at": row.last_checked_at.isoformat()
        if row.last_checked_at
        else None,
        "user_authorized": authorized,
        #: Uses the platform's app rather than credentials of its own.
        "shared_app": row.provider == "feishu" and not row.config.get("app_id"),
    }


def draft_view(row: MailDraft) -> dict:
    return {
        "id": str(row.id),
        "integration_id": str(row.integration_id),
        "project_id": str(row.project_id),
        "topic_id": str(row.topic_id) if row.topic_id else None,
        "created_by": row.created_by,
        "to": row.to,
        "cc": row.cc,
        "subject": row.subject,
        "body": row.body,
        "attachments": row.attachments,
        "in_reply_to": row.in_reply_to,
        "status": row.status,
        "error": row.error,
        "confirmed_by": row.confirmed_by,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


#: RFC 2544's benchmarking range. Nothing public lives there, which is why
#: fake-ip proxies (Clash, mihomo, sing-box) hand it out as placeholders: every
#: outside name resolves into it and the proxy connects by name. Seen on dev,
#: 2026-09-27: imap.qq.com → 198.18.0.198. Such an address says nothing about
#: where the name really points, so the real answer is asked for instead.
FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _real_addresses(host: str) -> list[IPAddress]:
    """The host's addresses from public DNS over HTTPS, past the local proxy."""
    import httpx

    found: list[IPAddress] = []
    for kind in ("A", "AAAA"):
        try:
            answer = httpx.get(
                settings.integration_doh_url,
                params={"name": host, "type": kind},
                timeout=8,
            ).json()
        except Exception:  # noqa: BLE001 — any failure means "not verified"
            continue
        for record in answer.get("Answer") or []:
            if record.get("type") in (1, 28):
                try:
                    found.append(ipaddress.ip_address(record["data"]))
                except (KeyError, ValueError):
                    continue
    return found


def refuse_internal_host(host: str, what: str) -> None:
    """Refuse a host a person named that is this platform's own network.

    `what` names the host in the refusal (「邮件服务器」, 「MCP 服务器」). Blocking:
    it resolves the name, and asks public DNS past a fake-ip proxy's placeholder.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValidationError(say("hostNotFound", what=what, host=host)) from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address in FAKE_IP_RANGE:
            real = _real_addresses(host)
            if not real:
                raise ValidationError(say("hostUnresolvable", what=what, host=host))
            for actual in real:
                _refuse_if_internal(host, actual, what)
            continue
        _refuse_if_internal(host, address, what)


def _refuse_if_internal(host: str, address: IPAddress, what: str) -> None:
    if not address.is_global or address.is_multicast:
        raise ValidationError(say("hostInternal", what=what, host=host))


def guard_mail_hosts(config: dict) -> None:
    """Refuse a mail server that is this platform's own network, or plaintext."""
    if settings.integration_allow_private_hosts:
        return
    if config.get("security") == "plain":
        raise ValidationError(say("mailServerNeedsEncryption"))
    for key in ("imap_host", "smtp_host"):
        refuse_internal_host(str(config.get(key) or ""), say("nounMailServer"))


class IntegrationService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def owned(self, owner_user_id: int) -> list[Integration]:
        return list(
            await self._session.scalars(
                select(Integration)
                .where(Integration.owner_user_id == owner_user_id)
                .order_by(Integration.created_at)
            )
        )

    async def get_owned(
        self, integration_id: uuid.UUID, owner_user_id: int
    ) -> Integration:
        row = await self._session.get(Integration, integration_id)
        if row is None or row.owner_user_id != owner_user_id:
            raise NotFoundError(say("integrationNotFound"))
        return row

    async def granted(self, project_id: uuid.UUID) -> list[Integration]:
        rows = await self._session.scalars(select(Integration))
        return [r for r in rows if str(project_id) in (r.grants or [])]

    async def for_project(
        self, integration_id: uuid.UUID, project_id: uuid.UUID
    ) -> Integration:
        row = await self._session.get(Integration, integration_id)
        if row is None:
            raise NotFoundError(say("integrationNotFound"))
        if str(project_id) not in (row.grants or []):
            raise ForbiddenError(
                say("integrationNotSharedWithProject", owner=row.owner_handle)
            )
        return row

    async def _check(self, row: Integration) -> None:
        row.last_checked_at = _now()
        if row.provider == "mail":
            await asyncio.to_thread(guard_mail_hosts, row.config)
        try:
            if row.provider == "mail":
                await asyncio.to_thread(mail.check, mail_settings(row))
            else:
                settings = await feishu_settings_for(self._session, row)
                # Nothing to call with yet on the platform's app before the member
                # has authorized: the credentials are the administrator's and the
                # authorization-code exchange is what proves them.
                if row.config.get("app_id") or settings.user_access_token:
                    await FeishuClient(settings).tenant_token()
        except IntegrationError as exc:
            row.status = {
                "auth_failed": "auth_failed",
                "unreachable": "unreachable",
            }.get(exc.kind, "error")
            row.last_error = str(exc.args[0])
            raise
        row.status = "ok"
        row.last_error = ""

    async def connect(
        self,
        *,
        owner_user_id: int,
        owner_handle: str,
        provider: str,
        label: str,
        config: dict,
        secret: dict,
        grants: list[str],
    ) -> Integration:
        row = Integration(
            id=uuid.uuid4(),
            owner_user_id=owner_user_id,
            owner_handle=owner_handle,
            provider=provider,
            label=label,
            config=config,
            grants=[str(g) for g in grants],
            status="ok",
        )
        seal(row, secret)
        await self._check(row)
        self._session.add(row)
        await self._session.flush()
        return row

    async def connect_under_platform_app(
        self, *, owner_user_id: int, owner_handle: str
    ) -> Integration:
        """The member's own row under the platform's app, before they authorize.

        The row carries no credentials of its own: it is where the callback puts
        the ``user_access_token`` and ``refresh_token`` that make it usable.

        Idempotent — the app is one and so is this person's account in it, so
        clicking «连接飞书» twice returns the same row rather than a second one.
        No call is made here: the app credentials belong to the administrator,
        and the authorization-code exchange proves them a moment later.
        """
        app = await FeishuAppService(self._session).current()
        if app is None:
            raise ValidationError(say("feishuAppNotConfigured"))
        for row in await self.owned(owner_user_id):
            if row.provider == "feishu" and not row.config.get("app_id"):
                return row
        row = Integration(
            id=uuid.uuid4(),
            owner_user_id=owner_user_id,
            owner_handle=owner_handle,
            provider="feishu",
            label="飞书" if app.domain == "feishu" else "Lark",
            config={},
            grants=[],
            status="ok",
        )
        seal(row, {})
        self._session.add(row)
        await self._session.flush()
        return row

    async def update(
        self, row: Integration, *, grants=None, config=None, secret=None, label=None
    ):
        if grants is not None:
            row.grants = [str(g) for g in grants]
        if label:
            row.label = label
        recheck = False
        if config:
            row.config = {**row.config, **config}
            recheck = True
        if secret:
            seal(row, {**unseal(row), **secret})
            recheck = True
        if recheck:
            await self._check(row)
        await self._session.flush()
        return row

    async def recheck(self, row: Integration) -> Integration:
        try:
            await self._check(row)
        except IntegrationError:
            pass
        await self._session.flush()
        return row

    # ── mail ───────────────────────────────────────────────────────────────

    async def _mail(self, row: Integration, fn, *args, **kwargs):
        if row.provider != "mail":
            raise ValidationError(say("integrationNotMail"))
        await asyncio.to_thread(guard_mail_hosts, row.config)
        try:
            return await asyncio.to_thread(fn, mail_settings(row), *args, **kwargs)
        except IntegrationError as exc:
            if exc.kind == "auth_failed":
                row.status, row.last_error = "auth_failed", str(exc.args[0])
                await self._session.flush()
            raise

    async def search(self, row: Integration, **query) -> list[dict]:
        since = query.pop("since", None)
        if since:
            try:
                query["since"] = date.fromisoformat(since)
            except ValueError as exc:
                raise ValidationError(say("mailSinceFormat")) from exc
        return await self._mail(row, mail.search, **query)

    async def read(self, row: Integration, uid: str, folder: str) -> dict:
        message = await self._mail(row, mail.read, uid, folder=folder)
        return {**message, "source": f"{row.label} · {folder} · UID {uid}"}

    async def attachment(self, row: Integration, uid: str, index: int, folder: str):
        return await self._mail(row, mail.attachment, uid, index, folder=folder)

    def _attachment_bytes(
        self, project_id: uuid.UUID, room_id: uuid.UUID, path: str
    ) -> bytes:
        try:
            return library.read_room_file(project_id, room_id, path)
        except ValidationError as exc:
            raise ValidationError(say("mailAttachmentNotInRoom", path=path)) from exc

    def _message(
        self, row: Integration, draft: MailDraft, files: list[tuple[str, bytes]]
    ) -> EmailMessage:
        return mail.compose(
            sender=row.config.get("address") or row.config["username"],
            to=draft.to,
            cc=draft.cc,
            subject=draft.subject,
            body=draft.body,
            attachments=files,
            in_reply_to=draft.in_reply_to,
            message_id=draft.message_id,
        )

    async def draft(
        self,
        row: Integration,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        by: str,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        attachments: list[str],
        in_reply_to: str | None,
    ) -> MailDraft:
        to, cc = mail.addresses(to), mail.addresses(cc)
        if not to:
            raise ValidationError(say("mailRecipientRequired"))
        files: list[tuple[str, bytes]] = []
        recorded = []
        for path in attachments:
            data = self._attachment_bytes(project_id, room_id, path)
            name = path.rsplit("/", 1)[-1]
            files.append((name, data))
            recorded.append(
                {
                    "path": path,
                    "name": name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        if sum(len(d) for _n, d in files) > MAX_ATTACHMENT_BYTES:
            raise ValidationError(say("mailAttachmentsTooLarge"))
        draft = MailDraft(
            id=uuid.uuid4(),
            integration_id=row.id,
            project_id=project_id,
            topic_id=room_id,
            created_by=by,
            to=to,
            cc=cc,
            subject=subject,
            body=body,
            attachments=recorded,
            in_reply_to=in_reply_to,
            message_id="",
            status="drafted",
        )
        message = self._message(row, draft, files)
        draft.message_id = message["Message-ID"]
        folder = await self._mail(row, mail.append_draft, message)
        draft.error = ""
        self._session.add(draft)
        await self._session.flush()
        draft.attachments = recorded
        row.config = {**row.config, "drafts_folder": folder}
        return draft

    async def get_draft(self, draft_id: uuid.UUID) -> MailDraft:
        draft = await self._session.get(MailDraft, draft_id)
        if draft is None:
            raise NotFoundError(say("mailDraftNotFound"))
        return draft

    async def send(self, draft: MailDraft, *, owner_user_id: int, by: str) -> dict:
        row = await self.get_owned(draft.integration_id, owner_user_id)
        if draft.status != "drafted":
            raise ValidationError(say("mailDraftNotSendable", status=draft.status))
        files: list[tuple[str, bytes]] = []
        assert draft.topic_id is not None
        for item in draft.attachments:
            data = self._attachment_bytes(
                draft.project_id, draft.topic_id, item["path"]
            )
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValidationError(say("mailAttachmentChanged", name=item["name"]))
            files.append((item["name"], data))
        message = self._message(row, draft, files)
        try:
            refused = await self._mail(row, mail.send, message)
        except IntegrationError as exc:
            draft.status, draft.error = "failed", str(exc.args[0])
            await self._session.flush()
            raise
        draft.status = "sent"
        draft.sent_at = _now()
        draft.confirmed_by = by
        draft.error = f"服务器拒收：{', '.join(refused)}" if refused else ""
        notes = []
        try:
            sent_folder = await self._mail(row, mail.save_sent, message)
            if sent_folder:
                notes.append(f"已存一份到「{sent_folder}」")
        except IntegrationError as exc:
            notes.append(f"没能存到已发送（{exc.args[0]}）")
        drafts_folder = row.config.get("drafts_folder")
        if drafts_folder:
            try:
                removed = await self._mail(
                    row, mail.remove_by_message_id, drafts_folder, draft.message_id
                )
                notes.append("已从草稿箱移除" if removed else "草稿箱里没找到这封草稿")
            except IntegrationError as exc:
                notes.append(f"没能从草稿箱移除（{exc.args[0]}）")
        await self._session.flush()
        return {"draft": draft_view(draft), "refused": refused, "notes": notes}

    async def discard(self, draft: MailDraft, *, owner_user_id: int) -> MailDraft:
        row = await self.get_owned(draft.integration_id, owner_user_id)
        if draft.status != "drafted":
            raise ValidationError(say("mailDraftDiscardUnsentOnly"))
        folder = row.config.get("drafts_folder")
        if folder:
            await self._mail(row, mail.remove_by_message_id, folder, draft.message_id)
        draft.status = "discarded"
        await self._session.flush()
        return draft

    async def drafts_of(
        self, owner_user_id: int, status: str | None
    ) -> list[MailDraft]:
        query = (
            select(MailDraft)
            .join(Integration, Integration.id == MailDraft.integration_id)
            .where(Integration.owner_user_id == owner_user_id)
            .order_by(MailDraft.created_at.desc())
        )
        if status:
            query = query.where(MailDraft.status == status)
        return list(await self._session.scalars(query.limit(100)))

    # ── Feishu ─────────────────────────────────────────────────────────────

    async def feishu(self, row: Integration, action):
        if row.provider != "feishu":
            raise ValidationError(say("integrationNotFeishu"))
        settings = await feishu_settings_for(self._session, row)
        client = FeishuClient(settings)
        try:
            return await action(client)
        except IntegrationError as exc:
            if exc.kind == "auth_failed":
                row.status, row.last_error = "auth_failed", str(exc.args[0])
            raise
        finally:
            keep_feishu_tokens(row, settings)
            await self._session.flush()
