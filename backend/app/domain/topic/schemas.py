"""Topic request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.presentation_meta import PresentationOut
from app.domain.topic.models import TopicKind, TopicStatus


class TopicCreate(BaseModel):
    project_id: uuid.UUID
    # A channel is named by whoever creates it.
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=500)
    parent_id: uuid.UUID | None = None
    # 私密频道: only the people in it see it. Its creator is its first.
    members_only: bool = False


class MemberActivityOut(BaseModel):
    """One member's activity, shaped like the room socket's ``activity`` frame.

    ``typing`` and ``expires_in`` stay declared here so this payload's shape does
    not move (the frontend reads several fields off it), but ``GET /topics``
    only ever emits ``working`` entries — see ``TopicOut.activity``.
    """

    member: str
    kind: Literal["typing", "working"]
    # Epoch seconds, as on the socket frames.
    since: float
    # Typing only: how long it lasts without another ping.
    expires_in: float | None = None


class MemberWaitOut(BaseModel):
    # The awaited member's handle; None when the timeline does not say whose.
    member: str | None
    reason: str
    since: datetime
    pr: int | None = None


class TopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    # Its number in the project's addresses; None for a private chat.
    number: int | None = None
    title: str
    description: str | None = None
    kind: TopicKind
    status: TopicStatus
    # 私密频道: listed, searched and read only by the people in it.
    members_only: bool = False
    # Who may speak in the main line: project role keys, None for everyone.
    speaker_roles: list[str] | None = None
    thread_replies_open: bool = True
    created_at: datetime
    # The topics ROW's mtime: it moves when the topic's own fields change
    # (title, status, session id), NOT when a block lands in it. For "was there
    # activity here", read `last_activity_at`.
    updated_at: datetime
    # 最后活动时间: the newest block in the topic, falling back to its creation.
    # Derived per query, so only the endpoints that ask for it
    # (list_topics/get_topic) fill it in; elsewhere it stays None.
    last_activity_at: datetime | None = None
    # 这间房里**还没恢复**的失败从什么时候开始（`block/waits.MemberWaits.
    # unresolved_failures`）：一条活/一轮停下报错，五分钟了没人接手，侧栏画红灯。
    # None = 没有未恢复的失败（恢复了、或本来就没坏过），不亮。
    #
    # 汇总的是这间房自己那条线、它的支线，以及它名下**还没关**的活里最早的一条：
    # 名下一件活报错停下，上面那间房就是在出问题；一件活自己恢复只清它自己，别的
    # 活、这间房自己那条线照旧。只有 list/get 话题时填，别处一律 None（「没人算过」，
    # 不是「没有」）。
    incomplete_since: datetime | None = None
    # 交到这里、且**读的人还没点开看过**的活：这个频道名下有这么一件「卡片被采纳
    # 过、而他从那以后没在这间房或那件活上点开过」（`room_task/repositories.
    # unseen_deliveries`）。侧栏的**长绿**问的就是它——「这里做过事、都交付完了、
    # 你还没点开看过」，点开过就灭（推的是已读位，见那一段）。
    #
    # 算在频道这一行上，是因为做完的活已经不占侧栏一行了（活一关就从侧栏消失，只剩
    # 它那个频道）：不汇总上来的话，这条规矩在侧栏上无处可亮。
    #
    # 只对这个人自己参与的活成立（负责人或协作者），和未读对任务的规矩一致；没有
    # 登录的人一律 False。借了 `Unread` 那份判断的同一个理由：别人的交付不该在我
    # 的侧栏上喊我。
    unseen_delivery: bool = False
    # 这间房此刻在不在往前走：它自己那条线、它的支线、它名下的活里，任意一处有
    # runner 报着在跑的会话；或者名下的活（或房间自己那张卡）走在交付链路上。
    # 侧栏的**呼吸绿**问的就是它——「有一轮在跑，或者活已经递出去」。
    #
    # 和 `unseen_delivery` 分开：呼吸绿说的是「现在还在动」，常亮绿说的是「做完了、
    # 你还没看过」。只有 list/get 话题时填，别处一律 False（「没人算过」，不是
    # 「没有」）。
    moving: bool = False
    # 成员动态：此刻谁在这个房间里忙——有一轮在跑的 AI 队友。
    # 来源：`agent/realtime/activity.py`。
    # 房间自己没有状态，有的是成员在做什么。只有 list/get 话题时填。
    #
    # 不带打字的人。侧栏不画他们（`useTopicRail`），而打字条目的 `expires_in` 是「还
    # 差几秒过期」、每次请求现算，所以进了这份 body 就会把它的 ETag 打穿：只要有人在
    # 打字，几百 KB 的清单每 30 秒都命中不了 304、原样重传一遍（`routes/topics.py`）。
    # 房间 socket 上那份 `activity_snapshot` 仍是完整的（含打字）—— 那是房内实时显示。
    activity: list[MemberActivityOut] = Field(default_factory=list)
    # 这间房的 AI 队友：没人点名的话交给名册上的哪一席（`TopicMemberService.
    # addressable_agent_handle`），一位都没坐就是 None。行上那句状态说到队友时
    # （「{agent}在做」）叫的是它，项目可以给它改名。同样只有 list/get 话题时填。
    agent_handle: str | None = None
    # 这个房间在等哪位成员、从什么时候开始、为什么（`block/waits.py`）：它那一轮
    # 报错了、有人点了它的名还没回、卡停在要它修的地方。多久算太久在前端按当下的
    # 钟判。同样只有 list/get 话题时填。
    waits: list[MemberWaitOut] = Field(default_factory=list)
    # Lifecycle markers (spec §6.3): who accepted, when archived, and — for an
    # upgraded topic — which block it grew from (for the 活引用 back-link).
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    archived_at: datetime | None = None
    cleanup_due_at: datetime | None = None
    # Whether the caller manages this channel: its creator, or someone who
    # manages the project. They rename it, describe it, archive it and choose
    # who is in it. 综合 is renamed and described like any other; it is never
    # archived.
    can_manage: bool = False
    upgraded_from_block_id: uuid.UUID | None = None
    # What this channel is to the CALLER. Derived per caller, so — like
    # `last_activity_at` and `activity` — only list_topics/get_topic fill them;
    # elsewhere both stay False, meaning "nobody computed this", not "no".
    #
    # `joined`: I am in it — 综合 always, any other channel once I joined it or
    # was handed work in it. The sidebar lists these and no others, and only a
    # member speaks in its main line.
    # `awaits_me`: it is waiting on ME to decide right now — a card routed to
    # me is still pending, a decision request to me is unanswered, or 芝士 is
    # stopped on a question only I can answer (I started the turn). An unread @
    # is deliberately NOT here: unread already has its own badge.
    joined: bool = False
    awaits_me: bool = False
    # 有人回复了我、我还没处理：这间房里（它自己那条线，或它名下的支线）有一条别人
    # 说的消息，答的是我写的某一块、或者点了我的名，而我从那以后没打开过这间房
    # （`TopicRepository.replied_to_me`）。侧栏的黄灯问的也是它 —— 和 `awaits_me`
    # 同一盏灯，都是「有事等你」。
    #
    # 和 `awaits_me` 那条「未读的 @ 不算」不同，未读的 @ 在这里**算**：这盏灯要的
    # 正是「有人回了我」这件事本身，未读角标只说有多少条。打开这间房就算处理了。
    #
    # 和 `awaits_me` 一样是对**看的人**算的：只有 list_topics/get_topic 会填，别处
    # 一律 False（「没人算过」，不是「没有」）。名下的活不在里面 —— 活有自己那一行。
    replied_to_me: bool = False
    # 看板上这一格 —— 同一个 `presentation` 结构，一条活和一个房间用同一套词，因为
    # 侧栏把它们画在一起。和上面几个派生字段同一条规矩：只有 list_topics/get_topic
    # 会填，别处是 None（「没人算过」）。
    presentation: PresentationOut | None = None


class CheckResultIn(BaseModel):
    """What the quick check said (`cheese check`).

    `ok=False` covers a red check AND one that ran out of its time budget: the
    budget is the point, so exceeding it is a result, not an absence of one.
    """

    ok: bool
    detail: str = ""


class ConclusionIn(BaseModel):
    """收卡. Empty means "the worker's last word stands" — the platform already
    wrote it on the card, so the room usually has nothing to add."""

    conclusion: str = ""
    reporter_handle: str | None = Field(default=None, max_length=64)
    contributor_handles: list[str] | None = None


class DocEditIn(BaseModel):
    content: str
    # The `doc_version` this edit is based on — 0 for "there is no doc yet".
    # Required, and deliberately so: this doc is only ever written whole, so a
    # writer with no version is a writer about to erase whatever it did not
    # read. Everything that writes here has just read the doc.
    expected_version: int = Field(ge=0)
    operation_id: uuid.UUID | None = None
