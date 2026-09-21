"""收件箱的那一行 —— 全平台只有这一张通知表（结论 58）。

## 为什么只剩一张

在这之前有两张，互不知道对方：`alerts` 是平台报告自己（房间里 @ 了谁、一轮完了、
一件事等人拍板），`notification` 是人对人（回帖、邀请、审批结果）。两边各有自己的
读写路径、各有自己的未读数，于是「一个人被 @ 了而人不在页面上什么都收不到」
（#1035）不是哪一边的 bug，是两张表都只看得见自己那一半。

并起来之后，**一行就是一个人收到的一条通知**，不管它是谁发的。投递账本
（`delivery/ledger.py`）写的是这一张，站内信读的也是这一张。

## 收件人为什么有两个名字

`recipient_handle` 是名册上的名字，`receiver_id` 是账号池里的那一行。投递这一侧只
认 handle（I11）—— 房间的名册、@ 的目标、`cheese notify --to` 给的都是 handle；知是
那一侧的站内信按数字 id 查。项目收件箱写下的行两个都填（handle 在账号池里找不到对
应行时后者为空），知是那一侧写下的行只有 `receiver_id`。

**两侧的读各认各的行。** `recipient_handle` 为空的那些才是站内信
（`repositories._my_mail`），带着名册名字的那些只在项目收件箱里出现（`_mine_in`）。
平台报告自己的那几种在知是的铃铛里没有渲染器 —— 前端按 `type` 找模板，`MENTION`
那一个读的是 `payload` 里的 `mentioner`/`discussionTitle`，项目通知的文字在
`title`/`body` 上，落进去就是一排读不出内容的空壳，未读数却照加。

## 广播没有自己的形状

一条通知只对一个人。`alerts` 里 `target_handle IS NULL` 表示「这条谁都看得见」，
读的那一侧于是每一处都得写成「点了我的名 **或者** 谁的名都没点」—— 四处查询、一
处内存过滤，漏掉任何一处就是把别人的信念给了他。现在广播在**写入的时候**就展开成
一人一行（`services.ProjectNotificationService.create`），读的那一侧只剩一句相等。
"""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Sequence,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

notification_seq = Sequence("notification_seq")


class NotificationLevel(str, Enum):
    """打扰到什么程度（spec §3、§8.6）。

    `silent` 默默记下来，不点亮角标；`light` 对话里轻提一句；`strong` 强提醒。
    分级限流按它算（每个房间每天 2 条 light、每周 1 条 strong）。
    """

    silent = "silent"
    light = "light"
    strong = "strong"


class NotificationType(str, Enum):
    MENTION = "MENTION"
    REPLY = "REPLY"
    REACTION = "REACTION"
    PROJECT_INVITE = "PROJECT_INVITE"
    DEADLINE_REMIND = "DEADLINE_REMIND"

    TEAM_JOIN_REQUEST = "TEAM_JOIN_REQUEST"
    TEAM_INVITATION = "TEAM_INVITATION"
    TEAM_REQUEST_APPROVED = "TEAM_REQUEST_APPROVED"
    TEAM_REQUEST_REJECTED = "TEAM_REQUEST_REJECTED"
    TEAM_INVITATION_ACCEPTED = "TEAM_INVITATION_ACCEPTED"
    TEAM_INVITATION_DECLINED = "TEAM_INVITATION_DECLINED"
    TEAM_INVITATION_CANCELED = "TEAM_INVITATION_CANCELED"
    TEAM_REQUEST_CANCELED = "TEAM_REQUEST_CANCELED"

    #: 平台在房间里说的、要人动手的那一句（`app.domain.agent.announce`）。所有
    #: 平台提示共用这一个码：要显示的文字是后端给的 `payload.content`，前端不按
    #: 类别拼模板，具体是哪件事看 `payload.eventType`。
    ROOM_NOTICE = "ROOM_NOTICE"

    #: 芝士提出待确认问题，本轮停止等待回答（`announce.notify_question`）。
    #: 与 `ROOM_NOTICE` 分开是因为它不是平台说的：文字是芝士自己的话，不受那一行
    #: 40 字的约束，前端也要按「一个问题」渲染，而不是按一条平台提示。
    CHEESE_QUESTION = "CHEESE_QUESTION"

    #: 平台报告自己的那四种（原 `AlertKind`）。值保持小写原样：`cheese notify
    #: --kind` 和前端的 `NOTIF_KIND` 标签表按它写，存量行里也是这几个字。
    CHANGE_ALERT = "change_alert"  # 变更提醒
    DECISION_REQUEST = "decision_request"  # 决策请求 (带选项)，拍板之前不离开收件箱
    ACCEPT_REQUEST = "accept_request"  # 验收卡 (点名)
    HEARTBEAT = "heartbeat"  # 巡检催办
    # 房间里被 @ 走 `MENTION` —— 「有人点了你的名」在两个产品里是同一件事，
    # 原来的 `AlertKind.mention` 没有第二份语义。


