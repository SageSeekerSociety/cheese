"""Friendly-@ canonicalization, shared by every platform write path.

Chat replies have long had this backstop (chat.py): a friendly "@名字 /
@handle / @话题名" is deterministically rewritten into the structured token
(<@handle> / <#id>) so it renders as a clickable chip and notifies. Docs,
weekly reports, and returned conclusions render through the same token-aware
pipelines but used to skip the rewrite — a doc saying "@张衡" stayed plain
text. This module hosts the shared rewrite so ALL those write paths (PUT doc,
weekly, return-conclusion, chat reply) canonicalize identically.

Notification title/body are rendered as plain text in the UI (no token
decoration), so they are deliberately NOT canonicalized — a raw token there
would look worse than the friendly name.
"""

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import markdown_preview
from app.domain.block.models import Block
from app.domain.conversation.models import ConversationKind
from app.domain.membership.ports import ProjectTeam
from app.domain.membership.roster import roster_rows
from app.domain.room_task.models import Task
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic.repositories import TopicRepository

# After an ASCII-word-ending @name/@handle, the next char must not continue the
# word — so roster handle "andy" never eats the front of a literal "@andyl".
# ASCII-only on purpose: Python's \w matches CJK, and "@张衡来负责" must still
# resolve 张衡 even though 来 follows without a space.
_ASCII_WORD = re.compile(r"[A-Za-z0-9_-]$")
_ASCII_BOUNDARY = r"(?![A-Za-z0-9_-])"


def expand_mention_names(
    text: str, roster: list[dict], topics: list[dict] | None = None
) -> str:
    """Canonicalize a friendly "@名字 / @handle / @话题名" into the structured
    token (<@handle> / <#id>) — deterministic exact-match against the
    roster/topics, longest first. Both the display name AND the handle work:
    in chat people are labeled by handle, so "@andyl" must resolve even when
    andyl's display name differs. Matching is case-insensitive so "@Alice"
    (display name "Alice", or the handle typed in the wrong case) still resolves
    to the canonical lowercase handle token <@alice>. Tokens already present are
    untouched (they don't match the @name patterns)."""
    subs: list[tuple[str, str]] = []
    for m in roster:
        tok = f"<@{m['handle']}>"
        for key in (m.get("name"), m.get("handle")):
            if key:  # an empty pattern ("@") would swallow every @ in the text
                subs.append((f"@{key}", tok))
    subs += [(f"@{t['title']}", f"<#{t['id']}>") for t in (topics or []) if t["title"]]
    subs.sort(key=lambda s: len(s[0]), reverse=True)
    seen: set[str] = set()
    for pat, tok in subs:
        if pat in seen:  # name == handle yields the same pattern twice
            continue
        seen.add(pat)
        boundary = _ASCII_BOUNDARY if _ASCII_WORD.search(pat) else ""
        # (?<!<) keeps already-encoded tokens intact: the "@handle" inside a
        # produced "<@handle>" must not be re-wrapped by a later pattern.
        # bind tok per-iteration (B023): a bare closure would see the last tok.
        # IGNORECASE: "@Alice" (name "Alice") and a mis-cased handle both map to
        # the canonical lowercase token; the replacement tok is always the
        # stored lowercase handle, so the emitted token is exactly <@alice>.
        text = re.sub(
            r"(?<!<)" + re.escape(pat) + boundary,
            lambda _m, t=tok: t,
            text,
            flags=re.IGNORECASE,
        )
    return text


async def canonicalize_refs(
    session: AsyncSession,
    project_id: uuid.UUID,
    text: str,
    *,
    team: ProjectTeam,
    exclude_topic_id: uuid.UUID | None = None,
) -> str:
    """Load the project's roster + topic list and canonicalize friendly @refs
    in `text`. The cheap no-@ case never touches the DB.

    名册上有队友，所以「@评审」解析得出它的席位 handle：一个 agent 对另一个 agent
    发的那条 chat（结论 12）就是从这里变成可点名、通知得到的一条消息的。"""
    if not text or "@" not in text:
        return text
    roster = await roster_rows(session, project_id, team=team)
    topics = [
        {"id": str(t.id), "title": t.title}
        for t in await TopicRepository(session).list_for_project(project_id)
        # A private channel's name is not turned into a link for anyone else.
        if t.kind != TopicKind.root and t.id != exclude_topic_id and not t.members_only
    ]
    return expand_mention_names(text, roster, topics)


