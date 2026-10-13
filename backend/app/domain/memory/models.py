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
    JSON,
    BigInteger,
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
from app.domain.memory import scopes
from app.domain.memory.files import MemoryFileScope


class MemoryLayer(enum.StrEnum):
    """Which layer a fact belongs to — i.e. when it is allowed to cost prompt.

    A pool grows without bound while the prompt does not, so "everything I
    learned" cannot be the injection unit forever: past a few dozen facts the
    ones that decide *how this agent behaves at all* start losing their seat to
    whatever happened to be written last. Splitting the pool is what stops that:

    - ``core`` — who this agent is, its standing rules and goals. Small,
      hand-curated, injected in full every single turn, never filtered by
      relevance. If it is only true sometimes, it is not core.
    - ``fact`` — everything else it learned. Retrieval used to be a query away
      (`cheese_recall`, since withdrawn): the pool is not injected, so no one
      turn pays for all of it. 记忆现在住会话目录里那棵树，这两档跟着这张表
      一起等迁移（见 `store.py`）。
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


def project_of_scope(scope: scopes.MemoryScope, scope_id: str) -> uuid.UUID | None:
    """The project a pool belongs to, read back out of its key; None for a
    scope that is not keyed by a project, or a key that is not in that shape."""
    if scope not in (scopes.MemoryScope.agent_project, scopes.MemoryScope.user):
        return None
    try:
        return uuid.UUID(scope_id.split(":", 1)[0])
    except ValueError:
        return None


def agent_project_scope_id(project_id: str | uuid.UUID, agent_handle: str) -> str:
    """scope_id for :attr:`scopes.MemoryScope.agent_project`.

    ``scope_id`` is one plain string shared by every scope, so the two parts
    are joined rather than given columns of their own. A handle cannot contain
    ``:`` (it is a username), so the split is unambiguous.
    """
    return f"{project_scope_prefix(project_id)}{agent_handle}"


def user_scope_id(
    project_id: str | uuid.UUID, agent_handle: str, person_handle: str
) -> str:
    """scope_id for :attr:`scopes.MemoryScope.user` — one agent's notes on one person.

    Three parts, because all three are needed to say whose knowledge this is:
    the instance owns it, and an instance only exists inside its project
    (结论 8). Neither a handle nor a project id can contain ``:``, so the split
    stays unambiguous.
    """
    return f"{agent_project_scope_id(project_id, agent_handle)}:{person_handle}"


def user_scope_about(person_handle: str) -> str:
    """The tail every :attr:`scopes.MemoryScope.user` pool about this person ends with.

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

    scope: Mapped[scopes.MemoryScope] = mapped_column(
        Enum(scopes.MemoryScope, native_enum=False, length=16), index=True
    )
    # Which pool: the composite keys built by `agent_project_scope_id` /
    # `user_scope_id`, or a skill name. 200 because the longest key
    # this can hold is `user_scope_id`: a uuid (36) plus an agent handle and a
    # person handle (64 each, `agent_instance.handle` / `topic_memberships.
    # member_handle`) plus two separators — 166. A key that does not fit is not
    # a truncated pool, it was a 500 out of the memory write endpoint and a
    # failed migration, so the column has to outrun the widest key by
    # construction.
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


class MemoryDreamRunStatus(enum.StrEnum):
    """一次整理跑到哪了。

    ``refused`` 是**没有开跑**：防删护栏（`dream.removal_refused`）在写回之前
    就判了「这一次删得不像人删的」，于是整轮作废、平台上一条都没少。它和
    ``failed`` 分开，因为读的人要做的下一步不一样——失败要去查会话，被拦下来
    要去看看是不是真的想删那么多。
    """

    running = "running"
    completed = "completed"
    failed = "failed"
    refused = "refused"


