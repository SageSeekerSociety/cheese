"""A person's mailbox and Feishu connections, and what AI teammates do with them.

`/me/...` is the owner: connect, grant projects, confirm or discard a drafted
mail. `/integrations/{id}/...?topic=` is a teammate (or a person) in a room of
a granted project, using the owner's account.
"""

import hmac
import uuid
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.app_return import back_in_app, started_in_app
from app.api.auth import ActorResolver, ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.api.write_access import ROUTE_DECIDES
from app.core.config import settings
from app.core.crypto import Purpose, keyed_digest, keyed_digests
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    ValidationError,
    message_key,
)
from app.core.sentences import exception_text, listing, say, with_keys
from app.core.single_use_state import SingleUseUnavailableError
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.identity.actor import Actor
from app.domain.integration.feishu import FeishuClient
from app.domain.integration.mail import IntegrationError
from app.domain.integration.models import Integration, MailDraft
from app.domain.integration.service import (
    FeishuAppService,
    IntegrationService,
    draft_view,
    feishu_settings_for,
    keep_feishu_tokens,
    public,
)
from app.domain.library import service as library
from app.domain.notice_meta import (
    EVENT_MAIL_DRAFTED,
    EVENT_MAIL_RESULT,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_CHEESE,
    WHO_HUMAN,
    notice,
)
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.oauth import handoff as oauth_handoff
from app.domain.room_task.place import place_or_404
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="", tags=["integrations"])


def _service(db: AsyncSession) -> IntegrationService:
    """The service, with the room-file reader wired in.

    Reading a draft's attachments off a room's file tree belongs to
    `app.domain.library`, one layer above `integration`, so the module that
    assembles the route's dependencies hands it in — the shape
    `app.domain.user.ports` uses for the same reason.
    """
    return IntegrationService(db, library, _Teammates(db))


