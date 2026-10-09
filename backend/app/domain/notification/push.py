"""浏览器推送：订阅的存取、这一条该不该推、以及推出去。

## 只推「下一步在人手上」的那两种

`ROOM_NOTICE` 和 `CHEESE_QUESTION` —— 平台在房间里说的那一句要人动手的话，和芝士停
在那里等回答的一个问题。别的类别码一个都不推。

这不是省事，是 #1084 定的判据：芝士跑一轮会产生几十条消息，按消息推送就是几十条推
送，人会直接把这个渠道关掉，之后真正要他动手的那一条也就收不到了。推送的价值全在
「收到就意味着该我动了」，掺进一条不用动手的，这个意思就没了。

## 推送的文字就是通知里的文字

不另写一套措辞：人在推送里读到的、在站内通知里读到的、回房间看到的，是同一句话。
这条在 `agent/announce.py` 里已经定过一次，这里只是同一条规矩的第三个渠道。

那句话用收件人自己选的语言说（`user.language`）：推送是服务端替一个人加密好发出去
的，浏览器收到就原样显示，没有 APNs `loc-key` 那样让收件端查词表的机会，所以只能在
这里按收件人渲染。房间里那一行的键在 `payload["message"]`，词表是前端那一份
（`backend/app/core/sentences.py`）。

## 服务端看不到内容

Web Push 的内容由**浏览器的**密钥加密（`p256dh` / `auth`），服务端加密完就再也解不
开。所以这张表存的不是「发过什么」，只是「往哪儿发、用哪把钥匙」。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import LOCALES, in_language, render, say
from app.domain.notification.models import NotificationType
from app.domain.notification.push_models import PushSubscription

logger = logging.getLogger(__name__)

#: 会推送的类别码。见模块说明 —— 这张表短是判据，不是遗漏。
PUSHABLE: Final = frozenset(
    {NotificationType.ROOM_NOTICE, NotificationType.CHEESE_QUESTION}
)


class PushSubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        *,
        user_id: int,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None,
    ) -> None:
        """记下一个订阅；同一个 endpoint 再来一次就覆盖。

        upsert 而不是先查后插：同一个浏览器会反复订阅（权限重新授予、service
        worker 换代、清了站点数据又装回来），每次都换回同一个 endpoint。而两把
        加密材料**可能变了**，所以要覆盖而不是忽略 —— 拿旧钥匙加密的内容新浏览器
        解不开，症状是推送静默地不出现。

        `user_id` 也要覆盖：同一台机器换人登录，那个 endpoint 就属于新的人了。
        """
        stmt = insert(PushSubscription).values(
            user_id=user_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent,
            created_at=datetime.now(UTC),
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[PushSubscription.endpoint],
                set_={
                    "user_id": stmt.excluded.user_id,
                    "p256dh": stmt.excluded.p256dh,
                    "auth": stmt.excluded.auth,
                    "user_agent": stmt.excluded.user_agent,
                },
            )
        )

    async def for_user(self, user_id: int) -> list[PushSubscription]:
        stmt = select(PushSubscription).where(PushSubscription.user_id == user_id)
        return list((await self._session.scalars(stmt)).all())

    async def drop(self, endpoint: str, *, user_id: int | None = None) -> bool:
        """删掉一个订阅；返回真的删到了没有。

        两个调用点，条件不同，所以 `user_id` 是可选的而不是必填：

        - **用户主动退订**要带上他自己的 id。endpoint 唯一，但它不是秘密 —— 只按
          endpoint 删，等于任何登录用户拿到别人的 endpoint 就能替他关掉推送。
        - **投递发现订阅没了**（404/410）不带：那时判据来自推送服务商，和哪个人无
          关，而那一行本来就该消失。
        """
        stmt = delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
        if user_id is not None:
            stmt = stmt.where(PushSubscription.user_id == user_id)
        result = await self._session.execute(stmt)
        # DELETE 回来的是 CursorResult，运行时有 rowcount。
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def mark_delivered(self, endpoint: str, at: datetime) -> None:
        await self._session.execute(
            update(PushSubscription)
            .where(PushSubscription.endpoint == endpoint)
            .values(last_delivered_at=at)
        )


def push_text(
    type_: NotificationType, payload: dict[str, Any], locale: str | None
) -> tuple[str, str]:
    """(标题, 正文) —— 推送上显示的那两行，用 `locale` 说。

    标题就是后端已经算好的那一句，不在这里拼模板：`ROOM_NOTICE` 是房间里那一行
    （键在 `payload["message"]`，旁边的 `content` 是它的中文），`CHEESE_QUESTION`
    的 `question` 是芝士问的原话，不翻译。正文说「在哪个房间」，因为推送脱离了上
    下文出现在系统通知栏里，而「是哪件工作」正是人判断要不要立刻打开的依据。

    那一行说不成 `locale`（没带键的旧行、那种语言缺这句）就整条用中文：标题只剩存
    下的中文，正文也跟着它，免得一条通知半句中文半句英文。
    """
    room = str(payload.get("topicTitle") or "").strip()
    if type_ is NotificationType.CHEESE_QUESTION:
        question = str(payload.get("question") or "").strip()
        where = in_language(say("pushInRoom", room=room), locale) if room else ""
        title = question or in_language(say("pushQuestion"), locale)
        return (title, where or in_language(say("pushAwaitingAnswer"), locale))
    said = render(payload.get("message"), locale)
    if said is None:
        locale = LOCALES[0]
    content = (said or str(payload.get("content") or "")).strip()
    where = in_language(say("pushInRoom", room=room), locale) if room else ""
    return (content or in_language(say("pushNotice"), locale), where)


def away_text(count: int, locale: str | None) -> str:
    """离开期间攒下 `count` 条时，桌面端合成的那一条通知的标题。"""
    return in_language(say("pushAway", count=count), locale)


def push_link(payload: dict[str, Any]) -> str:
    """点开推送落到哪儿：**那条消息在的那条会话** —— 一个任务、一条支线，或频道自
    己那条线；地址说不清就是待办，那里一定列着这件事。

    一个任务里问的问题也归它所在的频道，可那条消息不在频道主线上：落回频道是一页
    没有这条消息的地方，人点开只看到「这条消息已不存在」（`announce._asked_where`
    记的正是这条会话）。

    和 `frontend/public/push-sw.js` 的 `readPayload` 是同一条规则。
    """
    project_id = payload.get("projectId")
    topic_id = payload.get("topicId")
    task_id = payload.get("taskId")
    thread_id = payload.get("threadId")
    if project_id and topic_id:
        if task_id:
            return f"/projects/{project_id}/topics/{topic_id}/tasks/{task_id}"
        if thread_id:
            return f"/projects/{project_id}/topics/{topic_id}/threads/{thread_id}"
        return f"/projects/{project_id}/topics/{topic_id}"
    return "/inbox"
