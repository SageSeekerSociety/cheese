"""通知偏好的那张表 —— 一人一行，缺行就是全套默认。

一个人有没有调过设置是他自己的事，所以**读取永远成功**：没有行就返回设计稿的默认
（`preferences.default_preferences`），接口不会因为「你还没保存过」而回 404。写入是
一次 upsert，改一格不碰别的行。

`events` 存成 JSONB 而不是二十四列布尔：矩阵将来多半还会长行，多一行不该多一次迁移。
值里只放矩阵那八行，缺的一律按默认补（`Preferences.channel`），所以旧行不会被以后新增
的类别卡住。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.domain.notification.preferences import (
    ChannelChoice,
    DigestCadence,
    EmailMode,
    PreferenceCategory,
    Preferences,
    default_preferences,
    parse_hhmm,
)


class NotificationPreference(Base):
    __tablename__ = "notification_preference"

    #: 谁的偏好。一个人一行，所以主键就是 user_id，没有第二个键可以查错。
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    in_app_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    push_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    email_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="digest", server_default="digest"
    )
    quiet_hours_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    quiet_hours_start: Mapped[str] = mapped_column(
        String(5), nullable=False, default="22:00", server_default="22:00"
    )
    quiet_hours_end: Mapped[str] = mapped_column(
        String(5), nullable=False, default="08:00", server_default="08:00"
    )
    digest_cadence: Mapped[str] = mapped_column(
        String(16), nullable=False, default="weekly", server_default="weekly"
    )
    #: 矩阵那八行的渠道开关；缺的类别按默认补。
    events: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def _enum_of[EnumT: (EmailMode, DigestCadence)](
    enum_cls: type[EnumT], value: str
) -> EnumT:
    """存量行里万一有个不认识的值，退回这一类里的第一个（默认那一档）。"""
    try:
        return enum_cls(value)
    except ValueError:
        return next(iter(enum_cls))


def _choice_from(raw: object, category: PreferenceCategory) -> ChannelChoice:
    default = default_preferences().channel(category)
    if not isinstance(raw, Mapping):
        return default
    return ChannelChoice(
        in_app=bool(raw.get("inApp", default.in_app)),
        push=bool(raw.get("push", default.push)),
        email=bool(raw.get("email", default.email)),
    )


def _to_preferences(row: NotificationPreference) -> Preferences:
    events = row.events if isinstance(row.events, dict) else {}
    channels = {
        category: _choice_from(events.get(category.value), category)
        for category in PreferenceCategory
    }
    return Preferences(
        in_app_enabled=row.in_app_enabled,
        push_enabled=row.push_enabled,
        email_mode=_enum_of(EmailMode, row.email_mode),
        quiet_hours_enabled=row.quiet_hours_enabled,
        quiet_hours_start=parse_hhmm(row.quiet_hours_start),
        quiet_hours_end=parse_hhmm(row.quiet_hours_end),
        digest_cadence=_enum_of(DigestCadence, row.digest_cadence),
        channels=channels,
    )


def _events_json(pref: Preferences) -> dict:
    return {
        category.value: {
            "inApp": pref.channel(category).in_app,
            "push": pref.channel(category).push,
            "email": pref.channel(category).email,
        }
        for category in PreferenceCategory
    }


class PreferencesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_user(self, user_id: int) -> Preferences:
        """这个人的偏好；没保存过就是默认（读取永远成功，见模块说明）。"""
        row = await self._session.get(NotificationPreference, user_id)
        return _to_preferences(row) if row is not None else default_preferences()

    async def for_users(self, user_ids: Iterable[int]) -> dict[int, Preferences]:
        """一次把一批人的偏好读出来 —— 投递一条事件要按人裁渠道，别一个人一次查。"""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        rows = await self._session.scalars(
            select(NotificationPreference).where(
                NotificationPreference.user_id.in_(ids)
            )
        )
        found = {row.user_id: _to_preferences(row) for row in rows}
        return {user_id: found.get(user_id) or default_preferences() for user_id in ids}

    async def save(self, user_id: int, pref: Preferences) -> None:
        """整份写入这个人的偏好；改一格不新建行，也不动别人的行。"""
        stmt = pg_insert(NotificationPreference).values(
            user_id=user_id,
            in_app_enabled=pref.in_app_enabled,
            push_enabled=pref.push_enabled,
            email_mode=pref.email_mode.value,
            quiet_hours_enabled=pref.quiet_hours_enabled,
            quiet_hours_start=pref.quiet_hours_start.strftime("%H:%M"),
            quiet_hours_end=pref.quiet_hours_end.strftime("%H:%M"),
            digest_cadence=pref.digest_cadence.value,
            events=_events_json(pref),
            updated_at=datetime.now(UTC),
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[NotificationPreference.user_id],
                set_={
                    "in_app_enabled": stmt.excluded.in_app_enabled,
                    "push_enabled": stmt.excluded.push_enabled,
                    "email_mode": stmt.excluded.email_mode,
                    "quiet_hours_enabled": stmt.excluded.quiet_hours_enabled,
                    "quiet_hours_start": stmt.excluded.quiet_hours_start,
                    "quiet_hours_end": stmt.excluded.quiet_hours_end,
                    "digest_cadence": stmt.excluded.digest_cadence,
                    "events": stmt.excluded.events,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )
