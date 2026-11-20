"""通知设置页要的两个接口：读一个人的通知偏好，和整份写回。

只服务调用者自己 —— 偏好就是「我自己的通知怎么发」，没有代别人改这一回事，所以
两个接口都不收任何指明收件人的参数，收件人就是带着令牌的那个人（`require_auth_user`）。

读取永远成功：没保存过就回设计稿的默认（`preferences.default_preferences`），不是
404。写入是整份替换（一次 PUT），前端拿到的一直是完整的一份，不必自己拼增量。
"""

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.assistant_deps import tell_change
from app.api.auth import ActorResolverDep
from app.api.deps import get_consumptions
from app.api.request_deps import AuthUser, DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import AuthenticationRequiredError, ValidationError
from app.core.sandbox_auth import DelegatedClaims
from app.core.sentences import say
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.assistant import changes
from app.domain.notification.preferences import (
    ChannelChoice,
    DigestCadence,
    EmailMode,
    PreferenceCategory,
    Preferences,
    default_preferences,
    format_hhmm,
    parse_hhmm,
    to_dict,
)
from app.domain.notification.preferences_models import PreferencesRepository

router = APIRouter(prefix="/notifications/preferences", tags=["notifications"])


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


@router.put("", dependencies=[ROUTE_DECIDES])
async def save_preferences(
    body: PreferencesIn, db: DbSession, auth_user: AuthUser
) -> dict:
    pref = _build(body)
    await PreferencesRepository(db).save(auth_user.user_id, pref)
    await db.commit()
    return ok(to_dict(pref))


# A person's own 芝士 sets these for them (`cheese_settings`): one setting
# at a time, by the name it knows it by, kept as a change the person can undo
# from the panel (`assistant.changes`).

#: What 芝士 may set, and how the person's settings page names it.
AGENT_SETTINGS = {
    "in_app": ("站内通知", "on / off"),
    "push": ("推送通知", "on / off"),
    "email": ("邮件", "instant 每条都发 / digest 汇总 / off"),
    "quiet_hours": ("免打扰时段", "off，或 22:00-08:00 这样的时段"),
    "digest": ("汇总频率", "daily / weekly / off"),
}
_ON = {"on": True, "off": False, "true": True, "false": False}


def _value_of(pref: Preferences, key: str) -> str:
    if key == "in_app":
        return "on" if pref.in_app_enabled else "off"
    if key == "push":
        return "on" if pref.push_enabled else "off"
    if key == "email":
        return pref.email_mode.value
    if key == "digest":
        return pref.digest_cadence.value
    if not pref.quiet_hours_enabled:
        return "off"
    return f"{format_hhmm(pref.quiet_hours_start)}-{format_hhmm(pref.quiet_hours_end)}"


def _set(pref: Preferences, key: str, value: str) -> Preferences:
    value = value.strip().lower()
    try:
        if key in ("in_app", "push"):
            on = _ON[value]
            field = "in_app_enabled" if key == "in_app" else "push_enabled"
            return replace(pref, **{field: on})
        if key == "email":
            return replace(pref, email_mode=EmailMode(value))
        if key == "digest":
            return replace(pref, digest_cadence=DigestCadence(value))
        if key == "quiet_hours":
            if value == "off":
                return replace(pref, quiet_hours_enabled=False)
            start, _, end = value.replace("～", "-").replace("~", "-").partition("-")
            return replace(
                pref,
                quiet_hours_enabled=True,
                quiet_hours_start=parse_hhmm(start),
                quiet_hours_end=parse_hhmm(end),
            )
    except (KeyError, ValueError) as exc:
        name, choices = AGENT_SETTINGS[key]
        raise ValidationError(
            say("assistantSettingValue", name=name, choices=choices)
        ) from exc
    raise ValidationError(
        say("assistantSettingUnknown", keys=", ".join(AGENT_SETTINGS))
    )


def _said(pref: Preferences) -> str:
    return "\n".join(
        f"- {name}（{key}）：{_value_of(pref, key)}；可选 {choices}"
        for key, (name, choices) in AGENT_SETTINGS.items()
    )


async def _delegated_person(resolver) -> tuple[int, DelegatedClaims]:
    claims = resolver.delegation()
    actor = await resolver.resolve()
    if claims is None or actor.user_id is None:
        raise AuthenticationRequiredError(say("invalidToken"))
    return actor.user_id, claims


class AgentSettingIn(BaseModel):
    key: str = ""
    value: str = ""


@router.post("/agent/read", include_in_schema=False, dependencies=[ROUTE_DECIDES])
async def agent_read_preferences(db: DbSession, resolver: ActorResolverDep) -> dict:
    """cheese_read settings: the person's notification settings, in words."""
    user_id, _ = await _delegated_person(resolver)
    pref = await PreferencesRepository(db).for_user(user_id)
    return ok({"text": "你的通知设置：\n" + _said(pref)})


@router.post("/agent/set", include_in_schema=False, dependencies=[ROUTE_DECIDES])
async def agent_set_preference(
    body: AgentSettingIn,
    db: DbSession,
    resolver: ActorResolverDep,
    questions: Annotated[Consumptions, Depends(get_consumptions)],
) -> dict:
    """cheese_settings: one of the person's notification settings,
    changed for them and kept as a change they can undo."""
    user_id, claims = await _delegated_person(resolver)
    if body.key not in AGENT_SETTINGS:
        raise ValidationError(
            say("assistantSettingUnknown", keys=", ".join(AGENT_SETTINGS))
        )
    repo = PreferencesRepository(db)
    before = await repo.for_user(user_id)
    after = _set(before, body.key, body.value)
    await repo.save(user_id, after)
    name = AGENT_SETTINGS[body.key][0]
    row = await changes.report(
        db,
        user_id=user_id,
        work=claims.work,
        kind="settings",
        target="notifications",
        payload={
            "title": "通知设置",
            "changed": {
                body.key: {
                    "label": name,
                    "before": _value_of(before, body.key),
                    "after": _value_of(after, body.key),
                }
            },
        },
    )
    await db.commit()
    await tell_change(questions, claims, changes.view(row))
    return ok({"text": f"已把{name}改成 {_value_of(after, body.key)}。"})


def _settings_parts(payload: dict) -> list[str]:
    return list((payload.get("changed") or {}).keys())


async def _settings_back(db, resolver, same, parts: list[str], redo: bool) -> dict:
    """Put settings back as they were before the answer changed them, or as
    it left them, as the person's own change."""
    actor = await resolver.resolve()
    if actor.user_id is None:
        raise AuthenticationRequiredError(say("invalidToken"))
    repo = PreferencesRepository(db)
    pref = await repo.for_user(actor.user_id)
    order = list(reversed(same)) if redo else list(same)
    for part in parts:
        for change in order:
            values = (change.payload.get("changed") or {}).get(part)
            if values is not None:
                pref = _set(pref, part, values["after" if redo else "before"])
                break
    await repo.save(actor.user_id, pref)
    return {"settings": to_dict(pref)}


changes.register("settings", parts=_settings_parts, apply=_settings_back)