class _Teammates:
    """`integration.ports.TeammateNames`, answered by the project's roster of
    teammates (`agent_instance`, a layer above `integration`)."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def name_of(self, project_id: uuid.UUID, seat: str) -> str:
        named = await AgentInstanceService(self._db).for_seat_handle_in(
            project_id, seat
        )
        if named is not None and str(named.name_source) != "default":
            return named.display_name
        return say("teammateDefaultName")


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


class IntegrationPatch(BaseModel):
    label: str | None = None
    grants: list[str] | None = None
    password: str | None = None
    app_secret: str | None = None
    folders: list[str] | None = None
    #: The Feishu authorization this browser came back with
    #: (`feishu_callback`): claimed by the connection's owner, it finishes
    #: authorizing; the answer carries the outcome as ``authorization``.
    feishu_handoff: str | None = Field(default=None, max_length=64)


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
    actor = await resolver.resolve()
    if actor.via != "token" or actor.user_id is None:
        raise AuthenticationRequiredError(say("integrationSignInOwner"))
    return actor


async def _in_room(
    db: AsyncSession, resolver: ActorResolver, topic: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """(project, room, speaker) for a call made from a room."""
    place = await place_or_404(db, topic)
    actor = await resolver.resolve(project_id=place.project_id, topic_id=topic)
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
    row = await _service(db).for_project(integration_id, project)
    return row, project, room, speaker


# ── the owner ──────────────────────────────────────────────────────────────


@router.get("/me/integrations")
async def my_integrations(db: DbSession, resolver: ActorResolverDep) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    rows = await _service(db).owned(actor.user_id)
    items = [public(r) for r in rows]
    return ok(page(items, len(items)))


@router.post("/me/integrations/mail", dependencies=[ROUTE_DECIDES])
async def connect_mail(body: MailIn, db: DbSession, resolver: ActorResolverDep) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    if body.security not in ("ssl", "starttls", "plain"):
        raise ValidationError(say("mailSecurityInvalid"))
    address = body.address or body.username
    row = await _service(db).connect(
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


@router.get("/me/integrations/feishu")
async def feishu_availability(db: DbSession, resolver: ActorResolverDep) -> dict:
    """平台配过飞书应用没有 —— 「连接飞书」那颗按钮亮不亮就是这个问题的答案。

    平台应用只有一个，读它不需要是谁的连接；但这是成员自己的页面，所以还是要本人。
    """
    await _person(resolver)
    app = await FeishuAppService(db).current()
    return ok(
        {
            "configured": app is not None,
            "app_id": app.app_id if app else "",
            "domain": app.domain if app else "",
        }
    )


@router.post("/me/integrations/feishu", dependencies=[ROUTE_DECIDES])
async def connect_feishu(db: DbSession, resolver: ActorResolverDep) -> dict:
    """「连接飞书」按下的第一步：先有自己那一行，再跳去飞书授权页。

    行在这里就落库，因为回调用 `state` 里的 id 找到它、把 token 写进去（见
    `feishu_callback`）。应用凭据是平台的，这一行不带；授予哪些项目之后在这个连接
    那一行上勾选。
    """
    actor = await _person(resolver)
    assert actor.user_id is not None
    row = await _service(db).connect_under_platform_app(
        owner_user_id=actor.user_id, owner_handle=actor.handle
    )
    await db.commit()
    return ok(public(row))


@router.patch("/me/integrations/{integration_id}", dependencies=[ROUTE_DECIDES])
async def update_integration(
    integration_id: uuid.UUID,
    body: IntegrationPatch,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = _service(db)
    row = await service.get_owned(integration_id, actor.user_id)
    if body.feishu_handoff is not None:
        outcome = await _claim_feishu(db, row, body.feishu_handoff)
        return ok({**public(row), "authorization": outcome})
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


@router.post("/me/integrations/{integration_id}/check", dependencies=[ROUTE_DECIDES])
async def check_integration(
    integration_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = _service(db)
    row = await service.recheck(await service.get_owned(integration_id, actor.user_id))
    await db.commit()
    return ok(public(row))


@router.delete("/me/integrations/{integration_id}", dependencies=[ROUTE_DECIDES])
async def delete_integration(
    integration_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    row = await _service(db).get_owned(integration_id, actor.user_id)
    await db.delete(row)
    await db.commit()
    return ok({"deleted": str(integration_id)})


# Started in the desktop app: the callback hands the result back to it. Left
# outside the digest, as all it can change is where the result is shown.
_IN_APP = ".app"


def _state(integration_id: uuid.UUID, *, in_app: bool) -> str:
    kid, digest = keyed_digest(Purpose.INTEGRATION_STATE, integration_id.bytes)
    return f"{integration_id}.{kid}.{digest.hex()}" + (_IN_APP if in_app else "")


def _redirect_uri() -> str:
    return f"{settings.frontend_url}/api/integrations/feishu/callback"


@router.get("/me/integrations/{integration_id}/feishu/authorize")
async def feishu_authorize(
    integration_id: uuid.UUID,
    request: Request,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    row = await _service(db).get_owned(integration_id, actor.user_id)
    settings = await feishu_settings_for(db, row)
    url = FeishuClient(settings).authorize_url(
        _redirect_uri(), _state(row.id, in_app=started_in_app(request))
    )
    return ok({"url": url, "redirect_uri": _redirect_uri()})


@router.get("/integrations/feishu/callback")
async def feishu_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
) -> RedirectResponse:
    """Where Feishu sends the browser back. Nothing is saved here: what came
    back is held for the person who started the flow (`oauth/handoff.py`),
    and the connections page claims it once it is signed in."""
    in_app = state.endswith(_IN_APP)
    landing = await _hand_back_feishu(code, state.removesuffix(_IN_APP), error)
    return back_in_app(request, landing) if in_app else landing


def _connections(query: dict[str, str]) -> RedirectResponse:
    back = f"{settings.frontend_url}/users/settings/connections"
    return RedirectResponse(f"{back}?{urlencode(query)}", 302)


def _feishu_integration(state: str) -> uuid.UUID | None:
    """The integration a state was minted for, if it is ours."""
    try:
        raw_id, kid, digest = state.split(".")
        integration_id = uuid.UUID(raw_id)
    except ValueError:
        return None
    expected = keyed_digests(Purpose.INTEGRATION_STATE, integration_id.bytes).get(kid)
    if expected is None or not hmac.compare_digest(expected.hex(), digest):
        return None
    return integration_id


async def _hand_back_feishu(code: str, state: str, error: str) -> RedirectResponse:
    integration_id = _feishu_integration(state)
    if integration_id is None:
        return _connections({"feishu": "invalid_link"})
    if error or not code:
        return _connections(
            {"feishu": "denied", **({"feishu_detail": error[:200]} if error else {})}
        )
    try:
        held = await oauth_handoff.keep(
            "feishu", {"integration": str(integration_id), "code": code}
        )
    except SingleUseUnavailableError:
        return _connections({"feishu": "exchange_failed"})
    return _connections(
        {"feishu_integration": str(integration_id), "feishu_handoff": held}
    )


async def _claim_feishu(
    db: AsyncSession, row: Integration, handoff: str
) -> dict[str, str]:
    """Finish the authorization the callback held for ``row``, whose owner is
    the one asking (`update_integration` checked it before anything is
    taken). A hand-off for another connection — somebody else's flow,
    finished in this browser — is spent and saves nothing."""
    held = await oauth_handoff.take(handoff)
    if (
        held is None
        or held.get("kind") != "feishu"
        or held.get("integration") != str(row.id)
    ):
        return {"feishu": "invalid_link"}
    return await _finish_feishu(db, row, held["code"])


async def _finish_feishu(
    db: AsyncSession, row: Integration, code: str
) -> dict[str, str]:
    """Exchange the code and keep the tokens on ``row``; the outcome the page
    words: ``ok``, ``exchange_failed``, or the key of a refusal of ours said
    with ``say()``, with what Feishu itself answered as ``feishu_detail``."""

    def land(outcome: str, detail: str = "") -> dict[str, str]:
        query = {"feishu": outcome}
        if detail:
            query["feishu_detail"] = detail[:200]
        return query

    try:
        config = await feishu_settings_for(db, row)
        await FeishuClient(config).exchange_code(code, _redirect_uri())
    except Exception as exc:  # the person reads why, on the page
        # Feishu's answer goes by Feishu's own words, under the code the page
        # words; an IntegrationError is that answer even when it is said with
        # a key. A refusal of ours goes by its sentence's key.
        if isinstance(exc, IntegrationError):
            return land("exchange_failed", exc.reply)
        said = message_key(exception_text(exc))
        if said is not None:
            return land(said["key"])
        return land("exchange_failed", getattr(exc, "reply", ""))
    keep_feishu_tokens(row, config)
    row.status, row.last_error = "ok", ""
    await db.commit()
    return land("ok")


@router.get("/me/mail-drafts")
async def my_drafts(
    db: DbSession, resolver: ActorResolverDep, status: str | None = "drafted"
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    rows = await _service(db).drafts_of(actor.user_id, status or None)
    items = [draft_view(r) for r in rows]
    return ok(page(items, len(items)))


def _mail_outcome(draft: MailDraft, by: str) -> Block | None:
    """Tell the room how the draft it saw ended, so its card stops asking."""
    if draft.topic_id is None:
        return
    if draft.status == "sent":
        line = say("mailSent", actor=f"<@{by}>", subject=draft.subject)
        severity = SEVERITY_INFO
    elif draft.status == "failed":
        line = say("mailFailed", subject=draft.subject)
        severity = SEVERITY_WARN
    elif draft.status == "discarded":
        line = say("mailDiscarded", actor=f"<@{by}>", subject=draft.subject)
        severity = SEVERITY_INFO
    else:
        return
    return Block(
        id=uuid.uuid4(),
        project_id=draft.project_id,
        conversation_id=draft.topic_id,
        author="system",
        author_type=AuthorType.platform,
        kind=BlockKind.event,
        content=line,
        # A row, not `BlockRepository.add`: the line's key is recorded here.
        meta=with_keys(
            {
                **notice(
                    EVENT_MAIL_RESULT,
                    severity=severity,
                    who=WHO_HUMAN,
                    detail=draft.error or None,
                    detail_label=say("labelReason") if draft.error else None,
                ),
                "mail_draft_id": str(draft.id),
                "status": draft.status,
                "sent_at": draft.sent_at.isoformat() if draft.sent_at else None,
            },
            content=line,
        ),
    )


@router.post("/me/mail-drafts/{draft_id}/send", dependencies=[ROUTE_DECIDES])
async def send_draft(
    draft_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The owner read this exact draft (recipients, subject, body, files); send it."""
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = _service(db)
    draft = await service.get_draft(draft_id)
    try:
        result = await service.send(draft, owner_user_id=actor.user_id, by=actor.handle)
    finally:
        outcome = _mail_outcome(draft, actor.handle)
        if outcome is not None:
            db.add(outcome)
        await db.commit()
    return ok(result)


