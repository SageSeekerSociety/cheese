"""一条投递事件的词汇：它的码、它的身份、它的去重键。

房间里发生了一件事，要告诉某些人 —— 这件事是什么、它是哪一件事，这两样东西每一
层都在说：第 2 层的团队审批和项目邀请、第 3 层的空间公告、第 4 层的讨论回复、第 5
层的平台提示、第 6 层的定时提醒。它们原来都得向上够到第 5 层（`notification.models`
的码表、`delivery/ledger.py` 的事件），C5 分层契约里冻着一批这样的边。

## 为什么住在第 1 层

和 `addressing.py`（这件事送给谁）配成一对：那个说「谁」，这个说「什么」。两样都
是纯数据 —— 一个枚举、一个冻结 dataclass、两个纯函数，不碰 session、不碰任何一张
表。真正写账本的那一半（`delivery/ledger.py` 的 `deliver`）留在第 5 层：它要落行、
要发站内信和邮件，本来就该在高处；调它的人从上面把这次调用递进来。

搬的是**数据**，不是行为。`NotificationType` 的取值是前后端共用的契约（前端的
`NOTIF_KIND` 标签表、`cheese_notify --kind`、存量行里存的字面量），改它要回到产品
那边，不能单方面动。`notification.models` 把它再导一次，读它的地方不用改。

## 写它的那几个调用

下面那几个 `Protocol` 是账本的**写入口**：把事件交出去、事情变了改一条、事情撤了
收回一条、卡有了结果结掉等它的那些话。它们的形状就是 `delivery/ledger.py` 里同名
函数的形状，实现留在第 5 层（要落行、要发站内信和邮件），低层要用就在构造时把它
递进来 —— 和 `webhook/ports.py` 的 `Poster`、`integration/ports.py` 的 `RoomFiles`
同一条规矩：谁调用谁给。

声明摆在这里而不是每个消费方各写一份，是因为要的东西完全一样；名字跟着被递进来的
那个函数（`Deliver` 收的就是 `ledger.deliver`），读的时候两边对得上。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.addressing import Addressed

#: 社交事件 id 的命名空间。固定值：换掉它等于把在途的去重键全部作废，同一件事会
#: 再通知一遍。
_EVENT_NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_DNS, "notification.cheese")


class NotificationType(str, Enum):
    MENTION = "MENTION"
    REPLY = "REPLY"
    REACTION = "REACTION"
    PROJECT_INVITE = "PROJECT_INVITE"
    #: The owner offers you their project (`membership.OwnershipService`).
    PROJECT_TRANSFER = "PROJECT_TRANSFER"

    TEAM_JOIN_REQUEST = "TEAM_JOIN_REQUEST"
    TEAM_INVITATION = "TEAM_INVITATION"
    TEAM_REQUEST_APPROVED = "TEAM_REQUEST_APPROVED"
    TEAM_REQUEST_REJECTED = "TEAM_REQUEST_REJECTED"
    TEAM_INVITATION_ACCEPTED = "TEAM_INVITATION_ACCEPTED"
    TEAM_INVITATION_DECLINED = "TEAM_INVITATION_DECLINED"
    TEAM_INVITATION_CANCELED = "TEAM_INVITATION_CANCELED"
    TEAM_REQUEST_CANCELED = "TEAM_REQUEST_CANCELED"

    #: 我参与过的支线里有人回复了（`agent.mentions._tell_thread`）。走投递账本，
    #: `payload` 带频道（``topicId``）、支线（``threadId``）和那条消息（``blockId``）。
    THREAD_REPLY = "THREAD_REPLY"

    #: 平台在房间里说的、要人动手的那一句（`app.domain.agent.announce`）。所有
    #: 平台提示共用这一个码：要显示的文字是后端给的 `payload.content`，前端不按
    #: 类别拼模板，具体是哪件事看 `payload.eventType`。
    ROOM_NOTICE = "ROOM_NOTICE"

    #: 芝士提出待确认问题，本轮停止等待回答（`announce.notify_question`）。
    #: 与 `ROOM_NOTICE` 分开是因为它不是平台说的：文字是芝士自己的话，不受那一行
    #: 40 字的约束，前端也要按「一个问题」渲染，而不是按一条平台提示。
    CHEESE_QUESTION = "CHEESE_QUESTION"

    #: 一个 agent 会话开始在某人登记的自有设备上干活，告诉设备的主人：哪个项目、
    #: 哪个房间、哪个 agent，能不能访问整台机器（#1900 第 5 步）。只是告知，不等
    #: 主人批准；一次会话开工一条，不是每轮一条（`machine.session_work`）。
    DEVICE_IN_USE = "DEVICE_IN_USE"

    #: 空间里发了一条公告，告诉这个空间里除发布人以外的每个人
    #: （`space.announcement_service`）。走哪个渠道由收件人的偏好矩阵决定
    #: （`preferences` 的「空间公告」一行：默认站内 + 邮件，不推送）。改公告不再发；
    #: 删公告连它发出去的这些一起撤回。
    SPACE_ANNOUNCEMENT = "SPACE_ANNOUNCEMENT"

    #: 空间里一道题目上发生的、要领取者或出题人知道的事（`task_notices`）：领取
    #: 通过或没通过、提交批改了、提交要求改了、个人提交期限改了、提交期限还剩三天、
    #: 有人过了期限没交。说的话是 `payload.content`（带键），哪一种看
    #: `payload.event`。
    TASK_NOTICE = "TASK_NOTICE"
    #: 提交期限只剩一天、还没交：和 `TASK_NOTICE` 分开，因为只有它推送。
    TASK_DUE_SOON = "TASK_DUE_SOON"

    #: 个人芝士自己做事时告诉这个人的（`routine.personal`）：一条例行事务运行完了、
    #: 有操作待他确认、例行事务因额度或久未登录暂停了，或者芝士用
    #: `cheese_notify` 发给他的一句话。说的话是 `payload.content`（平台的句子
    #: 带键），哪一种看 `payload.event`；`payload.assistantConversation` 是点开要打开
    #: 的那段对话。
    ASSISTANT_NOTICE = "ASSISTANT_NOTICE"

    #: 团队、项目或成员的额度快用完了（`usage.alerts`）。收件人是会被停下的人：
    #: 团队额度到团队全体、项目预算到项目里的人、成员上限到本人。说的话是
    #: 平台算好的那一句（`payload.content` 带键），哪一种看 `payload.layer`。
    CREDIT_WARNING = "CREDIT_WARNING"

    #: 平台报告自己的那三种（原 `AlertKind`）。值保持小写原样：`cheese_notify
    #: --kind` 和前端的 `NOTIF_KIND` 标签表按它写，存量行里也是这几个字。
    CHANGE_ALERT = "change_alert"  # 变更提醒
    DECISION_REQUEST = "decision_request"  # 决策请求 (带选项)，拍板之前不离开收件箱
    ACCEPT_REQUEST = "accept_request"  # 验收卡 (点名)
    # 房间里被 @ 走 `MENTION` —— 「有人点了你的名」在两个产品里是同一件事，
    # 原来的 `AlertKind.mention` 没有第二份语义。


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    """一条要投出去的事件 —— 窄到只剩账本要记的部分。

    `id` 是这条事件的身份（房间里那条 block 的 id），去重键跟着它走，所以调用点重
    算一遍寻址不会变成第二次打扰。`occurred_at` 是事情发生的时刻，不是投递的时刻。
    """

    id: uuid.UUID
    type: NotificationType
    payload: dict
    #: **必填**。给它一个「现在」的默认值，等于让每个调用点都可以不声不响地把投递的
    #: 时刻当成事件的时刻，而这个字段整条设计都在说那两个不是一回事。
    occurred_at: datetime


#: Payload key of an instruction that only the newest of its kind is worth
#: reading: what it says is the current state of one thing (a PR's checks, its
#: conflict), so an earlier one with the same value that has not reached the
#: session yet is replaced, not stacked (`delivery.agent.record_task_instruction`).
REPLACES: Final = "replaces"


def dedup_key(event_id: uuid.UUID, handle: str) -> str:
    """这条事件发给这个人的那一次投递的身份。跟事件走，不跟发送尝试走。"""
    return f"{event_id}:{handle}"


def event_id_for(type_: NotificationType, record_id: int | str) -> uuid.UUID:
    """长在一条领域记录上的那种事件的身份：哪条记录，发生了哪件事。

    `record_id` 是引发它的那条记录（申请、邀请、讨论回复，或一次会话开工的那份
    租约），`type_` 是那条记录上发生的这一件事 —— 两样都要：一条邀请从发出到被
    取消是同一条记录上的两件事，只按记录算，第二件就会撞上第一件的去重键，收件人再也收不到取消那一条。

    算出来的 id 只取决于这两样，所以同一件事重算一遍得到同一个 id：重试、补发、同
    一条事件被算两遍，收件人只被打扰一次。当场 `uuid4()` 得到的是**这一次调用**的
    身份，那就等于没有去重。
    """
    return uuid.uuid5(_EVENT_NAMESPACE, f"{type_.value}:{record_id}")


class Deliver(Protocol):
    """把一条事件交给账本：算出收件人，落投递行，发出去。"""

    async def __call__(
        self, session: AsyncSession, event: DeliveryEvent, addressed: Addressed
    ) -> None: ...


class Retract(Protocol):
    """这件事不作数了：它发出去的每一笔连同收件箱里那一行一起删掉。"""

    async def __call__(self, session: AsyncSession, event_id: uuid.UUID) -> None: ...


class Amend(Protocol):
    """这件事说的话改了：已经发出去的那些改成新的说法，不再发一遍。"""

    async def __call__(
        self, session: AsyncSession, event_id: uuid.UUID, payload: dict
    ) -> None: ...


class Settle(Protocol):
    """这件事办完了：它发出去的每一行记下结果、结掉、标成已读。"""

    async def __call__(
        self, session: AsyncSession, event_id: uuid.UUID, outcome: dict
    ) -> None: ...


class SettleCard(Protocol):
    """这张验收卡办完了：关于它的每一条提示一并结掉；返回结掉的那几件事的事件 id。"""

    async def __call__(
        self, session: AsyncSession, card_id: uuid.UUID, outcome: dict
    ) -> set[uuid.UUID]: ...
