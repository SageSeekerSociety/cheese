"""一条消息里的点名：谁被点到、正文存成什么、落库之后谁收到什么。

一间房的时间线上，共享的那份事实就是正文本身（I13：没人被点名，谁都不动）。
这一块管的是「这条消息点了谁」从正文里读出来的全过程：

- **读**：`<@handle>` 是编码过的 token，不是从散文里猜的；`<@all>` / `<@here>`
  是保留的群播 token（`_resolve_mentions`）；`<#id>` 是话题引用（`_topic_refs`）。
- **写**：正文落库之前要过的那一层——人打的走 `person_mentions`，发布路径上
  的走 `project_refs_text`。「发出去会存成什么」由 `text_as_sent` 答（它留在
  chat.py，理由见下）。
- **落库之后**：`announce_mentions` 发强提醒、写 ``block.refs``、给点不到的
  handle 在消息旁边留一行。发送与编辑走的是同一个它。

**和另外两个「mention」模块不是一份东西，是同一根管子上的三段。**

- `app.domain.mentions`：friendly `@名字` / `@话题名` → 编码 token 的规范化
  （`expand_mention_names` / `canonicalize_refs`），文档 PUT、周报、结论回流也
  要过。它**产出** token。
- `app.domain.delivery.mention`：一条消息点到哪位 AI 队友，那条投递记在它那里
  （`record_mentions`），和定时投递同一本账。它决定**谁被叫起来**。
- 这里：token 怎么解析、正文存成什么、谁收强提醒、`block.refs` 写什么。

三段各答一个问题，判据都只有一份。

搬出来时按原样搬，行为一格没动。`PersonMentions` 跟着走；`SentText` 与
`text_as_sent` 留在 chat.py —— 它俩要 `_is_dm` 的那个答案，而它的调用点是
`block/editing.py`，签名是对外约定，插不进一个「这间房是不是私聊」的参数。
`person_mentions` 这一侧不需要：`_is_dm` 仍然只在 `room/turn.py` 里问，答案以 ``dm``
参数递进来，那个布尔因此在全仓仍然只有一个读点
（`test_is_private_read_points.py`）—— 连名字也不带过来，因为它按文件统计读点，
连形参与关键字实参都数。

`cloud_waiting_topics` 不是点名，是这次搬家顺手带过来的尾巴：它读的是同一份
时间线（同一批块），和上面几件一样是「这间房现在是什么状态」的只读判断，不该
在 chat.py 里再孤零零留一个 async 函数。

调用它们的是 chat.py 自己（`post_user_message`、`_persist_assistant_message`、
`post_system_event`、`ChatService.cloud_waiting_topics`）与 `block/editing.py`；
`ChatService` 上那些不读实例状态的方法留一行同名委托，调用点与测试都不用改。
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.core.text import markdown_preview
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import agent_instance_handle, looks_like_agent_handle
from app.domain.membership.roster import roster_rows
from app.domain.mentions import canonicalize_refs, expand_mention_names
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService

# Mentions are an ENCODED token, not guessed-from-prose: 芝士 (and the composer)
# emit `<@handle>`, which the platform resolves deterministically and the UI
# renders as a chip showing the member's name. A literal "@name" is just text.
_MENTION_RE = re.compile(r"<@([\w-]+)>")

# 群播 tokens (fusion-design §3): `<@all>` / `<@here>` are FIXED-LITERAL
# structured tokens (not natural-language semantics — rule 4), reserved handles
# the composer emits and the platform expands to the topic's roster. @all = the
# whole room; @here = active members (no presence yet, so = all — see below).
MENTION_ALL = "all"
MENTION_HERE = "here"
_SPECIAL_MENTIONS = frozenset({MENTION_ALL, MENTION_HERE})


_TOPIC_REF_RE = re.compile(r"<#([0-9a-fA-F-]{8,})>")


def _topic_refs(text: str) -> list[str]:
    """`<#topicId>` reference tokens in a message → topic refs (for linkage)."""
    return [f"topic:{tid}" for tid in dict.fromkeys(_TOPIC_REF_RE.findall(text or ""))]


# Canonicalization of friendly "@名字 / @话题名" now lives in app.domain.mentions
# so non-chat write paths (doc PUT, decision, conclusion) share the exact same
# rewrite. Re-exported under the old private name for existing callers/tests.
_expand_mention_names = expand_mention_names


def _resolve_mentions(text: str, roster: list[dict]) -> tuple[list[str], list[str]]:
    """Resolve <@handle> mention tokens against the roster. Returns
    (resolved_handles, unresolved_handles); unresolved = a token whose handle is
    not a member (a hallucinated handle → the platform flags it).

    An EMPTY roster means "this topic exposes no member list" (私聊, or a project
    carrying no explicit member rows), not "nobody is a member": with no list to
    check against a concrete handle can be neither confirmed nor refuted, so it
    is left out of BOTH lists — no notification, and no false 「项目里没有这个
    成员」 accusation against a real teammate.

    ``@all``/``@here`` are unaffected by any of that: they are expanded from the
    TOPIC's roster by `announce_mentions` (a DB read), never from this list,
    so an empty list must not silence a broadcast."""
    resolved: list[str] = []
    unresolved: list[str] = []
    if not text:
        return resolved, unresolved
    handles = {m["handle"] for m in roster}
    for h in dict.fromkeys(_MENTION_RE.findall(text)):
        # @all/@here are reserved broadcast tokens — always "resolved" (expanded
        # to the roster by announce_mentions), never flagged as a bad handle.
        if h in _SPECIAL_MENTIONS or h in handles:
            resolved.append(h)
        elif roster:
            unresolved.append(h)
    return resolved, unresolved


@dataclass(frozen=True, slots=True)
class PersonMentions:
    """A person's message as the room stores it, and what it was read against."""

    content: str
    roster: list[dict]
    agent_handles: list[str]
    by_seat: dict


