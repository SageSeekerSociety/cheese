"""空间公告：一条一行，发布时通知空间里的其他人。

读的人是空间里的每个人，写的人是这个空间的管理员 —— 两道门都在路由上
（`api/routes/space_announcements.py`），这里只管公告本身和它发出去的通知。

## 通知只在发布那一刻发一次

发布时把这条公告交给投递账本（`delivery.ledger`），收件人是那一刻空间里除发布人
以外的每个人。事件的身份是「这条公告被发布了」，所以同一条公告无论怎么重算都只
通知一次。之后：

- **改**不再通知，只把已经发出去的那些改成新的标题和摘要（`ledger.amend`）——
  动态里那一行说的得是这条公告现在说的话。
- **删**把发出去的那些一起撤回（`ledger.retract`）：一条删掉的公告不该还躺在谁的
  动态里，点开是一页找不到的东西。

走哪个渠道由收件人自己的偏好决定（`notification.preferences` 的矩阵）：设计稿里
「空间公告」一行默认站内 + 邮件、不推送，收件人也能自己关掉（`MAILBOX_ONLY` 是那
张矩阵之前的旧规矩，现在矩阵说了算）。

## 到期

``expires_at`` 过了就是已到期：它离开题目列表顶上那一栏，在公告页上收进「已到期」。
到没到期由这里按服务器的时钟答（`list_for_space` 把两组分开给），读的那一侧不
自己比时间。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.domain.delivery.addressing import Event, Hand, address
from app.domain.delivery.ledger import (
    DeliveryEvent,
    amend,
    deliver,
    event_id_for,
    retract,
)
from app.domain.notification.models import NotificationType
from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAnnouncement,
    SpaceMember,
)
from app.domain.user.models import User, UserProfile

TITLE_MAX_LENGTH = 255

#: 动态里那一行正文摘要的长度上限。
_EXCERPT_LENGTH = 120

#: 这些标签收尾的地方是一行的尽头：摘要取的是正文的第一行。
_LINE_END = re.compile(r"<br\s*/?>|</(?:p|div|li|h[1-6]|blockquote|pre)>", re.I)
_TAG = re.compile(r"<[^>]+>")


def first_line(content: str) -> str:
    """富文本正文的第一行有字的文字，截到 `_EXCERPT_LENGTH`。"""
    text = html.unescape(_TAG.sub("", _LINE_END.sub("\n", content)))
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return (
                line if len(line) <= _EXCERPT_LENGTH else f"{line[:_EXCERPT_LENGTH]}…"
            )
    return ""


@dataclass(frozen=True, slots=True)
class Author:
    id: int
    username: str
    nickname: str
    avatar_id: int | None


@dataclass(frozen=True, slots=True)
class AnnouncementView:
    row: SpaceAnnouncement
    author: Author | None


@dataclass(frozen=True, slots=True)
class SpaceAnnouncements:
    current: list[AnnouncementView]
    expired: list[AnnouncementView]


def _is_expired(row: SpaceAnnouncement, now: datetime) -> bool:
    return row.expires_at is not None and row.expires_at <= now


def _current_order(view: AnnouncementView) -> tuple:
    """置顶的在前，各自新的在前。"""
    return (not view.row.pinned, -view.row.created_at.timestamp(), -view.row.id)


def _expired_order(view: AnnouncementView) -> tuple:
    """刚到期的在前。"""
    expires_at = view.row.expires_at
    assert expires_at is not None
    return (-expires_at.timestamp(), -view.row.id)


def _clean_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        raise BadRequestError("Announcement title cannot be empty")
    if len(cleaned) > TITLE_MAX_LENGTH:
        raise BadRequestError(
            f"Announcement title must be at most {TITLE_MAX_LENGTH} characters"
        )
    return cleaned


class SpaceAnnouncementService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- reads ---------------------------------------------------------------

    async def list_for_space(self, space_id: int) -> SpaceAnnouncements:
        rows = list(
            (
                await self._session.scalars(
                    select(SpaceAnnouncement).where(
                        SpaceAnnouncement.space_id == space_id
                    )
                )
            ).all()
        )
        views = await self._with_authors(rows)
        now = datetime.now(UTC)
        return SpaceAnnouncements(
            current=sorted(
                (v for v in views if not _is_expired(v.row, now)), key=_current_order
            ),
            expired=sorted(
                (v for v in views if _is_expired(v.row, now)), key=_expired_order
            ),
        )

    async def audience_size(self, space_id: int, *, author_id: int) -> int:
        """发布一条公告会通知到几个人 —— 发布弹窗里那句话的 N。"""
        return len(await self._audience_ids(space_id, author_id=author_id))

    # -- writes --------------------------------------------------------------

    async def publish(
        self,
        *,
        space_id: int,
        author_id: int,
        title: str,
        content: str,
        pinned: bool,
        expires_at: datetime | None,
    ) -> AnnouncementView:
        space = await self._space_or_404(space_id)
        now = datetime.now(UTC)
        if expires_at is not None and expires_at <= now:
            raise BadRequestError("expiresAt must be in the future")
        row = SpaceAnnouncement(
            space_id=space_id,
            author_id=author_id,
            title=_clean_title(title),
            content=content,
            pinned=pinned,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        view = (await self._with_authors([row]))[0]
        await self._notify(space, view)
        return view

    async def update(
        self,
        *,
        space_id: int,
        announcement_id: int,
        title: str | None = None,
        content: str | None = None,
        pinned: bool | None = None,
        expires_at: datetime | None = None,
        set_expires_at: bool = False,
    ) -> AnnouncementView:
        row = await self._row_or_404(space_id, announcement_id)
        said_changed = False
        if title is not None:
            cleaned = _clean_title(title)
            said_changed |= cleaned != row.title
            row.title = cleaned
        if content is not None:
            said_changed |= content != row.content
            row.content = content
        if set_expires_at:
            said_changed |= expires_at != row.expires_at
            row.expires_at = expires_at
        if pinned is not None:
            # 置顶只改摆放的位置，不改它说的话，所以不算「已编辑」。
            row.pinned = pinned
        if said_changed:
            row.updated_at = datetime.now(UTC)
        await self._session.flush()
        view = (await self._with_authors([row]))[0]
        if said_changed:
            space = await self._space_or_404(space_id)
            await amend(
                self._session,
                self._event_id(row),
                self._payload(space, view),
            )
        return view

    async def delete(self, *, space_id: int, announcement_id: int) -> None:
        row = await self._row_or_404(space_id, announcement_id)
        await retract(self._session, self._event_id(row))
        await self._session.execute(
            delete(SpaceAnnouncement).where(SpaceAnnouncement.id == row.id)
        )
        await self._session.flush()

    # -- notification --------------------------------------------------------

    @staticmethod
    def _event_id(row: SpaceAnnouncement):
        return event_id_for(NotificationType.SPACE_ANNOUNCEMENT, row.id)

    @staticmethod
    def _payload(space: Space, view: AnnouncementView) -> dict:
        row = view.row
        return {
            "spaceId": space.id,
            "spaceName": space.name,
            "announcementId": row.id,
            "title": row.title,
            "excerpt": first_line(row.content),
            "authorName": view.author.nickname if view.author else "",
        }

    async def _notify(self, space: Space, view: AnnouncementView) -> None:
        row = view.row
        assert row.author_id is not None
        audience = await self._audience_ids(space.id, author_id=row.author_id)
        if not audience:
            return
        handles = (
            await self._session.scalars(
                select(User.username).where(User.id.in_(audience))
            )
        ).all()
        await deliver(
            self._session,
            DeliveryEvent(
                id=self._event_id(row),
                type=NotificationType.SPACE_ANNOUNCEMENT,
                payload=self._payload(space, view),
                occurred_at=row.created_at,
            ),
            address(Event(audience=tuple(sorted(handles))), Hand.participant),
        )

    async def _audience_ids(self, space_id: int, *, author_id: int) -> set[int]:
        """空间里的每个人：成员，加上管理员（他们在这个空间里不必有成员那一行），
        去掉发布人自己。"""
        members = await self._session.scalars(
            select(SpaceMember.user_id).where(
                SpaceMember.space_id == space_id, SpaceMember.deleted_at.is_(None)
            )
        )
        managers = await self._session.scalars(
            select(SpaceAdminRelation.user_id).where(
                SpaceAdminRelation.space_id == space_id,
                SpaceAdminRelation.deleted_at.is_(None),
            )
        )
        return ({int(i) for i in members} | {int(i) for i in managers}) - {author_id}

    # -- helpers -------------------------------------------------------------

    async def _space_or_404(self, space_id: int) -> Space:
        space = await self._session.get(Space, space_id)
        if space is None or space.deleted_at is not None:
            raise NotFoundError(
                "Resource space not found", data={"type": "space", "id": space_id}
            )
        return space

    async def _row_or_404(
        self, space_id: int, announcement_id: int
    ) -> SpaceAnnouncement:
        row = await self._session.get(SpaceAnnouncement, announcement_id)
        if row is None or row.space_id != space_id:
            raise NotFoundError(
                "Announcement not found",
                data={"spaceId": space_id, "announcementId": announcement_id},
            )
        return row

    async def _with_authors(
        self, rows: list[SpaceAnnouncement]
    ) -> list[AnnouncementView]:
        ids = {r.author_id for r in rows if r.author_id is not None}
        authors: dict[int, Author] = {}
        if ids:
            result = await self._session.execute(
                select(
                    User.id, User.username, UserProfile.nickname, UserProfile.avatar_id
                )
                .outerjoin(
                    UserProfile,
                    (UserProfile.user_id == User.id) & UserProfile.deleted_at.is_(None),
                )
                .where(User.id.in_(ids))
            )
            for user_id, username, nickname, avatar_id in result.all():
                authors[user_id] = Author(
                    id=user_id,
                    username=username,
                    nickname=nickname or username,
                    avatar_id=avatar_id,
                )
        return [
            AnnouncementView(
                row=r, author=authors.get(r.author_id) if r.author_id else None
            )
            for r in rows
        ]
