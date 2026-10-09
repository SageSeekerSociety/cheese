"""Block model: what is said and shown in a room.

Every message, attachment, event, comment and preview pointer is a Block.
Blocks live in one pool per project; ``reply_to`` makes the conversation tree,
``refs`` cite other things, and ``created_at`` is the timeline. Documents are
not blocks: they live in ``app.domain.living_doc``.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    FetchedValue,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.block.indexed_rows import (
    AGENT_CHECK_ROWS,
    COALESCED_ROWS,
    EID,
    FAILED_TURN_ROWS,
    LAST_SAID_ROWS,
    MACHINE_EVENT_ROWS,
    QUESTION_ROWS,
    QUEUED_MESSAGE_ROWS,
    SHOWN_ROWS,
    UNANSWERED_ROWS,
)
from app.domain.common import Timestamps, UuidPk

# The registry `conversation_id` points at: mapped wherever a block is, so the
# foreign key resolves in a process that never imports `app.models`.
from app.domain.conversation.models import Conversation  # noqa: F401


class BlockKind(enum.StrEnum):
    message = "message"
    attachment = "attachment"
    event = "event"
    # A renderable product 芝士 explicitly points at (spec §9.1): content = the
    # worktree-relative file path, mime_type = how to render it (text/html,
    # image/svg+xml). The latest artifact of a topic is its "current preview";
    # created via `cheese show`. Never inferred from prose — the AI names it.
    artifact = "artifact"
    # 一份周报 (spec §7.1 周报集): content = 周报正文，meta 里的 `since`/`until`
    # 是它讲的那一周。它是「项目自己的话」——项目文档页按项目列出它们。它讲的
    # 是一段历史而不是一个当前状态，所以带时间窗：并排摆着的几份周报，是那个
    # 窗口把它们分开的。
    weekly = "weekly"


class AuthorType(enum.StrEnum):
    """谁写下了这条事件：一个参与者，还是平台自己。

    人和 agent 是同一种参与者（结论 1），所以这一列不回答「是人还是 AI」——
    那个问题由署名（``Block.author``，一个 handle）回答，而且只有它回答得了：
    一个房间里坐着好几个人和好几个 agent，一个档位说不出是哪一个。

    读这一列的唯一落点是 ``app.domain.block.authorship``。
    """

    participant = "participant"

    # 平台自己产的事件：部署提醒、闸门结论、自动重发。
    platform = "platform"


# `meta` key carried by every new message/attachment that arrives as an input.
# Its value is null while the input is pending, then the id of the agent turn
# whose clean Stop showed the session had read it (BlockRepository.mark_consumed).
# Presence of the null key distinguishes a tracked pending input from a legacy
# block created before turn accounting.
#
# 为什么是运行时事实而不是位置：一轮的 prompt 窗口是在**拿到锁的那一刻**按当时
# 的 history 算的，而消息是无锁落库的 —— 于是"这条被哪一轮读进去了"根本不可能
# 从 created_at 的先后反推出来（两人同时 @ 时，第一轮把两条都合并进了 prompt，
# 但库里没有任何痕迹能让第二轮知道）。只能在读的时候记下来。
#
# 存在 meta 里而不是单开一列：这是 turn 记账的内部细节，不进 API 语义、不需要被
# 查询/索引，而本仓多个 agent 并发改动，一次 alembic 分叉的代价高于一列的收益。
CONSUMED_TURN_META_KEY = "consumed_turn"

# How many turns have taken this block into a prompt — INCLUDING the ones that
# died before finishing. `consumed_turn` above is stamped only by a turn that
# completed, which is deliberate (a dead turn must not eat the message). The
# cost of that correctness is invisible replay: a turn that keeps failing keeps
# re-sending the exact same blocks, forever, and from the room it is
# indistinguishable from "this topic is broken".
#
# 两个键分开，不是一个计数器兼职两件事：`consumed_turn` 决定**下一轮带什么**，
# 这个键只决定**要不要把重放说出来**。合并成一个的话，"说出来"就得改动窗口语义，
# 而那正是原注释在防的事。
PROMPT_ATTEMPTS_META_KEY = "prompt_attempts"

# The turn whose prompt last carried this block. The attempt count above says
# how often; this says where to, and it is what lets a clean Stop stamp the
# block when the process that fed it is gone: which blocks a turn was fed also
# lives in memory, and a backend replaced mid-turn loses it while the session
# on the machine runs on and stops against the new process.
PROMPTED_TURN_META_KEY = "prompted_turn"

# What this block has to say to 芝士, written by whoever created it — and absent
# on the blocks that have nothing to say to it, which is most of them.
#
# 一个话题的事件流是给人看的时间线：预览更新了、同步完成了、话题改了名。芝士需要
# 知道的只是其中很少的一部分，而「是哪一部分」只有**写下那条事件的代码**知道 ——
# 它就是造成这件事的那段代码。反过来按 kind / author_type 去猜，等于把一份为人做
# 的展示日志当成给模型的指令队列用。
#
# 措辞和 `content` 分开也是同一个理由：界面上要读到的是「张三 编辑了文档」，而芝士
# 要听的是它手上那份已经旧了、以及现在该做什么。同一件事，两个读者，两句话。
#
# 与 `consumed_turn` 成对：一个说「这是说给芝士的」，一个说「哪一轮已经读过了」。
AGENT_NOTICE_META_KEY = "agent_notice"

# When the author last edited this message (ISO-8601). Its presence is what the
# room reads as 「已编辑」; see `app.domain.block.editing`.
EDITED_AT_META_KEY = "edited_at"

# This message is a member's step checklist, the one `todo_write` (or a person
# in the composer) keeps editing:
# ``{"items": [{"id", "subject", "status"}], "result": str | None}``. The room
# draws the list from it; the text says the same thing for every other reader.
# Its presence is also how the next write finds which of its author's messages
# to edit.
CHECKLIST_META_KEY = "checklist"

# The step markers of the checklist message's text — what every reader who
# does not draw `meta.checklist` gets (the agent reading the history, a copy, a
# notification preview).
_CHECKLIST_MARK = {"completed": "✓", "in_progress": "✱", "pending": "○"}


def checklist_text(items: list[dict], result: str | None) -> str:
    """The checklist as the message's text: one line per step, and the result
    line under it once there is one."""
    lines = [f"{_CHECKLIST_MARK[item['status']]} {item['subject']}" for item in items]
    if result:
        lines += ["", f"✅ {result}"]
    return "\n".join(lines)


def consumed_turn(block: "Block") -> str | None:
    """Which turn already read this block into a prompt (None = still pending)."""
    return (block.meta or {}).get(CONSUMED_TURN_META_KEY)


def agent_notice(block: "Block") -> str | None:
    """What this block says to 芝士, or None when it says nothing to it."""
    return (block.meta or {}).get(AGENT_NOTICE_META_KEY) or None


def prompt_attempts(block: "Block") -> int:
    """How many turns have put this block into a prompt, finished or not."""
    return int((block.meta or {}).get(PROMPT_ATTEMPTS_META_KEY) or 0)


def prompted_turn(block: "Block") -> uuid.UUID | None:
    """The turn whose prompt last carried this block (None = never prompted)."""
    value = (block.meta or {}).get(PROMPTED_TURN_META_KEY)
    return uuid.UUID(value) if value else None


class Block(UuidPk, Timestamps, Base):
    __tablename__ = "blocks"
    # `seq` is given by the database on insert; RETURNING brings it back with the
    # flush, where a later read of the attribute would otherwise lazy-load it
    # past an await.
    __mapper_args__ = {"eager_defaults": True}
    # (conversation_id, created_at) serves every "this conversation's blocks,
    # newest first" question: the timeline pages, and the MAX(created_at)
    # behind a conversation's 最后活动时间 — which the sidebar sorts on, so it
    # runs once per listed conversation and must not degrade into reading its
    # whole history.
    __table_args__ = (
        Index("ix_blocks_conversation_created_at", "conversation_id", "created_at"),
        # The conversation as the room shows it, newest first: a room opens on
        # one page of it. Without the predicate in the index, a page of 50 shown
        # rows reads past the thousands of steps stored between them.
        Index(
            "ix_blocks_shown",
            "conversation_id",
            "created_at",
            "id",
            postgresql_where=SHOWN_ROWS,
        ),
        # A conversation's blocks in the order they were stored: "after n" for a
        # page catching up, and no two blocks with one number.
        Index("ix_blocks_conversation_seq", "conversation_id", "seq", unique=True),
        # 未读: count, per conversation, the messages someone else wrote after
        # the reader's cursor, which is a block number (`TopicReadState`). Its
        # only selective predicate lives elsewhere, so without this the planner
        # reads the whole table for every open tab. INCLUDE(author) rather than
        # a key column because `author <> me` is only ever tested for
        # inequality; carrying it in the leaf is what makes the scan index-ONLY.
        Index(
            "ix_blocks_conversation_kind_seq",
            "conversation_id",
            "kind",
            "seq",
            postgresql_include=["author"],
        ),
        # The same count against a time, the cursor the release before this one
        # reads while a deploy replaces it. Goes with `last_read_at`.
        Index(
            "ix_blocks_conversation_kind_created",
            "conversation_id",
            "kind",
            "created_at",
            postgresql_include=["author"],
        ),
        # The three below serve reads the sidebar and the board poll for every
        # conversation of a project at once — which questions are still open,
        # which rooms wait on a broken turn or a machine. Each looks for a few
        # hundred rows in the whole table. Partial on exactly the predicate the
        # query filters by (`indexed_rows`), which is also why they stay tiny.
        Index(
            "ix_blocks_questions",
            "conversation_id",
            text("created_at DESC"),
            postgresql_where=QUESTION_ROWS,
        ),
        Index(
            "ix_blocks_machine_events",
            "conversation_id",
            "created_at",
            postgresql_where=MACHINE_EVENT_ROWS,
        ),
        Index(
            "ix_blocks_failed_turns",
            "conversation_id",
            "created_at",
            postgresql_where=FAILED_TURN_ROWS,
        ),
        # The events that hand a stuck card to an agent, read for the rooms
        # whose card is stuck on every `GET /topics`.
        Index(
            "ix_blocks_agent_checks",
            "conversation_id",
            "created_at",
            postgresql_where=AGENT_CHECK_ROWS,
        ),
        # 房间里「谁在等人」的那两条扫描（`waits`）：谁最后说过话、谁被点名还没
        # 回答。两边都是每个项目一把，读各自房间里最近七天的消息，在 `/topics`
        # 里各占约 200 ms（2026-10-08）。谓词与查询逐字相同（`indexed_rows`）
        # —— 写成 ORM 表达式的话，参数化的那一份在通用计划下证不出自己蕴含
        # 索引的 WHERE，索引就不会被采纳；用 `^@` 而不是 LIKE 是为了躲开 `%`
        # 在各驱动之间的转义分歧，那会让迁移建出的索引和查询说的不是一件事。
        Index(
            "ix_blocks_last_said",
            "conversation_id",
            "author",
            text("created_at DESC"),
            postgresql_where=LAST_SAID_ROWS,
        ),
        Index(
            "ix_blocks_unanswered",
            "conversation_id",
            "created_at",
            postgresql_where=UNANSWERED_ROWS,
        ),
        # The messages still waiting for their turn, read by a sweep every few
        # seconds (`pending_messages`): tens of rows out of every block there is.
        Index(
            "ix_blocks_queued_messages",
            "created_at",
            "id",
            postgresql_where=QUEUED_MESSAGE_ROWS,
        ),
        # Whether a conversation already holds a hook event id: asked for every
        # room event that lands (`has_any_eid`), and almost always answered no.
        Index("ix_blocks_conversation_eid", "conversation_id", EID),
        Index(
            "ix_blocks_coalesced",
            "conversation_id",
            postgresql_where=COALESCED_ROWS,
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The conversation this block is in: a room or a task. No single-column
    # index of its own: the two indexes above lead with it, and serve a lookup
    # by it alone (including the ON DELETE CASCADE sweep).
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    # Where this block stands among its conversation's, in the order they were
    # stored: 1, 2, 3 … A page that lost its connection asks for what came
    # "after n", and a read cursor is one of these. The database numbers each
    # insert one past the largest in its conversation, under a lock held to
    # commit (trigger `blocks_numbered`), so writers in one conversation are
    # numbered in the order they commit. Not the order a room shows: that is
    # `created_at`, which a caller may date earlier than now
    # (`BlockRepository.create`).
    seq: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())

    kind: Mapped[BlockKind] = mapped_column(
        Enum(BlockKind, native_enum=False, length=16),
        default=BlockKind.message,
    )
    author_type: Mapped[AuthorType] = mapped_column(
        Enum(AuthorType, native_enum=False, length=16)
    )
    # Free-form author handle (user id, or "cheese" for the AI). Phase 0 has no
    # user table yet, so this stays a string.
    author: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, default="")

    # Conversation tree: which block this one replies to.
    reply_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blocks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Render-by-type (spec §9.1): the mimeType of an artifact block — the host
    # picks a renderer from this, never from parsing the AI's text. Only set on
    # kind=artifact blocks (e.g. text/html, image/svg+xml).
    # 255, not 64: an Office MIME type runs 65-73 characters
    # (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`
    # is 71), and 64 rejected every .docx/.xlsx/.pptx attachment — rolling back
    # the whole message after the upload had already returned 200. RFC 6838 caps
    # the type and subtype names at 127 each.
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The agent turn that produced this block (review R4): groups a turn's blocks
    # for traceability / recovery / the collaboration-trajectory dataset. Null for
    # human-authored or pre-R4 blocks.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    # Structured event payload (kind=event): {"tool": <name>, "arg": <preview>,
    # "platform": <bool>} so the UI translates/classifies at DISPLAY time instead
    # of relying on text baked into `content` (which stays as a human-readable
    # fallback for old clients / old rows). Null on non-event blocks and on
    # event rows created before this field existed.
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Citations: which topics / PRs / files this block leans on.
    refs: Mapped[list[str]] = mapped_column(JSON, default=list)


class BlockReaction(UuidPk, Base):
    """An emoji reaction on a block — Slack semantics (协作平台的消息表情).

    One row per (block, emoji, author); reacting again with the same emoji
    removes the row (toggle). Both humans and 芝士 react through this table —
    e.g. the platform's deterministic 👀 receipt on a summoning message."""

    __tablename__ = "block_reactions"
    __table_args__ = (
        UniqueConstraint("block_id", "emoji", "author", name="uq_block_reaction"),
    )

    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE"), index=True
    )
    emoji: Mapped[str] = mapped_column(String(32))
    # Free-form author handle, same convention as Block.author ("cheese" = AI).
    author: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
