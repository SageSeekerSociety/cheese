"""A person's mailbox and Feishu connections, and what AI teammates do with them.

`/me/...` is the owner: connect, grant projects, confirm or discard a drafted
mail. `/integrations/{id}/...?topic=` is a teammate (or a person) in a room of
a granted project, using the owner's account.
"""

import hmac
import uuid
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.config import settings
from app.core.crypto import Purpose, keyed_digest, keyed_digests
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ForbiddenError, ValidationError
from app.domain.agent.platform_notices import (
    EVENT_MAIL_DRAFTED,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.identity.actor import Actor
from app.domain.integration.feishu import FeishuClient
from app.domain.integration.models import Integration
from app.domain.integration.service import (
    IntegrationService,
    draft_view,
    feishu_settings,
    keep_feishu_tokens,
    public,
)
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="", tags=["integrations"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


class MailIn(BaseModel):
    label: str = ""
    address: str = ""
    imap_host: str
    imap_port: int = 993
    smtp_host: str
    smtp_port: int = 465
    username: str
    password: str = Field(min_length=1)
    security: str = "ssl"
    grants: list[str] = Field(default_factory=list)


class FeishuIn(BaseModel):
    label: str = "飞书"
    app_id: str
    app_secret: str = Field(min_length=1)
    domain: str = "feishu"
    folders: list[str] = Field(default_factory=list)
    grants: list[str] = Field(default_factory=list)


class IntegrationPatch(BaseModel):
    label: str | None = None
    grants: list[str] | None = None
    password: str | None = None
    app_secret: str | None = None
    folders: list[str] | None = None


class SearchIn(BaseModel):
    query: str | None = None
    sender: str | None = None
    subject: str | None = None
    since: str | None = None
    folder: str = "INBOX"
    limit: int = Field(default=20, ge=1, le=50)


class DraftIn(BaseModel):
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    subject: str = Field(min_length=1)
    body: str
    attachments: list[str] = Field(default_factory=list)
    in_reply_to: str | None = None


class FeishuSearchIn(BaseModel):
    query: str = Field(min_length=1)


class FeishuDocIn(BaseModel):
    title: str = Field(min_length=1)
    content: str = ""
    folder: str | None = None


class FeishuEditIn(BaseModel):
    append: str | None = None
    block_id: str | None = None
    text: str | None = None


async def _person(resolver: ActorResolver) -> Actor:
    actor = await resolver.resolve(fallback_handle=None)
    if actor.via != "token" or actor.user_id is None:
        raise AuthenticationRequiredError("连接只能由本人登录后管理")
    return actor


async def _in_room(
    db: AsyncSession, resolver: ActorResolver, topic: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """(project, room, speaker) for a call made from a room."""
    place = await TopicService(db).place_or_404(topic)
    actor = await resolver.resolve(
        fallback_handle=None, project_id=place.project_id, topic_id=topic
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=topic, enforce=True
    )
    speaker = (
        actor.handle
        if actor.authenticated
        else await TopicMemberService(db).resolve_agent_handle(place.room_id)
    )
    return place.project_id, place.room_id, speaker


async def _usable(
    db: AsyncSession,
    resolver: ActorResolver,
    integration_id: uuid.UUID,
    topic: uuid.UUID,
) -> tuple[Integration, uuid.UUID, uuid.UUID, str]:
    project, room, speaker = await _in_room(db, resolver, topic)
    row = await IntegrationService(db).for_project(integration_id, project)
    return row, project, room, speaker


# ── the owner ──────────────────────────────────────────────────────────────


@router.get("/me/integrations")
async def my_integrations(db: DbSession, resolver: ActorResolverDep) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    rows = await IntegrationService(db).owned(actor.user_id)
    items = [public(r) for r in rows]
    return ok(page(items, len(items)))


@router.post("/me/integrations/mail")
async def connect_mail(body: MailIn, db: DbSession, resolver: ActorResolverDep) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    if body.security not in ("ssl", "starttls", "plain"):
        raise ValidationError("security 只能是 ssl、starttls 或 plain")
    address = body.address or body.username
    row = await IntegrationService(db).connect(
        owner_user_id=actor.user_id,
        owner_handle=actor.handle,
        provider="mail",
        label=body.label or address,
        config={
            "address": address,
            "imap_host": body.imap_host,
            "imap_port": body.imap_port,
            "smtp_host": body.smtp_host,
            "smtp_port": body.smtp_port,
            "username": body.username,
            "security": body.security,
        },
        secret={"password": body.password},
        grants=body.grants,
    )
    await db.commit()
    return ok(public(row))


@router.post("/me/integrations/feishu")
async def connect_feishu(
    body: FeishuIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    if body.domain not in ("feishu", "lark"):
        raise ValidationError("domain 只能是 feishu 或 lark")
    row = await IntegrationService(db).connect(
        owner_user_id=actor.user_id,
        owner_handle=actor.handle,
        provider="feishu",
        label=body.label,
        config={"app_id": body.app_id, "domain": body.domain, "folders": body.folders},
        secret={"app_secret": body.app_secret},
        grants=body.grants,
    )
    await db.commit()
    return ok(public(row))


@router.patch("/me/integrations/{integration_id}")
async def update_integration(
    integration_id: uuid.UUID,
    body: IntegrationPatch,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = IntegrationService(db)
    row = await service.get_owned(integration_id, actor.user_id)
    secret = {
        k: v
        for k, v in (("password", body.password), ("app_secret", body.app_secret))
        if v
    }
    row = await service.update(
        row,
        grants=body.grants,
        label=body.label,
        config={"folders": body.folders} if body.folders is not None else None,
        secret=secret or None,
    )
    await db.commit()
    return ok(public(row))


@router.post("/me/integrations/{integration_id}/check")
async def check_integration(
    integration_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = IntegrationService(db)
    row = await service.recheck(await service.get_owned(integration_id, actor.user_id))
    await db.commit()
    return ok(public(row))


@router.delete("/me/integrations/{integration_id}")
async def delete_integration(
    integration_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    row = await IntegrationService(db).get_owned(integration_id, actor.user_id)
    await db.delete(row)
    await db.commit()
    return ok({"deleted": str(integration_id)})


def _state(integration_id: uuid.UUID) -> str:
    kid, digest = keyed_digest(Purpose.INTEGRATION_STATE, integration_id.bytes)
    return f"{integration_id}.{kid}.{digest.hex()}"


def _redirect_uri() -> str:
    return f"{settings.frontend_url}/api/integrations/feishu/callback"


@router.get("/me/integrations/{integration_id}/feishu/authorize")
async def feishu_authorize(
    integration_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    row = await IntegrationService(db).get_owned(integration_id, actor.user_id)
    url = FeishuClient(feishu_settings(row)).authorize_url(
        _redirect_uri(), _state(row.id)
    )
    return ok({"url": url, "redirect_uri": _redirect_uri()})


@router.get("/integrations/feishu/callback")
async def feishu_callback(
    db: DbSession, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    back = f"{settings.frontend_url}/my/connections"
    try:
        raw_id, kid, digest = state.split(".")
        integration_id = uuid.UUID(raw_id)
        expected = keyed_digests(Purpose.INTEGRATION_STATE, integration_id.bytes).get(
            kid
        )
        if expected is None or not hmac.compare_digest(expected.hex(), digest):
            raise ValueError
    except ValueError:
        return RedirectResponse(f"{back}?{urlencode({'feishu': '授权链接无效'})}", 302)
    if error or not code:
        return RedirectResponse(
            f"{back}?{urlencode({'feishu': error or '没有授权'})}", 302
        )
    row = await db.get(Integration, integration_id)
    if row is None:
        return RedirectResponse(f"{back}?{urlencode({'feishu': '连接已删除'})}", 302)
    config = feishu_settings(row)
    try:
        await FeishuClient(config).exchange_code(code, _redirect_uri())
    except Exception as exc:  # noqa: BLE001 — the person reads why, on the page
        return RedirectResponse(f"{back}?{urlencode({'feishu': str(exc)[:200]})}", 302)
    keep_feishu_tokens(row, config)
    row.status, row.last_error = "ok", ""
    await db.commit()
    return RedirectResponse(f"{back}?{urlencode({'feishu': 'ok'})}", 302)


@router.get("/me/mail-drafts")
async def my_drafts(
    db: DbSession, resolver: ActorResolverDep, status: str | None = "drafted"
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    rows = await IntegrationService(db).drafts_of(actor.user_id, status or None)
    items = [draft_view(r) for r in rows]
    return ok(page(items, len(items)))


@router.post("/me/mail-drafts/{draft_id}/send")
async def send_draft(
    draft_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The owner read this exact draft (recipients, subject, body, files); send it."""
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = IntegrationService(db)
    draft = await service.get_draft(draft_id)
    try:
        result = await service.send(draft, owner_user_id=actor.user_id, by=actor.handle)
    finally:
        await db.commit()
    return ok(result)


@router.post("/me/mail-drafts/{draft_id}/discard")
async def discard_draft(
    draft_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = IntegrationService(db)
    draft = await service.discard(
        await service.get_draft(draft_id), owner_user_id=actor.user_id
    )
    await db.commit()
    return ok(draft_view(draft))


# ── from a room ────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/integrations")
async def project_integrations(
    project_id: uuid.UUID, topic: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    project, _room, _speaker = await _in_room(db, resolver, topic)
    if project != project_id:
        raise ForbiddenError("这个房间不属于这个项目")
    rows = await IntegrationService(db).granted(project_id)
    items = [
        {
            "id": str(r.id),
            "provider": r.provider,
            "label": r.label,
            "owner_handle": r.owner_handle,
            "status": r.status,
            "last_error": r.last_error,
        }
        for r in rows
    ]
    return ok(page(items, len(items)))


@router.post("/integrations/{integration_id}/mail/search")
async def mail_search(
    integration_id: uuid.UUID,
    body: SearchIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    service = IntegrationService(db)
    try:
        hits = await service.search(row, **body.model_dump())
    finally:
        await db.commit()
    return ok({"source": row.label, "messages": hits})


@router.get("/integrations/{integration_id}/mail/messages/{uid}")
async def mail_read(
    integration_id: uuid.UUID,
    uid: str,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    folder: str = "INBOX",
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    try:
        message = await IntegrationService(db).read(row, uid, folder)
    finally:
        await db.commit()
    return ok(message)


@router.get("/integrations/{integration_id}/mail/messages/{uid}/attachments/{index}")
async def mail_attachment(
    integration_id: uuid.UUID,
    uid: str,
    index: int,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    folder: str = "INBOX",
) -> Response:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    try:
        name, content_type, data = await IntegrationService(db).attachment(
            row, uid, index, folder
        )
    finally:
        await db.commit()
    return Response(
        data,
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.post("/integrations/{integration_id}/mail/drafts")
async def mail_draft(
    integration_id: uuid.UUID,
    body: DraftIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, project, room, speaker = await _usable(db, resolver, integration_id, topic)
    draft = await IntegrationService(db).draft(
        row,
        project_id=project,
        room_id=room,
        by=speaker,
        to=body.to,
        cc=body.cc,
        subject=body.subject,
        body=body.body,
        attachments=body.attachments,
        in_reply_to=body.in_reply_to,
    )
    summary = f"收件人：{', '.join(draft.to)}\n主题：{draft.subject}"
    if draft.attachments:
        summary += "\n附件：" + "、".join(a["name"] for a in draft.attachments)
    db.add(
        Block(
            id=uuid.uuid4(),
            project_id=project,
            topic_id=room,
            author="system",
            author_type=AuthorType.platform,
            kind=BlockKind.event,
            content=(
                f"芝士在 {row.label} 里写好了一封草稿，"
                f"等 {row.owner_handle} 确认后才会发送"
            ),
            meta={
                **notice(
                    EVENT_MAIL_DRAFTED,
                    severity=SEVERITY_INFO,
                    who=WHO_CHEESE,
                    detail=summary,
                    detail_label="草稿",
                ),
                "mail_draft_id": str(draft.id),
            },
        )
    )
    await ProjectNotificationService(db).create(
        project_id=project,
        level=NotificationLevel.light,
        kind=NotificationType.CHANGE_ALERT,
        title=f"邮件草稿待你确认：{draft.subject}",
        body=summary + "\n到「我的连接 → 待发送」核对后再发送",
        target_handle=row.owner_handle,
        topic_id=room,
        payload={"mail_draft_id": str(draft.id)},
    )
    await db.commit()
    return ok(
        {
            **draft_view(draft),
            "stored_in": row.config.get("drafts_folder"),
            "next": (
                f"已存进邮箱草稿箱；发送要等 {row.owner_handle} 在「我的连接」里确认"
            ),
        }
    )


@router.post("/integrations/{integration_id}/feishu/search")
async def feishu_search(
    integration_id: uuid.UUID,
    body: FeishuSearchIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    hits = await IntegrationService(db).feishu(row, lambda c: c.search(body.query))
    await db.commit()
    return ok({"source": row.label, "documents": hits})


@router.get("/integrations/{integration_id}/feishu/docs/{document_id}")
async def feishu_read(
    integration_id: uuid.UUID,
    document_id: str,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    doc = await IntegrationService(db).feishu(row, lambda c: c.read(document_id))
    await db.commit()
    return ok(doc)


@router.post("/integrations/{integration_id}/feishu/docs")
async def feishu_create(
    integration_id: uuid.UUID,
    body: FeishuDocIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    created = await IntegrationService(db).feishu(
        row, lambda c: c.create(body.title, body.content, body.folder)
    )
    await db.commit()
    return ok(created)


@router.patch("/integrations/{integration_id}/feishu/docs/{document_id}")
async def feishu_edit(
    integration_id: uuid.UUID,
    document_id: str,
    body: FeishuEditIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    if not body.append and not (body.block_id and body.text is not None):
        raise ValidationError(
            "要么给 append（追加的内容），要么给 block_id 和 text（改哪一段、改成什么）"
        )

    async def edit(client: FeishuClient) -> dict:
        if body.append:
            await client.append(document_id, body.append)
        if body.block_id and body.text is not None:
            await client.replace_text(document_id, body.block_id, body.text)
        return await client.read(document_id)

    doc = await IntegrationService(db).feishu(row, edit)
    await db.commit()
    return ok(doc)