class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (
        Index(
            "idx_notification_receiver_read_created",
            "receiver_id",
            "read",
            "created_at",
            postgresql_using="btree",
        ),
        Index(
            "idx_notification_aggregation",
            "receiver_id",
            "aggregation_key",
            "aggregate_until",
            postgresql_using="btree",
        ),
        # 项目收件箱的每一条读都带着这两列（我的信 + 这个项目），未读数还带 `read`。
        Index(
            "idx_notification_project_recipient",
            "project_id",
            "recipient_handle",
            "read",
        ),
        # 「这个房间 @ 过我没有、还未读没有」——话题列表的相关性一次查完。
        Index("idx_notification_topic_recipient", "topic_id", "recipient_handle"),
    )

    id: Mapped[int] = mapped_column(BigInteger, notification_seq, primary_key=True)

    #: 收件人在账号池里的那一行。handle 找不到账号时为空，见模块说明。
    receiver_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 收件人在名册上的名字 —— 投递这一侧认的就是它（I11）。
    recipient_handle: Mapped[str | None] = mapped_column(
        String(length=64), nullable=True, index=True
    )

    type: Mapped[NotificationType] = mapped_column(
        "type", String(length=255), nullable=False
    )

    #: 这条通知关于哪个项目 / 哪个房间。人对人的那几种没有项目，两列都空。
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True
    )

    #: 打扰到什么程度。人对人的那几种不分级，为空 —— 它们不进项目角标，项目角标
    #: 的查询本来就按 `project_id` 圈过一遍。
    level: Mapped[NotificationLevel | None] = mapped_column(
        String(length=16), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(length=300), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    metadata_payload: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    content: Mapped[dict | None] = mapped_column("content", JSONB, nullable=True)

    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: 一条决策请求在被答复之前不离开收件箱 —— 读过不等于答过。选的哪一项写在
    #: `metadata_payload["resolved_choice"]`。
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 👍/👎（spec G3）：null / "up" / "down"。
    feedback: Mapped[str | None] = mapped_column(String(length=8), nullable=True)

    is_aggregatable: Mapped[bool] = mapped_column(
        "is_aggregatable", Boolean, nullable=False, default=False
    )
    aggregation_key: Mapped[str | None] = mapped_column(
        "aggregation_key", String(length=255), nullable=True
    )
    aggregate_until: Mapped[datetime | None] = mapped_column(
        "aggregate_until", DateTime(timezone=True), nullable=True
    )
    finalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: 这一行是哪一笔投递送来的（`delivery/ledger.py` 的去重键，全表唯一）。
    #:
    #: 「恰好一次」靠的就是它：一次发送在回写 `sent_at` 之前崩掉，补发会把同一笔再
    #: 发一遍，插入撞上这个唯一约束，收件人手里仍然只有一条。搬家进来的旧 alert 行
    #: 也带着键（`alert:<原 uuid>:<收件人>`），搬家那条迁移靠它认出哪些 alert 已经
    #: 落过行，整条跳过 —— 而不是逐行去撞约束：第二遍跑的时候名册可能已经变了，逐行
    #: 撞约束拦不住那批「窗口期里才进房间的人」凭空多出来的行。
    #: NULL 只存在于账本之前写下的旧行，而 Postgres 的唯一索引不认为两个 NULL 相
    #: 等，所以它们之间互不排斥。
    delivery_key: Mapped[str | None] = mapped_column(
        String(length=160), nullable=True, unique=True
    )

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
