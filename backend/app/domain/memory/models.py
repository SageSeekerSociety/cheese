"""Memory model.

Per spec §8.4, the block tree (DB) is the source of truth; memory is a
projection for fast AI recall, rebuildable from blocks. Memory is stored as
plain entries, each in one pool, and every pool belongs to a single agent
instance inside a single project (结论 8). 项目没有池：人和 agent 共同看的东西
是文档（结论 7）。
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class MemoryScope(enum.StrEnum):
    # 关于某个人的记忆: 某个项目里的某个 agent 实例对这个人的认识。它属于那个实
    # 例, 不属于那个人, 也不跟着人跨项目走 (结论 8) —— A 项目的芝士对他的判断,
    # B 项目的芝士读不到。scope_id 是 `<项目>:<agent handle>:<这个人的 handle>`,
    # 见 `user_scope_id`。
    user = "user"
    skill = "skill"  # 技能记忆: 领域知识/文档模板/场景包配置
    # 芝士 记忆: 一个 agent 在一个项目里学到的东西。People working on a project
    # each remember their own things; agents do too. Keyed per agent so a
    # project hosting several 芝士 doesn't pool one's operational trivia with
    # another's product decisions — and, like everything else about an agent,
    # it stays inside the project it was learned in.
    agent_project = "agent_project"


class MemoryLayer(enum.StrEnum):
    """Which layer a fact belongs to — i.e. when it is allowed to cost prompt.

    A pool grows without bound while the prompt does not, so "everything I
    learned" cannot be the injection unit forever: past a few dozen facts the
    ones that decide *how this agent behaves at all* start losing their seat to
    whatever happened to be written last. Splitting the pool is what stops that:

    - ``core`` — who this agent is, its standing rules and goals. Small,
      hand-curated, injected in full every single turn, never filtered by
      relevance. If it is only true sometimes, it is not core.
    - ``fact`` — everything else it learned. Retrieved against the turn's own
      context, so a pool can keep growing without any one turn paying for all
      of it. What a turn does not retrieve is still reachable through
      ``cheese recall``.
    """

    core = "core"
    fact = "fact"


def project_scope_prefix(project_id: str | uuid.UUID) -> str:
    """The ``scope_id`` prefix shared by every pool of one project.

    Listing "what did the 芝士 in this project remember" is a prefix scan over
    this, which is why the composite key's shape lives here rather than being
    re-spelled at each call site. Both composite scopes start with it, which is
    also the whole of 结论 8's isolation: a pool of another project cannot be
    named without naming that project's id.
    """
    return f"{project_id}:"


def agent_project_scope_id(project_id: str | uuid.UUID, agent_handle: str) -> str:
    """scope_id for :attr:`MemoryScope.agent_project`.

    ``scope_id`` is one plain string shared by every scope, so the two parts
    are joined rather than given columns of their own. A handle cannot contain
    ``:`` (it is a username), so the split is unambiguous.
    """
    return f"{project_scope_prefix(project_id)}{agent_handle}"


def user_scope_id(
    project_id: str | uuid.UUID, agent_handle: str, person_handle: str
) -> str:
    """scope_id for :attr:`MemoryScope.user` — one agent's notes on one person.

    Three parts, because all three are needed to say whose knowledge this is:
    the instance owns it, and an instance only exists inside its project
    (结论 8). Neither a handle nor a project id can contain ``:``, so the split
    stays unambiguous.
    """
    return f"{agent_project_scope_id(project_id, agent_handle)}:{person_handle}"


def user_scope_about(person_handle: str) -> str:
    """The tail every :attr:`MemoryScope.user` pool about this person ends with.

    A person's own profile page asks the one question that is not about a
    single pool — "what has been learned about me, anywhere" — and a suffix is
    the only way to ask it without enumerating every project and every agent.
    """
    return f":{person_handle}"


class MemoryDream(UuidPk, Timestamps, Base):
    """A 记忆整理 pass that ran while the clock-driven organizer existed.

    Nothing writes these rows any more. They are kept because `MemoryEntry`
    points at them: each retired entry names the pass that retired it, and
    reading the pool's history means being able to follow that pointer.
    """

    __tablename__ = "memory_dreams"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The kickoff turn that ran the pass.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # What the proposal was computed against.
    snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    # 人话 summary of what changed, in 芝士's own words.
    summary: Mapped[str] = mapped_column(Text, default="")


class MemoryEntry(UuidPk, Timestamps, Base):
    __tablename__ = "memory_entries"

    # Injection reads one layer of one pool at a time; the standalone
    # scope/scope_id indexes cannot answer that without a heap scan.
    __table_args__ = (
        Index("ix_memory_entries_pool_layer", "scope", "scope_id", "layer"),
    )

    scope: Mapped[MemoryScope] = mapped_column(
        Enum(MemoryScope, native_enum=False, length=16), index=True
    )
    # Which pool: the composite keys built by `agent_project_scope_id` /
    # `user_scope_id`, or a skill name. 200 because the longest key
    # this can hold is `user_scope_id`: a uuid (36) plus an agent handle and a
    # person handle (64 each, `agent_instance.handle` / `topic_memberships.
    # member_handle`) plus two separators — 166. A key that does not fit is not
    # a truncated pool, it is a 500 out of `cheese remember` and a failed
    # migration, so the column has to outrun the widest key by construction.
    scope_id: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[str] = mapped_column(Text)
    # Default `fact`: a memory earns its permanent seat, it is not born with
    # one. Anything written without saying otherwise is something learned.
    layer: Mapped[MemoryLayer] = mapped_column(
        Enum(MemoryLayer, native_enum=False, length=8),
        default=MemoryLayer.fact,
        server_default=MemoryLayer.fact.value,
    )

    # 记忆整理 retired instead of deleting, so the rows it decided against are
    # still here and still have to stay out of every read (`live_entries`).
    # Reinstating them would hand back facts 芝士 checked against the code and
    # found no longer true.
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Which pass retired it / created it.
    retired_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_dreams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_dreams.id", ondelete="SET NULL"), nullable=True, index=True
    )
