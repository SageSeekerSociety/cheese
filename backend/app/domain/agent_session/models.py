"""Agent session — where ONE agent's conversation in one place got to.

The third of the three layers the other two already name (see
:mod:`app.domain.agent_instance.models`): a **type** is 出厂设置 and belongs to no
project, an **instance** is that type working in one project and owns what it has
learned there, and a **session** is one conversation that may be thrown away.
Until now the third layer was a single ``topics.session_id`` column, which said —
structurally, not by policy — that a place hosts at most one agent.

Keyed by ``(conversation, agent_handle, harness)`` — so a room can host several
agents at once and each keeps its own conversation, and a task is worked in a
conversation of its own rather than inside the room's. A conversation is a room
or a task (:mod:`app.domain.conversation.models`); its id is the room's or the
task's own. ``agent_handle`` is
:attr:`~app.domain.agent_instance.services.ResolvedAgent.handle`, the same key the
agent's memory pool is named by — not the instance's uuid, because a project that
never configured an agent has no instance row at all and NULL does not compare
equal to NULL in a unique index. It is also not the authorship handle
(``cheese-<topic hex>``): that answers "who took this action", while this answers
"whose conversation is this".

One consequence worth stating: the implicit default and a later-configured
instance that uses the same harness and is called ``cheese`` share a key,
so configuring one inherits the conversation the project's 芝士 already had.
That is the same continuity-over-purity call ``ResolvedAgent`` already makes
for the memory pool.

Handing a topic to a different agent therefore destroys nothing — the new agent
looks up a key that has no row and starts fresh, and handing it back finds the
old row still there.

A row also carries WHERE this conversation's process runs, and a copy of the
work computer it was given. 一个话题一个容器（2026-09-28 决定）：一间房只有一条
算力选择，房间里的会话都工作在它算出来的那台机器上；一个任务没有自己的选择时也用
它所在房间的那一项，有就用自己的（``tasks.compute_config``）。所以那一列
（``execution_request`` / ``work_lease``）是那项选择的副本，解析的时候问的是选择
本身（``machine/session_work._attempt``）。进程在哪台会话机上仍然是这条会话自己的
事（``runtime_location``）。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

# The registry `conversation_id` points at: mapped wherever a session is, so the
# foreign key resolves in a process that never imports `app.models`.
from app.domain.conversation.models import Conversation  # noqa: F401


@dataclass(frozen=True, slots=True)
class SessionPlace:
    """一条会话解析出来的地点。

    两件事，一条会话上各占一列，因为它们各自会变：进程可以从一台会话机搬到另一台，
    而手上那棵工作树不动；工作机器可以换，而进程不动。混成一列的那些年里，"换机器"
    只能整条一起换，同一个房间的第二个 agent 连开都开不起来。

    工作机器那一半是**房间**的（一个话题一个容器，2026-09-28 决定，推翻结论 60）：
    ``lease`` 是房间那一项选择落到这条会话上的那一份，同一条会话的每一轮拿到同一个
    句柄，同一个房间里的两条会话拿到同一台机器上的两份。会话机那一半仍然是这条会话
    自己的。
    """

    #: 会话机：这条会话的进程在哪台机器上。
    machine: str
    #: 哪条通道开的这个进程——通道自己认领会话时按它过滤。
    channel: str
    #: 这一代资源。房间重开会换代，旧代的凭证与残留进程一律不再当作本会话的。
    resource_id: str
    #: 骨架自己要记的运行状态（状态目录、agent handle 之类），平台不解析。
    runtime: dict
    #: 工作机器租约：这条会话的手。私聊这类"手就在会话机上"的路子没有单独的租约。
    lease: dict | None


class AgentSession(UuidPk, Timestamps, Base):
    __tablename__ = "agent_sessions"
    # One session per agent per conversation.
    __table_args__ = (
        Index(
            "uq_agent_sessions_conversation",
            "conversation_id",
            "agent_handle",
            "harness",
            unique=True,
        ),
    )

    # The conversation this session is in: a room's or a task's id.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    # ResolvedAgent.handle — the agent's key inside its project.
    agent_handle: Mapped[str] = mapped_column(String(64))
    # 哪个骨架跑的这条会话。模型上没有默认值：一个不说骨架的写入，等于把「跑的是
    # 哪个」又答了一遍（结论 28 说只许有一个答法，那个答法是部署设置加项目设置）。
    # 每一条写路径都说得出来——全走 ``AgentSessionRepository._upsert``，它显式传
    # harness——所以这里不必替谁猜。库里那一列的 DDL 默认值也在同一次改动里去掉了
    # （迁移 c9f41b7a2e08），不然这条保证只在按 metadata 建表的测试库上成立。
    harness: Mapped[str] = mapped_column(String(64))
    # What the harness resumes this conversation by. Opaque to the platform: it
    # is Claude Code's session id today and whatever the next harness hands back
    # tomorrow, so nothing here may parse it.
    #
    # NULL until the harness has handed one back. A row now appears the moment
    # this session takes a machine — which is before its first turn has said
    # anything — so "has this place run" is `resume_token IS NOT NULL`, not the
    # bare existence of a row.
    resume_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 这条会话的工作机器租约：手在哪、这一代的工作区在哪、装到了什么程度。
    # **房间**租手（一个话题一个容器，2026-09-28 决定，推翻结论 60）：这一份是房间
    # 那一项选择落到这条会话上的那一份，房间里每一条会话的租约落在同一台机器上，
    # 换机器是房间一起换（`machine/session_work.request_choice`），不是谁自己搬。
    # 一位成员自己的 Claude Code 例外：它的手是它会话所在的那台主人电脑，不跟房间
    # （`session_work._own_host`）。
    #
    # ``none_as_null=True``：没租到手要落成 SQL NULL。默认那一档会把 Python 的
    # ``None`` 序列化成 JSON ``'null'`` 存进去，于是 ``work_lease IS NOT NULL``
    # 对「手就在会话机上」的 pi 会话也为真，读的人拿到的却是 ``None``。
    work_lease: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    # A requested choice and its authenticated authorization source. Recording
    # intent does not reserve a machine; the first execution tool consumes it.
    execution_request: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    # 这条会话的进程在哪台会话机上，连同开它的通道与骨架的运行状态。
    runtime_location: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    # 这条会话上一次落位是什么时候。房间只有一块屏，归最后开屏的那条会话——
    # 排这个先后要的就是落位的时刻，不是这一行上任何一列的写入时刻。
    # ``updated_at`` 排不了：每轮跑完存 ``resume_token`` 也在动同一行，于是「最后
    # 开屏的」会变成「最后说过话的」，屏就归错了会话。
    placed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 这条对话最近一次被告知的项目现状，每一段一个摘要
    # （`harness.prompt.SessionOpening.digests`）。下一轮只把和它不一样的那几段再
    # 说一次，好让系统提示词在会话里保持不变。NULL 是还没告诉过：那一轮整份都说。
    told: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)

    def place(self) -> SessionPlace | None:
        """这条会话在哪——它自己那一半地点的唯一入口。

        入口在会话上，不在房间上：同一条会话的每一轮解析出同一个句柄。没有
        ``runtime_location`` 就是还没有地点，下一轮重新租，而不是去猜房间上记着
        什么。

        ``lease``（工作机器）是**房间**那一半，见 :class:`SessionPlace`：它跟着
        这一行走只是为了读的人当场看得见，解析它的是房间那一项。
        """
        location = self.runtime_location
        if not location:
            return None
        return SessionPlace(
            machine=location["device_id"],
            channel=location["channel"],
            resource_id=location["resource_id"],
            runtime=location.get("runtime") or {},
            lease=self.work_lease,
        )