#: A ``<#id>`` reference, as the reader draws it: ``frontend/src/lib/refChip.ts``
#: makes a chip of ``<#[\w-]+>``, and JavaScript's ``\w`` is ASCII only. What it
#: does not draw as a chip is text, and nothing here calls it a reference.
_CONVERSATION_REF_RE = re.compile(r"<#([A-Za-z0-9_-]+)>")
_ANY_REF_RE = re.compile(r"<([@#&])([^<>\s]+)>")

#: How much of a 支线's opening message names it.
THREAD_TITLE_CHARS = 40


@dataclass(frozen=True, slots=True)
class ConversationRef:
    """What a ``<#id>`` names: a channel, a task or a 支线, and the room it is
    in (a channel's own id for a channel)."""

    id: uuid.UUID
    project_id: uuid.UUID
    kind: ConversationKind
    room_id: uuid.UUID
    title: str


def _thread_title(content: str) -> str:
    """A 支线 has no title: it is named by the message it hangs under, read as
    words — a teammate by their handle, a file by its name, a reference not at
    all."""

    def word(m: re.Match) -> str:
        kind, value = m.group(1), m.group(2)
        if kind == "@":
            return f"@{value}"
        if kind == "&":
            return value.split(":")[0].rsplit("/", 1)[-1]
        return ""

    return markdown_preview(_ANY_REF_RE.sub(word, content or ""), THREAD_TITLE_CHARS)


async def conversation_refs(
    session: AsyncSession, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ConversationRef]:
    """Each of ``ids`` that is a channel, a task or a 支线, by id. One that is
    none of them is not in the answer."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return {}
    found: dict[uuid.UUID, ConversationRef] = {}
    for id_, project_id, title in await session.execute(
        select(Topic.id, Topic.project_id, Topic.title).where(Topic.id.in_(wanted))
    ):
        found[id_] = ConversationRef(
            id_, project_id, ConversationKind.room, id_, title or ""
        )
    for id_, project_id, room_id, title in await session.execute(
        select(Task.id, Task.project_id, Task.room_id, Task.title).where(
            Task.id.in_(wanted)
        )
    ):
        found[id_] = ConversationRef(
            id_, project_id, ConversationKind.task, room_id, title or ""
        )
    for id_, project_id, room_id, content in await session.execute(
        select(Thread.id, Thread.project_id, Thread.room_id, Block.content)
        .join(Block, Block.id == Thread.root_block_id)
        .where(Thread.id.in_(wanted))
    ):
        found[id_] = ConversationRef(
            id_, project_id, ConversationKind.thread, room_id, _thread_title(content)
        )
    return found


def _ref_ids(text: str) -> list[str]:
    return list(dict.fromkeys(_CONVERSATION_REF_RE.findall(text or "")))


async def dangling_refs(
    session: AsyncSession, project_id: uuid.UUID, text: str
) -> list[str]:
    """The ``<#id>`` references in ``text`` that name no channel, task or 支线
    of this project, as written. A reader sees each as a reference that opens
    nothing."""
    written = _ref_ids(text)
    if not written:
        return []
    parsed: dict[str, uuid.UUID] = {}
    for raw in written:
        try:
            parsed[raw] = uuid.UUID(raw)
        except ValueError:
            continue
    refs = await conversation_refs(session, parsed.values())
    return [
        raw
        for raw in written
        if raw not in parsed
        or (ref := refs.get(parsed[raw])) is None
        or ref.project_id != project_id
    ]


def dangling_refs_warning(dangling: list[str]) -> str:
    """What the writer of ``dangling`` references is told, beside the write
    that stored them or the read that found them."""
    named = "、".join(f"`<#{ref}>`" for ref in dangling)
    return (
        f"{named} 指不到这个项目里的任何频道、任务或支线，读的人看到的是一个打不开的"
        "「引用不存在」。`<#id>` 只认频道、任务和支线自己的 id：换成正确的 id，"
        "或者改成普通文字。"
    )
