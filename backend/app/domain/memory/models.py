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
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk
from app.domain.memory.files import MemoryFileScope


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
      ``cheese_recall``.
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


def project_of_scope(scope: MemoryScope, scope_id: str) -> uuid.UUID | None:
    """The project a pool belongs to, read back out of its key; None for a
    scope that is not keyed by a project, or a key that is not in that shape."""
    if scope not in (MemoryScope.agent_project, MemoryScope.user):
        return None
    try:
        return uuid.UUID(scope_id.split(":", 1)[0])
    except ValueError:
        return None


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


def parse_user_scope_id(scope_id: str) -> tuple[uuid.UUID, str, str] | None:
    """``(project id, agent handle, person handle)`` back out of a
    :func:`user_scope_id`, or None when the key is not in that shape."""
    parts = scope_id.split(":")
    if len(parts) != 3:
        return None
    try:
        project_id = uuid.UUID(parts[0])
    except ValueError:
        return None
    return project_id, parts[1], parts[2]


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
    # a truncated pool, it is a 500 out of `cheese_remember` and a failed
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


class MemoryFileRecord(UuidPk, Timestamps, Base):
    """一条记忆：一个 markdown 文件，按项目 + 作用域 + 路径定位（见 `files.py`）。

    这张表是真相，会话目录里那一份是副本：agent 用原生的 Write/Edit 改副本，
    平台按版本号同步回这里，冲突拒绝并让它重读。所以每次写入都带 `version`，
    每次成功都 `version + 1`——「文件在会话里被改过、库里那一份已经不是它了」
    这件事必须有地方能看出来，否则两边同时改就是后写的那个静默赢。

    ``owner_handle`` 对 team 记忆是**空串**，不是 NULL。设计上写的是「可空」，
    但可空在这里会真的坏事：唯一约束在 SQL 里是 NULL != NULL，同一个项目里
    `MEMORY.md` 于是可以插进去任意多行不带 owner 的 team 记忆，而那正是
    「先查重再新建」要挡的东西——约束不生效的地方，查重就只剩一次竞态。空串
    让这一列在任何情况下都参与唯一约束，而「空串 = team」由 `prefix_of` 一手
    决定，没有第二个地方可以读错。
    """

    __tablename__ = "memory_files"

    # 一个作用域一份索引、一条记忆一个文件：这张唯一约束就是「先查重，再新建」
    # 在数据库那一侧的样子。按 (项目, 作用域, 人, 路径) 而不是按 id 查，走的就是
    # 它。
    __table_args__ = (
        Index(
            "uq_memory_files_scope_path",
            "project_id",
            "scope",
            "owner_handle",
            "path",
            unique=True,
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[MemoryFileScope] = mapped_column(
        Enum(MemoryFileScope, native_enum=False, length=8), index=True
    )
    # 空串 = team（见类注释）；private 时是这个人的 handle。
    owner_handle: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # 本作用域目录内的相对路径：`MEMORY.md` 或 `<slug>.md`。
    path: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    # 乐观锁：会话副本带着它下去，回写时必须原样带回来。
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # 谁改的。人做的和芝士做的走同一个字段——这一列回答的是「这一版是谁写的」，
    # 而两种写入在下面这条变更记录里长得一样。
    updated_by: Mapped[str] = mapped_column(String(64), default="")