async def person_mentions(
    session: AsyncSession,
    topic: Topic,
    content: str,
    agent,
    *,
    dm: bool,
) -> PersonMentions:
    """The mention rewrite a person's message gets in ``topic``: friendly
    ``@名字`` / ``@话题名`` become ``<@handle>`` / ``<#id>`` tokens, except in a
    private room, whose text is stored as written. ``agent`` is the room's
    agent (`AgentInstanceService.for_topic`), the name a seat not on the
    project roster answers to.

    ``dm`` 是调用方对 ``topic`` 问过 `_is_dm` 之后递进来的答案。名字刻意不
    叫 ``is_private``：那个布尔的读点按文件登记、是一道棘轮
    （`test_is_private_read_points.py`，连形参与关键字实参都数），这里既不该
    读它，也不该让它的名字在第二个模块里出现。

    Sending (`post_user_message`) and editing (`text_as_sent`) both read a
    person's words through here, so an edit stores what sending stores."""
    agent_handles = (
        await TopicMemberService(session).agent_handles(topic.id)
        if "@" in content
        else []
    )
    # Which agent each seat belongs to — 名册上 @ 到的是席位，而这一轮要跑
    # 起来的是它背后那个实例（记忆池的 key、署名用的 handle 都在实例上）。
    # 房间可以坐好几位，所以这张表按席位建，不按房间（#1192）。
    by_seat = (
        {
            agent_instance_handle(instance.id): instance
            for instance in await AgentInstanceService(session).list_for_project(
                topic.project_id
            )
        }
        if agent_handles
        else {}
    )
    roster: list[dict] = []
    if content:
        # Same backstop the doc/chat-reply paths already had, but the
        # human chat-send path used to skip it: a friendly "@Alice /
        # @handle / @话题名" is canonicalized into the structured token
        # (<@alice> / <#id>) BEFORE the block is stored, so it renders
        # as a clickable chip instead of leaking raw "@Alice" text.
        # 私聊没有名册可以解析（也不暴露成员列表），原样存下来。
        # 队友已经在这张名册上（``membership/roster.py``），每一位带着自己
        # 的名字，所以名字不再另拼一份——拼出来的那份就是第二份声明。
        roster = (
            []
            if "@" not in content or dm
            else await roster_rows(session, topic.project_id)
        )
        # 这间房真正坐着的 AI 席位先答这个名字：排到名册最前，名册上没有它
        # 的补一行。一间还挂着共用 `cheese` 席位的老房间（那一步是惰性的，
        # 等这间房的 agent 下次动手才迁，见 `migrate_shared_agent_seat`）在
        # 项目名册上没有对应的行——名册上叫「芝士」的是项目的默认实例，
        # 「@芝士」展开成它就等于 @ 了一个没坐在这间房里的队友：这一轮起不
        # 来，通知还发给了它。反过来也成立：成员表里历史上落过的一行
        # `cheese` 会以人的身份排在名册最前，在**正常**房间里把「@芝士」抢
        # 成 <@cheese>。谁坐在这间房里，谁先答。
        # 这是同一次读的一个渲染顺序，不是第二份名册——和 `roster_rows()`
        # 的定位一致。
        seated = set(agent_handles)
        if roster and agent_handles:
            row_of = {row["handle"]: row for row in roster}
            roster = [
                row_of[handle]
                if handle in row_of
                else {
                    "handle": handle,
                    "name": (
                        by_seat[handle].display_name
                        if handle in by_seat
                        else agent.display_name
                    ),
                }
                for handle in agent_handles
            ] + [row for row in roster if row["handle"] not in seated]
        # An AI teammate answers to its name only in a room it sits in. One
        # that does not is not addressed by an @ here (``addressed`` reads the
        # seats), so its name stays the words a person typed rather than a
        # chip that looks like it summoned someone.
        roster = [
            row for row in roster if not row.get("agent") or row["handle"] in seated
        ]
        if roster:
            topic_refs = [
                {"id": str(t.id), "title": t.title}
                for t in await TopicRepository(session).list_for_project(
                    topic.project_id
                )
                if t.kind != TopicKind.root and t.id != topic.id
            ]
            content = expand_mention_names(content, roster, topic_refs)
    return PersonMentions(content, roster, agent_handles, by_seat)


