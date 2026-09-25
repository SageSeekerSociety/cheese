"""Connect, lend and use a person's mailbox or Feishu account.

Who may do what: only the owner manages a connection, names the projects whose
AI teammates may use it, and sends a drafted mail. A teammate in a granted
project may search, read, fetch attachments, write drafts, and read and write
Feishu documents — always as the owner's account, never as the platform's.
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
from app.domain.integration import mail
from app.domain.integration.feishu import FeishuClient, FeishuSettings
from app.domain.integration.mail import IntegrationError, MailSettings
from app.domain.integration.models import Integration, MailDraft
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


def feishu_settings(row: Integration) -> FeishuSettings:
    secret = unseal(row)
    return FeishuSettings(
        app_id=row.config["app_id"],
        app_secret=secret["app_secret"],
        domain=row.config.get("domain", "feishu"),
        user_access_token=secret.get("user_access_token"),
        user_token_expires_at=secret.get("user_token_expires_at"),
        refresh_token=secret.get("refresh_token"),
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


def guard_mail_hosts(config: dict) -> None:
    """Refuse a mail server that is this platform's own network, or plaintext."""
    if settings.integration_allow_private_hosts:
        return
    if config.get("security") == "plain":
        raise ValidationError("邮件服务器必须用 SSL 或 STARTTLS 加密连接")
    for key in ("imap_host", "smtp_host"):
        host = str(config.get(key) or "")
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as exc:
            raise ValidationError(f"找不到邮件服务器 {host}") from exc
        for info in infos:
            address = ipaddress.ip_address(info[4][0])
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
                or address.is_unspecified
            ):
                raise ValidationError(f"邮件服务器 {host} 指向内网地址，不能使用")


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
            raise NotFoundError("没有这个连接")
        return row

    async def granted(self, project_id: uuid.UUID) -> list[Integration]:
        rows = await self._session.scalars(select(Integration))
        return [r for r in rows if str(project_id) in (r.grants or [])]

    async def for_project(
        self, integration_id: uuid.UUID, project_id: uuid.UUID
    ) -> Integration:
        row = await self._session.get(Integration, integration_id)
        if row is None:
            raise NotFoundError("没有这个连接")
        if str(project_id) not in (row.grants or []):
            raise ForbiddenError(
                f"{row.owner_handle} 没有把这个连接授权给这个项目使用；"
                "请他在「我的连接」里勾选这个项目"
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
                await FeishuClient(feishu_settings(row)).tenant_token()
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
            raise ValidationError("这个连接不是邮箱")
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
                raise ValidationError("since 要写成 2026-09-01 这样的日期") from exc
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
            raise ValidationError(
                f"附件「{path}」不在房间文件里：先用 cheese show 把它放进房间"
            ) from exc

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
            raise ValidationError("至少要有一个收件人")
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
            raise ValidationError("附件合计超过 20 MB")
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
            raise NotFoundError("没有这封草稿")
        return draft

    async def send(self, draft: MailDraft, *, owner_user_id: int, by: str) -> dict:
        row = await self.get_owned(draft.integration_id, owner_user_id)
        if draft.status != "drafted":
            raise ValidationError(f"这封草稿已经是「{draft.status}」状态，不能再发")
        files: list[tuple[str, bytes]] = []
        assert draft.topic_id is not None
        for item in draft.attachments:
            data = self._attachment_bytes(
                draft.project_id, draft.topic_id, item["path"]
            )
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValidationError(
                    f"附件「{item['name']}」在起草之后被改过，发出去的会和你确认的不一样；请让芝士重新起草"
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
            raise ValidationError("只能放弃还没发送的草稿")
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
            raise ValidationError("这个连接不是飞书")
        settings = feishu_settings(row)
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
