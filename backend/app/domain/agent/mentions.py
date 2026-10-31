"""一条消息里的点名：谁被点到、正文存成什么、落库之后谁收到什么。

一间房的时间线上，共享的那份事实就是正文本身（I13：没人被点名，谁都不动）。
这一块管的是「这条消息点了谁」从正文里读出来的全过程：

- **读**：`<@handle>` 是编码过的 token，不是从散文里猜的；`<@all>` / `<@here>`
  是保留的群播 token（`_resolve_mentions`）；`<#id>` 是话题引用（`_topic_refs`）。
- **写**：正文落库之前要过的那一层——人打的走 `person_mentions`，发布路径上
  的走 `project_refs_text`。「发出去会存成什么」由 `turn/intake/rewrite.py`
  的 `text_as_sent` 答。
- **落库之后**：`announce_mentions` 把被点名的人和支线里的人交给投递账本
  （`delivery/ledger.py`，站内、推送、邮件按各人的偏好裁）、写 ``block.refs``、
  给点不到的 handle 在消息旁边留一行。发送与编辑走的是同一个它。

**和另外两个「mention」模块不是一份东西，是同一根管子上的三段。**

- `app.domain.mentions`：friendly `@名字` / `@话题名` → 编码 token 的规范化
  （`expand_mention_names` / `canonicalize_refs`），文档 PUT、周报、结论回流也
  要过。它**产出** token。
- `app.domain.delivery.mention`：一条消息点到哪位 AI 队友，那条投递记在它那里
  （`record_mentions`），和定时投递同一本账。它决定**谁被叫起来**。
- 这里：token 怎么解析、正文存成什么、谁收强提醒、`block.refs` 写什么。

三段各答一个问题，判据都只有一份。

人类发送、assistant 发布和编辑的真实 intake 共享这些判据。
`SentText` 与 `text_as_sent` 由 `turn/intake/rewrite.py` 持有，`block/editing.py`
直接使用；它只问 `turn/intake/rooms.py` 的 `_is_dm`，仍然只有那里问一次房间
是不是私聊。`person_mentions` 接收该答案，不复制判据
（`test_is_private_read_points.py`）。
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.core.text import markdown_preview
from app.domain.addressing import Event, Hand, address
from app.domain.agent_instance.adapters import project_team
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block
from app.domain.conversation.services import is_task
from app.domain.delivery.ledger import deliver
from app.domain.delivery.models import Delivery
from app.domain.delivery_events import DeliveryEvent, event_id_for
from app.domain.identity.handles import agent_instance_handle, looks_like_agent_handle
from app.domain.identity.services import IdentityService
from app.domain.living_doc.models import DocumentComment
from app.domain.membership.roster import Member, roster_rows, rows_of
from app.domain.mentions import (
    canonicalize_refs,
    dangling_refs,
    expand_mention_names,
)
from app.domain.notification.models import NotificationType
from app.domain.run_record.service import record
from app.domain.thread import reads as thread_reads
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
    members: Sequence[Member] | None = None,
    seating: Sequence[str] = (),
) -> PersonMentions:
    """The mention rewrite a person's message gets in ``topic``: friendly
    ``@名字`` / ``@话题名`` become ``<@handle>`` / ``<#id>`` tokens, except in a
    private room, whose text is stored as written. ``agent`` is the room's
    agent (`AgentInstanceService.for_topic`), the name a seat not on the
    project roster answers to.

    ``dm`` 是调用方对 ``topic`` 问过 `_is_dm` 之后递进来的答案。名字刻意不
    叫 ``is_private``：那个布尔只在 `topic/models.py` 里读
    （`test_is_private_read_points.py`，连形参与关键字实参都数），这里既不该
    读它，也不该让它的名字在第二个模块里出现。

    Sending (`post_user_message`) and editing (`text_as_sent`) both read a
    person's words through here, so an edit stores what sending stores.

    ``members`` is the project's `roster` when the caller already read it
    (sending hands over the request's read); without it the roster is read.

    ``seating`` are seats the message would seat before it is read
    (`own_calls.named_own_seats`): a preview reads the message as if they
    already sat here, which sending makes true before it reads."""
    agent_handles = (
        await TopicMemberService(session).agent_handles(topic.id)
        if "@" in content
        else []
    )
    agent_handles += [s for s in seating if s not in agent_handles]
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
            else rows_of(members)
            if members is not None
            else await roster_rows(session, topic.project_id, team=project_team)
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
                # A private channel's name is not turned into a link for anyone else.
                if t.kind != TopicKind.root and t.id != topic.id and not t.members_only
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
        session, project_id, content, team=project_team, exclude_topic_id=room_id
    )


async def _where(session: AsyncSession, topic: Topic, block: Block) -> dict[str, str]:
    """Which conversation of ``topic`` the message is in: a task, a 支线, or
    the channel's own line (empty). A notification opens there, where the
    message is (`push.push_link`, the frontend's renderers)."""
    if block.conversation_id == topic.id:
        return {}
    if await is_task(session, block.conversation_id):
        return {"taskId": str(block.conversation_id)}
    return {"threadId": str(block.conversation_id)}


async def _tell(
    session: AsyncSession,
    topic: Topic,
    block: Block,
    kind: NotificationType,
    *,
    author: str,
    who: str,
    event: Event,
) -> None:
    """Hand one message to the delivery ledger for the people ``event`` names.

    The ledger writes the inbox row and records email and push by each
    recipient's own settings for ``kind``. Its key is this message and this
    kind, so an edit that names someone again does not tell them twice."""
    await deliver(
        session,
        DeliveryEvent(
            id=event_id_for(kind, str(block.id)),
            type=kind,
            payload={
                "projectId": str(topic.project_id),
                "topicId": str(topic.id),
                "topicTitle": topic.title,
                "blockId": str(block.id),
                "author": author,
                "authorName": who,
                "content": markdown_preview(block.content, 200),
                **await _where(session, topic, block),
            },
            occurred_at=block.created_at,
        ),
        address(event, Hand.participant),
    )


async def _tell_thread(
    session: AsyncSession,
    topic: Topic,
    block: Block,
    author: str,
    roster: list[dict],
    *,
    told: set[str],
) -> None:
    """A person's reply in a 支线 reaches the people who took part in it,
    except whoever it already @-ed and whoever muted the channel. 芝士's
    replies do not: it answers in every 支线 it is asked in, the rule the
    支线's own mark follows (`thread.reads`)."""
    if looks_like_agent_handle(author) or block.conversation_id == topic.id:
        return
    people = await TopicMemberService(session).reached(
        topic,
        [
            h
            for h in await thread_reads.people_in(session, block.conversation_id)
            if h != author and h not in told
        ],
    )
    if not people:
        return
    muted = await TopicRepository(session).muted_among(topic.id, people)
    reached = tuple(h for h in people if h not in muted)
    if reached:
        await _tell(
            session,
            topic,
            block,
            NotificationType.THREAD_REPLY,
            author=author,
            who=await _name_of(session, topic, author, roster),
            event=Event(in_thread=reached),
        )


async def _name_of(
    session: AsyncSession, topic: Topic, handle: str, roster: list[dict]
) -> str:
    """What a notification calls the author of a message: the name the room's
    roster gives that handle, the one its chip shows. An AI teammate missing
    from the roster is named by its instance; a person by their handle."""
    for row in roster:
        if row.get("handle") == handle and row.get("name"):
            return row["name"]
    if looks_like_agent_handle(handle):
        for instance in await AgentInstanceService(session).list_for_project(
            topic.project_id
        ):
            if agent_instance_handle(instance.id) == handle and instance.display_name:
                return instance.display_name
    return handle


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
    teammate it @s, record them in ``block.refs``, and,
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
    topics = TopicRepository(session)
    if any(h in _SPECIAL_MENTIONS for h in fresh):
        # Expand @all/@here to the channel's people, less whoever muted it: a
        # muted channel reaches someone only by their own name. @here should
        # be the ACTIVE members, but there's no presence signal yet, so it
        # equals @all for now (TODO: intersect with presence once it lands).
        #
        # A broadcast reaches people only: every 芝士 in the room already
        # reads the timeline, so notifying them adds nothing. An explicit
        # <@handle> is different and is NOT filtered here — that is how one
        # agent addresses another, which a room hosting several 芝士 depends
        # on.
        people = await TopicMemberService(session).people_in(topic, team=project_team)
        muted = await topics.muted_among(topic.id, people)
        concrete += [h for h in people if h not in muted]
    # Nobody needs a notification for their own message.
    targets = await TopicMemberService(session).reached(
        topic, [h for h in dict.fromkeys(concrete) if h != author]
    )
    if targets:
        await _tell(
            session,
            topic,
            block,
            NotificationType.MENTION,
            author=author,
            who=await _name_of(session, topic, author, roster),
            event=Event(mentioned=tuple(targets)),
        )
    if not before:
        await _tell_thread(session, topic, block, author, roster, told=set(targets))
    refs = [f"user:{h}" for h in resolved] + _topic_refs(text)
    if refs or before:
        block.refs = refs
    if not flag_unresolved:
        return
    lines = [
        say("mentionUnknownMember", member=f"<@{bad}>")
        for bad in unresolved
        if bad not in flagged
    ]
    # A `<#id>` that names no channel, task or 支线 draws as a reference that
    # opens nothing; the agent that wrote it is told beside the message, as it
    # is for a handle that names nobody. One an edit kept was told already.
    kept = set(await dangling_refs(session, block.project_id, before))
    lines += [
        say("refUnknownConversation", ref=f"<#{ref}>")
        for ref in await dangling_refs(session, block.project_id, text)
        if ref not in kept
    ]
    if not lines:
        return
    # The same conversation as the message. The site reads platform records;
    # hidden event blocks are no longer one of its sources.
    for line in lines:
        await record(
            session,
            conversation_id=block.conversation_id,
            content=line,
            turn_id=block.turn_id,
            seat=author,
            meta={
                "event_type": "unresolved_reference",
                "severity": "warning",
                "block_id": str(block.id),
            },
        )


@dataclass(frozen=True, slots=True)
class MentionedOutside:
    """The people a person's message @-mentions who are not in the channel it
    was said in, told to its sender alone, as Slack does: whether the sender
    may add them, and whether the channel is private (then they cannot read
    it either; in a public one they are told of the mention and read it
    there)."""

    room_id: uuid.UUID
    handles: list[str]
    may_add: bool
    members_only: bool

    def as_json(self) -> dict:
        return {
            "room_id": str(self.room_id),
            "handles": self.handles,
            "may_add": self.may_add,
            "members_only": self.members_only,
        }


async def mentioned_outside(
    session: AsyncSession, room: Topic, text: str, author: str
) -> MentionedOutside | None:
    """Whom ``author``'s message in ``room``, stored as ``text``, names that the
    channel does not seat. None when it names nobody outside. Nobody is added
    here: the sender chooses, through the channel's own way in."""
    named = [
        h
        for h in dict.fromkeys(_MENTION_RE.findall(text or ""))
        if h != author and h not in _SPECIAL_MENTIONS
    ]
    if not named:
        return None
    members = TopicMemberService(session)
    handles = await members.outside(room, named, team=project_team)
    if not handles:
        return None
    return MentionedOutside(
        room_id=room.id,
        handles=handles,
        may_add=await members.may_bring_in(room, author),
        members_only=room.members_only,
    )


@dataclass(frozen=True, slots=True)
class CommentMentions:
    """What a document comment's @s did: the people it told, and, for its
    writer alone, the people it named who cannot open the document."""

    notified: list[str]
    outside: MentionedOutside | None


async def announce_comment_mentions(
    session: AsyncSession,
    project_id: uuid.UUID | None,
    room: Topic | None,
    comment: DocumentComment,
    *,
    title: str,
    document_id: uuid.UUID | None = None,
    room_path: str | None = None,
    room_title: str | None = None,
    task_id: uuid.UUID | None = None,
) -> CommentMentions:
    """Tell each person a comment's @s, once for that comment.

    The comment hangs on a document (``document_id``) or on a file of a room
    (``room_path``), and ``title`` names it either way — the document's title,
    or the file's path.

    Only people of the project are told, never the writer, and never anyone who
    cannot open it: a task's document is read by whoever may enter its channel,
    so in a private channel someone not seated in it is left out and named back
    to the writer instead, as a message in that channel would be
    (:func:`mentioned_outside`). An AI teammate is not told here: the
    document's own agent is handed the thread by the comment route, and a
    comment names no other. A comment an AI teammate wrote tells nobody.

    The delivery is keyed by the comment, so delivering it again tells
    nobody twice; :func:`comment_notified` reads the same key back."""
    author = comment.author
    # What an AI teammate writes into a comment refers to people; it calls
    # nobody (its answer is read where it was asked).
    if await IdentityService(session).is_agent(author):
        return CommentMentions([], None)
    if project_id is None:
        # A team's own document tells nobody yet (`api.doc_mentions`).
        return CommentMentions([], None)
    members = TopicMemberService(session)
    people = set(await members.project_people(project_id, team=project_team))
    named = [
        h
        for h in dict.fromkeys(_MENTION_RE.findall(comment.content or ""))
        if h != author and h in people
    ]
    if not named:
        return CommentMentions([], None)
    outside = None
    targets = named
    if room is not None:
        targets = await members.reached(room, named)
        # Only whoever the comment could not tell: in a public channel someone
        # not in it still opens the document and is told.
        outsiders = await members.outside(room, named, team=project_team)
        away = [h for h in outsiders if h not in targets]
        if away:
            outside = MentionedOutside(
                room_id=room.id,
                handles=away,
                may_add=await members.may_bring_in(room, author),
                members_only=room.members_only,
            )
    if targets:
        roster = await roster_rows(session, project_id, team=project_team)
        who = next(
            (r["name"] for r in roster if r.get("handle") == author and r.get("name")),
            author,
        )
        where = (
            {"topicId": str(room.id), "taskId": str(task_id)}
            if room is not None and task_id is not None
            else {"topicId": str(room.id)}
            if room is not None
            else {}
        )
        await deliver(
            session,
            DeliveryEvent(
                id=event_id_for(NotificationType.MENTION, str(comment.id)),
                type=NotificationType.MENTION,
                payload={
                    "projectId": str(project_id),
                    # Which subject it hangs on. A file's comment names the
                    # file; a document's names the document, which is what the
                    # letter and the push title read to say where it was
                    # (`notification.letter`, `notification.push`).
                    **(
                        {"documentId": str(document_id)}
                        if document_id is not None
                        else {"roomPath": room_path}
                    ),
                    "commentId": str(comment.id),
                    # The letter's headline and the email subject both name it.
                    "documentTitle": title,
                    # The room it hangs in, when the caller named it. A file's
                    # title is a path, and a path is not a channel's name.
                    "topicTitle": room_title or title,
                    "author": author,
                    "authorName": who,
                    "content": markdown_preview(comment.content, 200),
                    **where,
                },
                occurred_at=comment.created_at,
            ),
            address(Event(mentioned=tuple(targets)), Hand.participant),
        )
    return CommentMentions(targets, outside)


async def comment_notified(
    session: AsyncSession, comment_ids: list[uuid.UUID]
) -> dict[str, list[str]]:
    """Whom each of these comments told by its @s, from the delivery ledger:
    a name in a comment is drawn as a mention only where it told someone."""
    if not comment_ids:
        return {}
    by_event = {
        event_id_for(NotificationType.MENTION, str(cid)): str(cid)
        for cid in comment_ids
    }
    rows = await session.execute(
        select(Delivery.event_id, Delivery.recipient_handle).where(
            Delivery.event_id.in_(list(by_event))
        )
    )
    told: dict[str, list[str]] = {}
    for event_id, handle in rows:
        told.setdefault(by_event[event_id], []).append(handle)
    return told