@router.post("/me/mail-drafts/{draft_id}/discard", dependencies=[ROUTE_DECIDES])
async def discard_draft(
    draft_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = _service(db)
    draft = await service.discard(
        await service.get_draft(draft_id), owner_user_id=actor.user_id
    )
    outcome = _mail_outcome(draft, actor.handle)
    if outcome is not None:
        db.add(outcome)
    await db.commit()
    return ok(draft_view(draft))


# ── from a room ────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/integrations")
async def project_integrations(
    project_id: uuid.UUID, topic: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    project, _room, _speaker = await _in_room(db, resolver, topic)
    if project != project_id:
        raise ForbiddenError(say("roomNotInProject"))
    rows = await _service(db).granted(project_id)
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


@router.post("/integrations/{integration_id}/mail/search", dependencies=[ROUTE_DECIDES])
async def mail_search(
    integration_id: uuid.UUID,
    body: SearchIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    service = _service(db)
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
        message = await _service(db).read(row, uid, folder)
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
        name, content_type, data = await _service(db).attachment(
            row, uid, index, folder
        )
    finally:
        await db.commit()
    return Response(
        data,
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.post("/integrations/{integration_id}/mail/drafts", dependencies=[ROUTE_DECIDES])
async def mail_draft(
    integration_id: uuid.UUID,
    body: DraftIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, project, room, speaker = await _usable(db, resolver, integration_id, topic)
    draft = await _service(db).draft(
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
    summary = (
        say(
            "mailDraftSummaryWithFiles",
            to=", ".join(draft.to),
            subject=draft.subject,
            files=listing(a["name"] for a in draft.attachments),
        )
        if draft.attachments
        else say("mailDraftSummary", to=", ".join(draft.to), subject=draft.subject)
    )
    line = say(
        "mailDraftedBy",
        who=f"<@{speaker}>",
        account=row.label,
        owner=f"<@{row.owner_handle}>",
    )
    block_id = uuid.uuid4()
    db.add(
        Block(
            id=block_id,
            project_id=project,
            conversation_id=room,
            author="system",
            author_type=AuthorType.platform,
            kind=BlockKind.event,
            content=line,
            # A row, not `BlockRepository.add`: the line's key is recorded here.
            meta=with_keys(
                {
                    **notice(
                        EVENT_MAIL_DRAFTED,
                        severity=SEVERITY_INFO,
                        who=WHO_CHEESE,
                        detail=summary,
                        detail_label=say("labelMailDraft"),
                    ),
                    "mail_draft_id": str(draft.id),
                    # The room's confirm card is drawn from this: what the owner
                    # checks is what the teammate wrote, in the room it was written.
                    "mail": {
                        "owner": row.owner_handle,
                        # 写它的那位队友：卡上那句「由 X 起草」叫它的名字。
                        "drafted_by": speaker,
                        "account": row.label,
                        "to": draft.to,
                        "cc": draft.cc,
                        "subject": draft.subject,
                        "body": draft.body,
                        "attachments": [
                            {"name": a["name"], "size": a.get("size")}
                            for a in draft.attachments
                        ],
                    },
                },
                content=line,
            ),
        )
    )
    await ProjectNotificationService(db).create(
        project_id=project,
        level=NotificationLevel.light,
        kind=NotificationType.CHANGE_ALERT,
        title=f"邮件草稿待你确认：{draft.subject}",
        body=summary + "\n在房间里的卡片上核对后点「确认发送」",
        target_handle=row.owner_handle,
        conversation_id=room,
        payload={"mail_draft_id": str(draft.id), "block_id": str(block_id)},
    )
    await db.commit()
    return ok(
        {
            **draft_view(draft),
            "stored_in": row.config.get("drafts_folder"),
            "next": (
                f"已存进邮箱草稿箱；发送要等 {row.owner_handle} "
                "在房间里的卡片上核对后点「确认发送」"
            ),
        }
    )


@router.post(
    "/integrations/{integration_id}/feishu/search", dependencies=[ROUTE_DECIDES]
)
async def feishu_search(
    integration_id: uuid.UUID,
    body: FeishuSearchIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    hits = await _service(db).feishu(row, lambda c: c.search(body.query))
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
    doc = await _service(db).feishu(row, lambda c: c.read(document_id))
    await db.commit()
    return ok(doc)


@router.post("/integrations/{integration_id}/feishu/docs", dependencies=[ROUTE_DECIDES])
async def feishu_create(
    integration_id: uuid.UUID,
    body: FeishuDocIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    created = await _service(db).feishu(
        row, lambda c: c.create(body.title, body.content, body.folder)
    )
    await db.commit()
    return ok(created)


@router.patch(
    "/integrations/{integration_id}/feishu/docs/{document_id}",
    dependencies=[ROUTE_DECIDES],
)
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
        raise ValidationError(say("feishuDocEditShape"))

    async def edit(client: FeishuClient) -> dict:
        if body.append:
            await client.append(document_id, body.append)
        if body.block_id and body.text is not None:
            await client.replace_text(document_id, body.block_id, body.text)
        return await client.read(document_id)

    doc = await _service(db).feishu(row, edit)
    await db.commit()
    return ok(doc)
