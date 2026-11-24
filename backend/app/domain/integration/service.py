"""Connect, lend and use a person's mailbox or Feishu account.

Who may do what: only the owner manages a connection, names the projects whose
AI teammates may use it, and sends a drafted mail. A teammate in a granted
project may search, read, fetch attachments, write drafts, and read and write
Feishu documents — always as the owner's account, never as the platform's.

The Feishu app those documents are reached through is the member's own: a
self-built app lives in one Feishu tenant, so each member connects with an app
built in their own company, its ``app_id`` and ``app_secret`` kept on the
connection itself. The platform holds no app of its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from email.message import EmailMessage

import httpx
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.integration import mail
from app.domain.integration.feishu import FeishuClient, FeishuSettings
from app.domain.integration.mail import IntegrationError, MailSettings
from app.domain.integration.models import Integration, MailDraft
from app.domain.integration.ports import RoomFiles, TeammateNames

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


#: Failures only the connection's owner can mend — authorize again, or bring
#: app credentials Feishu accepts. The row is marked failed and waits for them.
NEEDS_OWNER = frozenset({"auth_failed", "credentials_invalid"})


def feishu_settings(row: Integration) -> FeishuSettings:
    """The app credentials and the owner's tokens this connection is used with:
    the self-built app its owner brought (``config.app_id``, the sealed
    ``app_secret``), and whatever authorization the owner gave it.

    A row without an ``app_id`` was made under the platform-wide app, which no
    longer exists: there is nothing to call Feishu with, and its owner is told
    to connect again with their own company's app.
    """
    app_id = row.config.get("app_id")
    if not app_id:
        raise IntegrationError("credentials_invalid", say("feishuNeedsOwnApp"))
    secret = unseal(row)
    return FeishuSettings(
        app_id=app_id,
        app_secret=secret.get("app_secret") or "",
        domain=row.config.get("domain", "feishu"),
        user_access_token=secret.get("user_access_token"),
        user_token_expires_at=secret.get("user_token_expires_at"),
        refresh_token=secret.get("refresh_token"),
        refresh_token_expires_at=secret.get("refresh_token_expires_at"),
        folders=list(row.config.get("folders") or []),
    )


def keep_feishu_tokens(row: Integration, settings: FeishuSettings) -> None:
    """A refreshed user token is written back, or the next call refreshes again."""
    secret = unseal(row)
    fresh = {
        **secret,
        "user_access_token": settings.user_access_token,
        "user_token_expires_at": settings.user_token_expires_at,
        "refresh_token": settings.refresh_token,
        "refresh_token_expires_at": settings.refresh_token_expires_at,
    }
    if fresh != secret:
        seal(row, fresh)


#: How long before the refresh token stops working an authorization is renewed.
#: Feishu's lasts about a week, so an hourly look renews it with days to spare.
RENEW_BEFORE_S = 2 * 24 * 3600


def _access_lapses(settings: FeishuSettings) -> bool:
    """The user token will not last through a call."""
    return (
        bool(settings.user_access_token)
        and (settings.user_token_expires_at or 0) <= time.time() + 60
    )


def _refresh_lapses(settings: FeishuSettings) -> bool:
    """The refresh token is near its end; one whose end is not known (kept
    before its expiry was) is renewed so that it is."""
    if not settings.refresh_token:
        return False
    ends = settings.refresh_token_expires_at
    return ends is None or ends <= time.time() + RENEW_BEFORE_S


async def _renewed(
    session: AsyncSession,
    row: Integration,
    lapses: Callable[[FeishuSettings], bool],
) -> FeishuSettings:
    """The row's settings, its user token refreshed first when ``lapses`` says so.

    A refresh token is good for one refresh: two calls refreshing with the same
    one leave the second refused and the person told to authorize again. So the
    row is locked and read again before refreshing, and the new tokens are
    written back at once — a caller that waited on the lock finds them there and
    does not refresh a second time.
    """
    settings = feishu_settings(row)
    if not lapses(settings):
        return settings
    if inspect(row).persistent:
        await session.refresh(row, with_for_update=True)
        settings = feishu_settings(row)
        if not lapses(settings):
            return settings
    try:
        await FeishuClient(settings).refresh_user_token()
    except IntegrationError as exc:
        if exc.kind in NEEDS_OWNER:
            row.status, row.last_error = "auth_failed", str(exc.args[0])
            await session.flush()
        raise
    keep_feishu_tokens(row, settings)
    await session.flush()
    return settings


async def renew_feishu(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Refresh every Feishu authorization whose refresh token is near its end,
    so one the person has not used for a week still works. One that cannot be
    refreshed (Feishu ends every authorization a year after it was given) is
    marked failed, which the person's page says."""
    async with sessions() as session:
        ids = list(
            await session.scalars(
                select(Integration.id).where(Integration.provider == "feishu")
            )
        )
    for integration_id in ids:
        async with sessions() as session:
            row = await session.get(Integration, integration_id)
            # One already failed waits for the person to authorize again.
            if row is None or row.status == "auth_failed":
                continue
            try:
                await _renewed(session, row, _refresh_lapses)
            except IntegrationError as exc:
                # One Feishu did not answer for is tried again the next hour.
                if exc.kind not in NEEDS_OWNER:
                    continue
                # A failed one is marked so and kept; the scan goes on.
                row.status, row.last_error = "auth_failed", str(exc.args[0])
            await session.commit()


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

    found: list[IPAddress] = []
    for kind in ("A", "AAAA"):
        try:
            answer = httpx.get(
                settings.integration_doh_url,
                params={"name": host, "type": kind},
                timeout=8,
            ).json()
        except Exception:  # any failure means "not verified"
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
    def __init__(
        self, session: AsyncSession, room_files: RoomFiles, teammates: TeammateNames
    ):
        self._session = session
        self._room_files = room_files
        self._teammates = teammates

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
                settings = feishu_settings(row)
                # The owner's authorization is proved by refreshing it: a token
                # still in its two hours says nothing of whether it will be
                # renewed, and the refresh is what stops working when it lapses.
                if settings.user_access_token or settings.refresh_token:
                    settings = await _renewed(self._session, row, lambda _: True)
                await FeishuClient(settings).tenant_token()
        except IntegrationError as exc:
            row.status = {
                "auth_failed": "auth_failed",
                "credentials_invalid": "auth_failed",
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

    async def connect_feishu(
        self,
        *,
        owner_user_id: int,
        owner_handle: str,
        app_id: str,
        app_secret: str,
        domain: str,
    ) -> Integration:
        """The member's Feishu connection, under a self-built app of their own
        company.

        A self-built app is usable only inside its own Feishu tenant, so each
        member builds one in their company and brings its credentials. They are
        proved here, exactly as typed (a tenant token is asked for with them),
        so a wrong App Secret is said now, not after the authorization page.

        The same App ID entered again is that connection, not a second one
        (``uq_integrations_owner_feishu_app``): the credentials just proved
        replace the stored ones on it, whether or not the owner's earlier
        authorization still works — they authorize again next. Two connects
        racing for the same App ID meet at that index, and the one that lost
        becomes such a replacement.
        """
        if domain not in ("feishu", "lark"):
            raise ValidationError(say("feishuDomainInvalid"))
        await FeishuClient(
            FeishuSettings(app_id=app_id, app_secret=app_secret, domain=domain)
        ).tenant_token()
        existing = await self._feishu_connection(owner_user_id, app_id)
        if existing is not None:
            return await self._keep_feishu_app(existing, app_secret, domain)
        row = Integration(
            id=uuid.uuid4(),
            owner_user_id=owner_user_id,
            owner_handle=owner_handle,
            provider="feishu",
            label="飞书" if domain == "feishu" else "Lark",
            config={"app_id": app_id, "domain": domain, "folders": []},
            grants=[],
            status="ok",
            last_error="",
            last_checked_at=_now(),
        )
        seal(row, {"app_secret": app_secret})
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            existing = await self._feishu_connection(owner_user_id, app_id)
            if existing is None:
                raise
            return await self._keep_feishu_app(existing, app_secret, domain)
        return row

    async def _feishu_connection(
        self, owner_user_id: int, app_id: str
    ) -> Integration | None:
        return await self._session.scalar(
            select(Integration).where(
                Integration.owner_user_id == owner_user_id,
                Integration.provider == "feishu",
                Integration.config["app_id"].astext == app_id,
            )
        )

    async def _keep_feishu_app(
        self, row: Integration, app_secret: str, domain: str
    ) -> Integration:
        """Store app credentials already proved on ``row``, the owner's tokens
        left as they are, and say the row works as far as the app goes."""
        row.config = {**row.config, "domain": domain}
        seal(row, {**unseal(row), "app_secret": app_secret})
        row.status, row.last_error, row.last_checked_at = "ok", "", _now()
        await self._session.flush()
        return row

    async def update(
        self, row: Integration, *, grants=None, config=None, secret=None, label=None
    ):
        if grants is not None:
            row.grants = [str(g) for g in grants]
        if label:
            row.label = label
        if row.provider == "feishu" and secret and secret.get("app_secret"):
            # A new App Secret is proved by itself, before it is kept: proving
            # it through ``_check`` would refresh the owner's authorization
            # first, with the old secret, and fail on a lapsed one.
            settings = feishu_settings(row)
            await FeishuClient(
                FeishuSettings(
                    app_id=settings.app_id,
                    app_secret=secret["app_secret"],
                    domain=settings.domain,
                )
            ).tenant_token()
            await self._keep_feishu_app(row, secret["app_secret"], settings.domain)
            secret = {k: v for k, v in secret.items() if k != "app_secret"}
        recheck = False
        if config:
            row.config = {**row.config, **config}
            recheck = True
        if secret:
            seal(row, {**unseal(row), **secret})
            recheck = True
        if recheck:
            # Flushed first: checking a Feishu row may lock and read it again
            # (``_renewed``), which would otherwise drop these changes.
            await self._session.flush()
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
            return self._room_files.read_room_file(project_id, room_id, path)
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
                # The teammate who drafted it is the one to ask for a new
                # draft, called by its name: the refusal is read in a toast
                # on 「我的连接」 as well as on the room's card.
                raise ValidationError(
                    say(
                        "mailAttachmentChanged",
                        name=item["name"],
                        agent=await self._teammates.name_of(
                            draft.project_id, draft.created_by
                        ),
                    )
                )
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
        try:
            settings = await _renewed(self._session, row, _access_lapses)
        except IntegrationError as exc:
            if exc.kind in NEEDS_OWNER:
                row.status, row.last_error = "auth_failed", str(exc.args[0])
                await self._session.flush()
            raise
        client = FeishuClient(settings)
        try:
            return await action(client)
        except IntegrationError as exc:
            if exc.kind in NEEDS_OWNER:
                row.status, row.last_error = "auth_failed", str(exc.args[0])
            raise
        finally:
            keep_feishu_tokens(row, settings)
            await self._session.flush()