async def project_refs_text(
    session: AsyncSession, project_id: uuid.UUID, room_id: uuid.UUID, content: str
) -> str:
    """Friendly names and topic titles resolved against the whole project: the
    rewrite a message gets when an agent publishes it (`chat_send`,
    `todo_write`, before `_persist_assistant_message` does the rest) and when
    anyone speaks on a card (`say_on_task`)."""
    return await canonicalize_refs(
        session, project_id, content, exclude_topic_id=room_id
    )


async def announce_mentions(
    session: AsyncSession,
    topic: Topic,
    block: Block,
    author: str,
    roster: list[dict],
    *,
    before: str = "",
    flag_unresolved: bool = False,
) -> None:
    """What a message's mentions do once its text is written: notify each
    teammate it @s (spec §7: @人 = strong), record them in ``block.refs``, and,
    for an agent's message, leave a line beside it for each handle that names
    nobody. `<@all>`/`<@here>` expand to the topic's roster (群播,
    fusion-design §3).

    Sending and editing both come through here. ``before`` is the text the
    message had until an edit: whoever it already @-ed was told then, so only
    what the edit adds is announced."""
    text = block.content
    resolved, unresolved = _resolve_mentions(text, roster)
    told, flagged = _resolve_mentions(before, roster) if before else ([], [])
    fresh = [h for h in resolved if h not in told]
    concrete = [h for h in fresh if h not in _SPECIAL_MENTIONS]
    if any(h in _SPECIAL_MENTIONS for h in fresh):
        # Expand @all/@here to the topic's members. @here should be the
        # ACTIVE members, but there's no presence signal yet, so it equals
        # @all for now (TODO: intersect with presence once it lands).
        #
        # A broadcast reaches the room's humans only: every 芝士 in the room
        # already reads the timeline, so notifying them adds nothing. An
        # explicit <@handle> is different and is NOT filtered here — that is
        # how one agent addresses another, which a room hosting several 芝士
        # depends on.
        member_service = TopicMemberService(session)
        members, _ = await member_service.list_for_topic(topic.id)
        agents = set(await member_service.agent_handles(topic.id))
        concrete += [m.member_handle for m in members if m.member_handle not in agents]
    # Nobody needs a notification for their own message.
    targets = [h for h in dict.fromkeys(concrete) if h != author]
    if targets:
        notifs = ProjectNotificationService(session)
        preview = markdown_preview(text, 200)
        who = "芝士" if looks_like_agent_handle(author) else author
        for h in targets:
            await notifs.create(
                project_id=topic.project_id,
                level=NotificationLevel.strong,
                kind=NotificationType.MENTION,
                title=f"{who} 在「{topic.title}」@了你",
                body=preview,
                target_handle=h,
                topic_id=topic.id,
            )
    refs = [f"user:{h}" for h in resolved] + _topic_refs(text)
    if refs or before:
        block.refs = refs
    if not flag_unresolved:
        return
    for bad in unresolved:
        if bad in flagged:
            continue
        # Beside the message it is about, not in the room the message did not
        # go to — same landing as the message.
        landed = landing(
            EventAbout.task if block.task_id is not None else EventAbout.room,
            project_id=block.project_id,
            room_id=block.topic_id,
            task_id=block.task_id,
        )
        await BlockRepository(session).add(
            project_id=landed.project_id,
            topic_id=landed.topic_id,
            task_id=landed.task_id,
            author=author,
            author_type=AuthorType.participant,
            content=say("mentionUnknownMember", member=f"<@{bad}>"),
            kind=BlockKind.event,
            turn_id=block.turn_id,
            meta={"in_room": False},
        )


async def cloud_waiting_topics(
    session: AsyncSession, topic_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    waiting: list[uuid.UUID] = []
    blocks = BlockRepository(session)
    for topic_id in topic_ids:
        history = await blocks.list_for_topic(topic_id)
        events = [
            b
            for b in history
            if (b.meta or {}).get("event_type") == "cloud_provisioning"
        ]
        if events and (events[-1].meta or {}).get("state") == "waiting":
            waiting.append(topic_id)
    return waiting
