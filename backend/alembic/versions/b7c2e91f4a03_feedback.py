"""feedback: 平台级反馈的七张表

反馈是平台的功能，不属于任何项目 —— 一条反馈可能来自某个话题，也可能来自一个
harness 在沙箱里撞到的墙，后者根本没有话题可挂。所以 `topic_id` / `project_id`
都带 ON DELETE **SET NULL** 而不是 CASCADE：这条反馈说的是平台的事，房间归档、
机器退场都不该让它消失。

`feedback_seq` 是「FB-1042」那个人读号。它对外的意义只是「短、能念、能搜」，所以
是一个与 uuid 主键并存的独立序列，不是主键的一部分 —— 主键一旦被人念过就再也不能
改，而号可以按运维的需要重排。加序列的写法照 `a95752502bb0`（`task_seq`）。

枚举一律落成普通 VARCHAR(16)（`feedback.kind/status/visibility/priority`、
`feedback_timeline.status`），不用 PG 原生枚举，也不加 CHECK：状态集合几乎肯定
还要动，而没有 CHECK 就没有迁移，和 `alerts.level` / `alerts.kind` 是同一个决定。

四张子表都挂在 `feedback.id` 上带 CASCADE：一条被删掉的反馈下面不该留下孤儿评论。
`feedback_read_states` 是唯一一张与反馈条目无关的表 —— 每人一条的未读游标，理由
写在模型类上（不进 `alerts`：`alerts.project_id` 是 NOT NULL，而反馈与项目无关）。
`feedback_proposal_dismissals` 记的是「这个不用」：agent 的提案本身是话题里的一条
消息块，不落表，但它被拒绝过这件事必须留下，否则同一个问题每轮都会再问一遍。

索引只建「有读者的」那一批，理由逐条写在 `models.py` 的 `__table_args__` 上。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c2e91f4a03"
down_revision: str | Sequence[str] | None = "f3a8c5d2e917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The column below takes its default from this sequence, so it has to exist
    # first — `sa.Sequence` on a column does not create anything by itself.
    op.execute(sa.schema.CreateSequence(sa.Sequence("feedback_seq")))

    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "display_no",
            sa.Integer(),
            sa.Sequence("feedback_seq"),
            server_default=sa.text("nextval('feedback_seq')"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column(
            "security", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("expectation", sa.Text(), nullable=True),
        sa.Column("what_happened", sa.Text(), nullable=True),
        sa.Column("repro", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("logs", sa.Text(), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("environment", sa.String(length=255), nullable=True),
        sa.Column("author_handle", sa.String(length=64), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=True),
        sa.Column("author_is_agent", sa.Boolean(), nullable=False),
        sa.Column("submitted_by_handle", sa.String(length=64), nullable=True),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("assignee_handle", sa.String(length=64), nullable=True),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("display_no", name="uq_feedback_display_no"),
    )
    op.create_index(
        "ix_feedback_visibility_status_created",
        "feedback",
        ["visibility", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_visibility_created",
        "feedback",
        ["visibility", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_visibility_security_created",
        "feedback",
        ["visibility", "security", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_agent_created",
        "feedback",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("author_is_agent"),
    )
    op.create_index(
        "ix_feedback_author_created",
        "feedback",
        ["author_handle", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_submitted_by_created",
        "feedback",
        ["submitted_by_handle", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_assignee_created",
        "feedback",
        ["assignee_handle", "created_at"],
        unique=False,
        postgresql_where=sa.text("assignee_handle IS NOT NULL"),
    )

    op.create_table(
        "feedback_supports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("author_handle", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedback.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feedback_id", "author_handle", name="uq_feedback_support"),
    )

    op.create_table(
        "feedback_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("author_handle", sa.String(length=64), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=True),
        sa.Column("author_is_agent", sa.Boolean(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedback.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["feedback_comments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feedback_comments_feedback_created",
        "feedback_comments",
        ["feedback_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_comments_parent_id",
        "feedback_comments",
        ["parent_id"],
        unique=False,
    )

    op.create_table(
        "feedback_timeline",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("by_handle", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedback.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feedback_timeline_feedback_at",
        "feedback_timeline",
        ["feedback_id", "at"],
        unique=False,
    )

    op.create_table(
        "feedback_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("author_handle", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedback.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feedback_notes_feedback_created",
        "feedback_notes",
        ["feedback_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "feedback_read_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_handle", sa.String(length=64), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_handle", name="uq_feedback_read_state_user"),
    )

    # 提案本身是话题里的一条消息块（`meta.feedback_proposal`），不落表 —— 它就是
    # 给人看的那张卡。这张表只记「这个不用」，因为那是唯一一件卡消失之后还必须记得
    # 的事：否则同一个问题每轮都会再问一遍。
    op.create_table(
        "feedback_proposal_dismissals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("dismissed_by_handle", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "topic_id", "fingerprint", name="uq_feedback_proposal_dismissal"
        ),
    )


def downgrade() -> None:
    op.drop_table("feedback_proposal_dismissals")
    op.drop_table("feedback_read_states")
    op.drop_index("ix_feedback_notes_feedback_created", table_name="feedback_notes")
    op.drop_table("feedback_notes")
    op.drop_index("ix_feedback_timeline_feedback_at", table_name="feedback_timeline")
    op.drop_table("feedback_timeline")
    op.drop_index("ix_feedback_comments_parent_id", table_name="feedback_comments")
    op.drop_index(
        "ix_feedback_comments_feedback_created", table_name="feedback_comments"
    )
    op.drop_table("feedback_comments")
    op.drop_table("feedback_supports")
    op.drop_index("ix_feedback_assignee_created", table_name="feedback")
    op.drop_index("ix_feedback_submitted_by_created", table_name="feedback")
    op.drop_index("ix_feedback_author_created", table_name="feedback")
    op.drop_index("ix_feedback_agent_created", table_name="feedback")
    op.drop_index("ix_feedback_visibility_security_created", table_name="feedback")
    op.drop_index("ix_feedback_visibility_created", table_name="feedback")
    op.drop_index("ix_feedback_visibility_status_created", table_name="feedback")
    op.drop_table("feedback")
    op.execute(sa.schema.DropSequence(sa.Sequence("feedback_seq")))
