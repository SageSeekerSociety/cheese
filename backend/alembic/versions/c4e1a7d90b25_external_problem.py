"""external_problem: 外站题号、外站账号、每个人的核对结果

三张表都是新的，所以没有回填、也没有 CONCURRENTLY 的顾虑（`.claude/rules/migrations.md`）。

`task_external_problem_seq` 要显式建：列上的 ``sa.Sequence`` 自己不创建任何东西，
写法照 `b7c2e91f4a03`（`feedback_seq`）。

**这张表上没有外键，是有意的。** ``task_external_problem.task_id`` 只想指
``tasks.id``，``external_problem_result`` 的 ``task_id`` / ``member_id`` 同理。
但它们指的那两个域不属于本域，而 ``tests/unit/test_domain_import_guard.py`` 对
「domain 之间引 repository」是一张双向棘轮：新增一条边会失败。代价是删题 / 退赛
时没有级联，那两处的清理要自己写；换来的是这一刀不用去动那张白名单。

索引只建唯一约束带不出来的那几个：`task_external_problem` 按 ``task_id`` 查走的是
``uq_task_external_problem_task_source`` 的第一列，不另建。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e1a7d90b25"
down_revision: str | Sequence[str] | None = "ea1b19de526b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_problem_account",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", name="uq_external_problem_account_source"),
    )

    # The column below takes its default from this sequence, so it has to exist
    # first — `sa.Sequence` on a column does not create anything by itself.
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_external_problem_seq")))

    op.create_table(
        "task_external_problem",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("task_external_problem_seq"),
            server_default=sa.text("nextval('task_external_problem_seq')"),
            nullable=False,
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("problem_no", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_status",
            sa.String(length=16),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "last_error", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "source", name="uq_task_external_problem_task_source"
        ),
    )

    op.create_table(
        "external_problem_result",
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("student_no", sa.String(length=64), nullable=False),
        sa.Column("solved", sa.Boolean(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id", "member_id", "source"),
    )


def downgrade() -> None:
    op.drop_table("external_problem_result")
    op.drop_table("task_external_problem")
    op.execute(sa.schema.DropSequence(sa.Sequence("task_external_problem_seq")))
    op.drop_table("external_problem_account")
