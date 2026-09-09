"""Block model — 万物皆块 (spec §5).

Every piece of content (a message, a doc node, a decision, an attachment) is a
Block. Blocks live in one pool per project and are organized by two trees:

- reply_to     → conversation tree ("how it was discussed")
- struct_parent → document tree ("how it was organized")

plus refs[] (citations) and a timeline (created_at). One block can appear in
both trees at once. Phase 0 stores the structure; richer views come later.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class BlockKind(enum.StrEnum):
    message = "message"
    doc = "doc"
    # B1: a structured node inside the living doc's block tree (heading/paragraph/
    # list/code/quote). The `doc` block stays the canonical markdown; doc_node
    # blocks are its struct_parent children, carrying stable ids for downstream
    # anchoring (cross-view highlight, comments, live refs). Excluded from the
    # conversation timeline.
    doc_node = "doc_node"
    # B4: an inline comment anchored to a doc node (reply_to = the doc_node id).
    # Shown in the document margin, not the conversation timeline.
    comment = "comment"
    decision = "decision"
    attachment = "attachment"
    event = "event"
    # A renderable product 芝士 explicitly points at (spec §9.1): content = the
    # worktree-relative file path, mime_type = how to render it (text/html,
    # image/svg+xml). The latest artifact of a topic is its "current preview";
    # created via `cheese artifact`. Never inferred from prose — the AI names it.
    artifact = "artifact"


class AuthorType(enum.StrEnum):
    human = "human"
    ai = "ai"  # 芝士 (本体 or 分身)
    system = "system"


# `meta` key carried by every new human message/attachment. Its value is null
# while the input is pending, then the id of the agent turn that actually read
# it (BlockRepository.mark_consumed). Presence of the null key distinguishes a
# tracked pending input from a legacy block created before turn accounting.
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


def consumed_turn(block: "Block") -> str | None:
    """Which turn already read this block into a prompt (None = still pending)."""
    return (block.meta or {}).get(CONSUMED_TURN_META_KEY)


def prompt_attempts(block: "Block") -> int:
    """How many turns have put this block into a prompt, finished or not."""
    return int((block.meta or {}).get(PROMPT_ATTEMPTS_META_KEY) or 0)


class Block(UuidPk, Timestamps, Base):
    __tablename__ = "blocks"
    # (topic_id, created_at) serves every "this topic's blocks, newest first"
    # question: the timeline pages, and the MAX(created_at) behind a topic's
    # 最后活动时间 — which the sidebar sorts on, so it runs once per listed topic
    # and must not degrade into reading the whole topic's history.
    __table_args__ = (
        Index("ix_blocks_topic_id_created_at", "topic_id", "created_at"),
        # The same shape one level down: a thread's conversation, oldest-first.
        # Partial, because most blocks are the room's own line and carry no
        # task — indexing those NULLs would double the index for no reader.
        Index(
            "ix_blocks_task_id_created_at",
            "task_id",
            "created_at",
            postgresql_where=text("task_id IS NOT NULL"),
        ),
        # 话题级未读: count, per topic, the messages on a room's own line that
        # someone else wrote after the reader's cursor. Its only selective
        # predicate lives on `topics`, so without this the planner read the
        # whole table — every 30 seconds, for every open tab, at a cost that
        # grew with the size of the entire platform rather than the project
        # being looked at. INCLUDE(author) rather than a fifth key column
        # because `author <> me` is only ever tested for inequality; carrying
        # it in the leaf is what makes the scan index-ONLY (Heap Fetches: 0),
        # and the heap reads are where the buffer-pool churn came from.
        # `created_at` IS a key column: the cursor comparison ranges on it.
        Index(
            "ix_blocks_topic_kind_task_created",
            "topic_id",
            "kind",
            "task_id",
            "created_at",
            postgresql_include=["author"],
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # No single-column index of its own: `ix_blocks_topic_kind_task_created`
    # and `ix_blocks_topic_id_created_at` both lead with topic_id, so either
    # serves a topic_id-only lookup (including the ON DELETE CASCADE sweep when
    # a topic or project goes away). A third copy would only be one more index
    # for every insert to maintain.
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    # WHICH thread this block is in. NULL = the room's own line; set = the
    # conversation of that one piece of work. This is the key that makes a task
    # a thread instead of a room: before it, the only way to give work its own
    # conversation was to give it its own row in `topics`, because `topic_id`
    # was the sole grouping key.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )

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
    # Document tree: position in the structured document.
    struct_parent: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blocks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # B1 doc-as-block-tree: structural node type (heading/paragraph/list/code/
    # quote) and sibling order under struct_parent. Only set on kind=doc nodes.
    node_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    struct_order: Mapped[float | None] = mapped_column(Float, nullable=True)

    # B4 段落评论: the exact text a comment was selected on (Feishu-style). The
    # comment still anchors to its paragraph via reply_to; this preserves the quoted
    # span for display. Only set on kind=comment blocks.
    anchor_quote: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Render-by-type (spec §9.1): the mimeType of an artifact block — the host
    # picks a renderer from this, never from parsing the AI's text. Only set on
    # kind=artifact blocks (e.g. text/html, image/svg+xml).
    # 255, not 64: an Office MIME type runs 65-73 characters
    # (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`
    # is 71), and 64 rejected every .docx/.xlsx/.pptx attachment — rolling back
    # the whole message after the upload had already returned 200. RFC 6838 caps
    # the type and subtype names at 127 each.
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # How many times the living doc has been written (kind=doc only; every other
    # block sits at 1 and never moves). A writer sends the version it read and
    # the update is conditional on it, so 芝士 overwriting the whole doc from a
    # copy it took ten minutes ago is refused instead of erasing what a person
    # wrote in between — 整块覆盖 is the only way this doc is ever written, which
    # makes every stale write a total loss.
    #
    # A counter rather than a content hash (the version workspace files carry):
    # this one is said out loud. A person's edit pushes 「文档已更新到第 7 版」
    # into the running session, and 第 7 版 is a thing 芝士 can compare against
    # what it holds; a hash is not.
    doc_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", default=1
    )

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
    # Citations: which decisions / PRs / files this block leans on.
    refs: Mapped[list[str]] = mapped_column(JSON, default=list)

    # If this block was upgraded into its own topic (spec §6.1), the original
    # position becomes a live link to the new topic.
    upgraded_to_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    # …and if it was dispatched into a piece of work instead, which is what
    # upgrading a message inside a room now does. Two columns rather than one
    # holding either kind of id: both are real foreign keys, and a single
    # untyped column would be a pointer the database cannot check into a table
    # it cannot name.
    upgraded_to_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )


class BlockReaction(UuidPk, Base):
    """An emoji reaction on a block — Slack semantics (协作平台的消息表情).

    One row per (block, emoji, author); reacting again with the same emoji
    removes the row (toggle). Both humans and 芝士 react through this table —
    e.g. the platform's deterministic ✅ receipt on a summoning message."""

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
