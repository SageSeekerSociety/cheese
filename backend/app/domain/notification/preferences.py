"""按用户的通知偏好 —— 哪一类事件走哪个渠道、免打扰与摘要（设计稿第 1 节）。

在这之前渠道是写死的：邮件对每条事件都发、关不掉；推送登录之后也关不掉。这里把
「哪一类、走哪个渠道」交给收件人，并在投递那一刻按它裁一遍（`outbox.py`）。

## 三个渠道与一个矩阵

`站内 / 浏览器推送 / 邮件` 三个渠道各有一个**总开关**（`in_app_enabled` /
`push_enabled` / `email_mode`），下面那张矩阵再按事件类别细分。总开关和矩阵都是
「与」：某一类要走进某个渠道，两边都得不关。这样「把邮件整个关掉」是一下子的事，
不必逐类去点。

`email_mode` 是三态而不是布尔：`instant` 立即发、`digest` 折进摘要、`off` 一封都不
发。免打扰（`quiet_hours`）只压推送与立即邮件 —— 摘要照常，它本来就是给「人不在的
时候」用的。

## 矩阵覆盖哪些类别码

设计稿的八行，只有一部分有今天的生产者（`CATEGORY_TYPES`）。没被任何类别覆盖的通知
类别码（房间提示、芝士提问、截止提醒……）走旧规矩：`PUSHABLE` 决定能不能推、
`MAILBOX_ONLY` 决定发不发邮件，只是总开关照样生效 —— 人把邮件关了，就不该再收到
任何一封。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from enum import Enum
from typing import Final

from app.domain.notification.models import NotificationType
from app.domain.notification.push import PUSHABLE

#: 只进站内收件箱的类别码：不发邮件（推送另有 `PUSHABLE` 那一道，它们也不在里
#: 面）。一条公告同时发给整个空间，一个百来人的班发一条就是百来封信 —— 矩阵里的
#: 「空间公告」默认发邮件，是这一档的例外，由矩阵说了算（`_email_wanted`）。
MAILBOX_ONLY: Final[frozenset[NotificationType]] = frozenset(
    {NotificationType.SPACE_ANNOUNCEMENT}
)

#: 默认安静时段：22:00 到次日 08:00（设计稿）。跨零点，`in_quiet_hours` 认得。
DEFAULT_QUIET_START: Final = time(22, 0)
DEFAULT_QUIET_END: Final = time(8, 0)


class PreferenceCategory(str, Enum):
    """矩阵里的那八行。值是写给前端的稳定键（驼峰），不要改。"""

    MENTION = "mention"  # 有人 @ 我
    REPLY = "reply"  # 我的消息被回复
    REACTION = "reaction"  # 有人回应了我的消息
    WAITS_ON_ME = "waitsOnMe"  # 等你处理的验收卡 / 决策请求
    DEVICE_IN_USE = "deviceInUse"  # 我的设备有人在用
    INVITATION = "invitation"  # 团队 / 项目邀请
    ANNOUNCEMENT = "announcement"  # 空间公告
    BILLING = "billing"  # 额度与计费提醒


class EmailMode(str, Enum):
    instant = "instant"
    digest = "digest"
    off = "off"


class DigestCadence(str, Enum):
    daily = "daily"
    weekly = "weekly"
    off = "off"


#: 类别 → 它替哪些通知类别码做决定。矩阵一行一类。
#:
#: 有的类别今天还没有生产者：`BILLING` 的额度/计费提醒还没接进来（`models` 里没有
#: 对应的类别码）。它照样留在模型与界面里 —— 少一行默认值等于以后接上时没有地方
#: 让人调。
CATEGORY_TYPES: Final[dict[PreferenceCategory, frozenset[NotificationType]]] = {
    PreferenceCategory.MENTION: frozenset({NotificationType.MENTION}),
    PreferenceCategory.REPLY: frozenset({NotificationType.REPLY}),
    PreferenceCategory.REACTION: frozenset({NotificationType.REACTION}),
    PreferenceCategory.WAITS_ON_ME: frozenset(
        {NotificationType.ACCEPT_REQUEST, NotificationType.DECISION_REQUEST}
    ),
    PreferenceCategory.DEVICE_IN_USE: frozenset({NotificationType.DEVICE_IN_USE}),
    PreferenceCategory.INVITATION: frozenset(
        {NotificationType.PROJECT_INVITE, NotificationType.TEAM_INVITATION}
    ),
    PreferenceCategory.ANNOUNCEMENT: frozenset({NotificationType.SPACE_ANNOUNCEMENT}),
    PreferenceCategory.BILLING: frozenset(),
}

#: 反查：一个通知类别码属于哪一行矩阵。没覆盖到的返回 None（走旧规矩）。
CATEGORY_OF: Final[dict[NotificationType, PreferenceCategory]] = {
    type_: category for category, types in CATEGORY_TYPES.items() for type_ in types
}

#: 矩阵的显示顺序，前端按它排。
CATEGORY_ORDER: Final[tuple[PreferenceCategory, ...]] = tuple(PreferenceCategory)


@dataclass(frozen=True, slots=True)
class ChannelChoice:
    """一类事件在三个渠道上的开关。"""

    in_app: bool
    push: bool
    email: bool


#: 设计稿矩阵的默认值，一格一格照抄：
#:
#: - @我 / 等你处理 / 团队项目邀请：三个渠道全开。
#: - 我的消息被回复：站内 + 邮件，不推送。
#: - 有人回应了我：只进站内（小事不刷屏）。
#: - 我的设备有人在用：站内 + 推送，不发邮件。
#: - 空间公告 / 额度计费：站内 + 邮件，不推送。
DEFAULT_CHANNELS: Final[dict[PreferenceCategory, ChannelChoice]] = {
    PreferenceCategory.MENTION: ChannelChoice(True, True, True),
    PreferenceCategory.REPLY: ChannelChoice(True, False, True),
    PreferenceCategory.REACTION: ChannelChoice(True, False, False),
    PreferenceCategory.WAITS_ON_ME: ChannelChoice(True, True, True),
    PreferenceCategory.DEVICE_IN_USE: ChannelChoice(True, True, False),
    PreferenceCategory.INVITATION: ChannelChoice(True, True, True),
    PreferenceCategory.ANNOUNCEMENT: ChannelChoice(True, False, True),
    PreferenceCategory.BILLING: ChannelChoice(True, False, True),
}


@dataclass(frozen=True, slots=True)
class Preferences:
    """一个人的整套偏好。缺省就是没开过设置页时的样子 —— 设计稿的默认。"""

    in_app_enabled: bool = True
    push_enabled: bool = True
    #: 设计稿「邮件」那一格默认停在 `摘要`（`即时 / 摘要 / 关闭` 里 `摘要` 是选中
    #: 的那一个），不是「每条都立即发」—— 那份稿子要修的正是后者。
    email_mode: EmailMode = EmailMode.digest
    quiet_hours_enabled: bool = True
    quiet_hours_start: time = DEFAULT_QUIET_START
    quiet_hours_end: time = DEFAULT_QUIET_END
    digest_cadence: DigestCadence = DigestCadence.weekly
    channels: Mapping[PreferenceCategory, ChannelChoice] = field(
        default_factory=lambda: dict(DEFAULT_CHANNELS)
    )

    def channel(self, category: PreferenceCategory) -> ChannelChoice:
        """这一类的三个开关；存量行缺哪一类就用默认补上。"""
        return self.channels.get(category) or DEFAULT_CHANNELS[category]


def default_preferences() -> Preferences:
    return Preferences()


@dataclass(frozen=True, slots=True)
class ChannelIntent:
    """一条通知此刻该走哪几个渠道（由 `resolve` 裁出来的结论）。"""

    #: 写不写进站内收件箱。
    in_app: bool
    #: 此刻发不发浏览器推送（免打扰会压掉）。
    push: bool
    #: 此刻发不发一封立即邮件（免打扰会压掉）。
    email: bool
    #: 要不要记一笔、等摘要把它合进去。立即邮件被压掉的、以及 `email_mode`
    #: 就是 `digest` 的，都落在这一档。
    digest: bool


def in_quiet_hours(pref: Preferences, now: datetime) -> bool:
    """`now` 在不在这个人的安静时段里。

    时段是墙上钟点（`22:00`–`08:00`），按 `now` 自己的时区算 —— 调用方传进来的通
    常是 UTC，所以这是「按 UTC 的安静时段」。跨零点（start > end）是常态，默认那
    一对就是；start == end 当没设。
    """
    if not pref.quiet_hours_enabled:
        return False
    start, end = pref.quiet_hours_start, pref.quiet_hours_end
    if start == end:
        return False
    current = now.timetz().replace(tzinfo=None)
    if start < end:
        return start <= current < end
    # 跨零点：过了 start 算夜里，没过 end 还算夜里。
    return current >= start or current < end


def _email_wanted(pref: Preferences, type_: NotificationType) -> bool:
    """这类事件要不要发邮件（不管此刻压不压、折不折摘要）。

    矩阵覆盖的按矩阵；没覆盖的按旧规矩：`MAILBOX_ONLY` 那几种不发邮件。
    """
    category = CATEGORY_OF.get(type_)
    if category is not None:
        return pref.channel(category).email
    return type_ not in MAILBOX_ONLY


def in_app_allowed(pref: Preferences, type_: NotificationType) -> bool:
    """这类事件此刻要不要写进站内收件箱。

    给直接写 `notification` 表那条路用（`services.ProjectNotificationService.create`）：
    矩阵覆盖的按矩阵，没覆盖的照旧进站内。
    """
    if not pref.in_app_enabled:
        return False
    category = CATEGORY_OF.get(type_)
    if category is None:
        return True
    return pref.channel(category).in_app


def resolve(
    pref: Preferences, type_: NotificationType, *, now: datetime
) -> ChannelIntent:
    """一条通知按这个人的偏好在 `now` 这一刻该走哪几个渠道。

    站内只受矩阵与总开关；推送多一道免打扰；邮件在这之上还要分「立即」与「摘要」：

    - `email_mode == off`：一封都不发（立即和摘要都没有）。
    - `email_mode == instant`：立即发；落在安静时段里的那一档发不出去（被压掉），
      改记进摘要。
    - `email_mode == digest`：全部记进摘要，不立即发。

    摘要本身还受 `digest_cadence`：它关掉就没有摘要可折。
    """
    category = CATEGORY_OF.get(type_)
    if category is not None:
        per_type_push = pref.channel(category).push
    else:
        per_type_push = type_ in PUSHABLE

    quiet = in_quiet_hours(pref, now)
    email_off = pref.email_mode is EmailMode.off
    email_on = _email_wanted(pref, type_) and not email_off
    digest_on = email_on and pref.digest_cadence is not DigestCadence.off

    instant = email_on and pref.email_mode is EmailMode.instant and not quiet
    # 立即发不出去的那些（安静时段压掉的、本来就选摘要的）改记进摘要。
    digest = digest_on and (pref.email_mode is EmailMode.digest or (email_on and quiet))

    return ChannelIntent(
        in_app=in_app_allowed(pref, type_),
        push=pref.push_enabled and per_type_push and not quiet,
        email=instant,
        digest=digest,
    )


def parse_hhmm(value: str) -> time:
    """`"22:00"` → `time(22, 0)`；不合形状就抛 `ValueError`。"""
    text = value.strip()
    hour_text, sep, minute_text = text.partition(":")
    if sep != ":" or not (hour_text.isdigit() and minute_text.isdigit()):
        raise ValueError(f"Not an HH:MM time: {value!r}")
    hour, minute = int(hour_text), int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Not an HH:MM time: {value!r}")
    return time(hour, minute)


def format_hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def to_dict(pref: Preferences) -> dict:
    """给路由的 JSON 形状。键名驼峰，前端照着它画。"""
    return {
        "inAppEnabled": pref.in_app_enabled,
        "pushEnabled": pref.push_enabled,
        "emailMode": pref.email_mode.value,
        "quietHoursEnabled": pref.quiet_hours_enabled,
        "quietHoursStart": format_hhmm(pref.quiet_hours_start),
        "quietHoursEnd": format_hhmm(pref.quiet_hours_end),
        "digestCadence": pref.digest_cadence.value,
        "events": {
            category.value: {
                "inApp": pref.channel(category).in_app,
                "push": pref.channel(category).push,
                "email": pref.channel(category).email,
            }
            for category in CATEGORY_ORDER
        },
    }


def with_channel(
    pref: Preferences, category: PreferenceCategory, choice: ChannelChoice
) -> Preferences:
    channels = dict(pref.channels)
    channels[category] = choice
    return replace(pref, channels=channels)
