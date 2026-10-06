"""通知设置页要的两个接口：读一个人的通知偏好，和整份写回。

只服务调用者自己 —— 偏好就是「我自己的通知怎么发」，没有代别人改这一回事，所以
两个接口都不收任何指明收件人的参数，收件人就是带着令牌的那个人（`require_auth_user`）。

读取永远成功：没保存过就回设计稿的默认（`preferences.default_preferences`），不是
404。写入是整份替换（一次 PUT），前端拿到的一直是完整的一份，不必自己拼增量。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.db import get_db
from app.core.errors import ValidationError
from app.domain.notification.preferences import (
    ChannelChoice,
    DigestCadence,
    EmailMode,
    PreferenceCategory,
    Preferences,
    default_preferences,
    parse_hhmm,
    to_dict,
)
from app.domain.notification.preferences_models import PreferencesRepository

router = APIRouter(prefix="/notifications/preferences", tags=["notifications"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AuthUser = Annotated[AuthUserInfo, Depends(require_auth_user)]

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class EventChoiceIn(BaseModel):
    inApp: bool = True
    push: bool = False
    email: bool = False


class PreferencesIn(BaseModel):
    """整份偏好。缺的字段用设计稿的默认，于是老前端少传一列也不会把它清成 false。"""

    inAppEnabled: bool = True
    pushEnabled: bool = True
    emailMode: EmailMode = EmailMode.digest
    quietHoursEnabled: bool = True
    quietHoursStart: str = Field(default="22:00", pattern=_HHMM)
    quietHoursEnd: str = Field(default="08:00", pattern=_HHMM)
    digestCadence: DigestCadence = DigestCadence.weekly
    events: dict[str, EventChoiceIn] = Field(default_factory=dict)


def _build(body: PreferencesIn) -> Preferences:
    base = default_preferences()
    channels = dict(base.channels)
    for category in PreferenceCategory:
        raw = body.events.get(category.value)
        if raw is not None:
            channels[category] = ChannelChoice(
                in_app=raw.inApp, push=raw.push, email=raw.email
            )
    try:
        start = parse_hhmm(body.quietHoursStart)
        end = parse_hhmm(body.quietHoursEnd)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return Preferences(
        in_app_enabled=body.inAppEnabled,
        push_enabled=body.pushEnabled,
        email_mode=body.emailMode,
        quiet_hours_enabled=body.quietHoursEnabled,
        quiet_hours_start=start,
        quiet_hours_end=end,
        digest_cadence=body.digestCadence,
        channels=channels,
    )


@router.get("")
async def get_preferences(db: DbSession, auth_user: AuthUser) -> dict:
    pref = await PreferencesRepository(db).for_user(auth_user.user_id)
    return ok(to_dict(pref))


@router.put("")
async def save_preferences(
    body: PreferencesIn, db: DbSession, auth_user: AuthUser
) -> dict:
    pref = _build(body)
    await PreferencesRepository(db).save(auth_user.user_id, pref)
    await db.commit()
    return ok(to_dict(pref))