class MemoryDreamState(UuidPk, Timestamps, Base):
    """一个项目的整理账本：上次整理是什么时候，以及现在有没有人在整理。

    **一个项目一行**，唯一约束就是「一把锁」在数据库那一侧的样子。锁用
    ``claimed_at`` 做占位的比较并交换（`dream.claim`）：一行 UPDATE，谁先把它从
    「没有 / 过期」改成「现在」谁就跑，另一个人拿不到就是拿不到——不用把一次可能
    几分钟的整理包在一个长事务里，也就不会因为整理没跑完而挡住这个项目别的事。

    ``last_dream_at`` 是**触发阈值的时间原点**：累计 token 只算它之后的
    （`dream.pending_output_tokens`）。写在这里而不是从 `resource_usage` 反推，
    是因为「上次整理到哪」不是一份能重新算出来的东西——整理自己花掉的那部分要
    按 kind 剔掉，两次整理之间平台还可能重启。
    """

    __tablename__ = "memory_dream_states"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    #: 上次整理**成功**结束的时刻；从没整理过就是 NULL（阈值的原点取更早的那个）。
    last_dream_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 现在是谁占着这把锁。过期由 `dream.claim` 判（`CLAIM_TTL`），不是靠
    #: 有人来清——被打断的一次整理（进程没了、机器没了）不该把这个项目锁死。
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MemoryDreamRun(UuidPk, Timestamps, Base):
    """一次整理的记录。整理是一轮真正的会话，跑完了才知道它动了什么。"""

    __tablename__ = "memory_dream_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default=MemoryDreamRunStatus.running.value
    )
    #: 这一轮起点时累计了多少 token（触发它的那个数）。记录它，是为了回答
    #: 「这次为什么跑起来了」，以及事后核对阈值。
    tokens_at_start: Mapped[int] = mapped_column(BigInteger, default=0)
    #: 整理自己在这一步之前已经花掉的输出 token，收尾时写回，用来核对「不计自身」。
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 芝士自己写的交代（那一轮的最后一段），或失败/被拦下来的原因。
    summary: Mapped[str] = mapped_column(Text, default="")
    #: 这一轮动过的文件（`team/x.md`、`private/alice/y.md`），给人一眼扫。
    files_changed: Mapped[list] = mapped_column(JSON, default=list)


class MemoryMigrationStatus(enum.StrEnum):
    """一次旧表搬迁跑到哪了。"""

    #: 报告出来了，等复核。**这个状态下一个字都没写。**
    draft = "draft"
    #: 复核过（`approved_by`），可以落笔。
    approved = "approved"
    applied = "applied"
    #: 落笔时撞上了冲突（有人在复核期间改了那棵树）。整次都没写。
    failed = "failed"


class MemoryMigrationPlan(UuidPk, Timestamps, Base):
    """一次旧记忆迁移的计划、报告和它的结局。

    计划要**存下来**，不能等到 apply 的时候再问一次模型：人复核的是**这一份**报告，
    再问一次得到的是另一份——两次之间模型可以给出不同的去处，而人点头的是第一条。
    所以 dry-run 把「模型的原始决定」和「它算出来的计划」一起存进来，apply 只重放，
    不再问模型。

    `sources` 存的是旧记忆**当时的原样**（只读的那张表，和复制过来的正文）：apply
    前拿它算一次指纹，和 `sources_digest` 对不上就说明这份报告描述的不是现在这张
    表了，重跑 dry-run。

    这张表也是「哪些旧条目已经搬过」的账本（`source_ids`）：第二次 dry-run 不能再
    把已经搬过的那几条再搬一遍，否则新树里会长出一份重名的东西。
    """

    __tablename__ = "memory_migration_plans"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default=MemoryMigrationStatus.draft.value
    )
    #: 旧记忆那一份的指纹（`migration.sources_digest`）。
    sources_digest: Mapped[str] = mapped_column(String(32), default="")
    #: 报告正文（`migration.render_report` 的输出）。**人复核的就是它**。
    report: Mapped[str] = mapped_column(Text, default="")
    #: 旧记忆的原样：`[{source_id, origin, where, content}]`。
    sources: Mapped[list] = mapped_column(JSON, default=list)
    #: 已经搬过的那些 source_id（这份计划里的）。
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    #: 模型的原始决定，原样存着（重放计划要用）。
    decisions: Mapped[list] = mapped_column(JSON, default=list)
    #: 要写的文件：`[{scope, owner, path, content, version, sources, is_new}]`。
    files: Mapped[list] = mapped_column(JSON, default=list)
    #: 要写的索引：`[{scope, owner, content, version, added_lines}]`。
    indexes: Mapped[list] = mapped_column(JSON, default=list)
    #: 只建议、不自动改的那些。
    suggestions: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    #: 复核人。**落笔前必须是它**，见 `settings.memory_migration_reviewer`。
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 结局：写进去几个文件，或者为什么停下。
    summary: Mapped[str] = mapped_column(Text, default="")
