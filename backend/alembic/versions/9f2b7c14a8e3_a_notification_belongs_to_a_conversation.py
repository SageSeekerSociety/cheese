"""A notification is about the conversation it names

Revision ID: 9f2b7c14a8e3
Revises: ff58bbb04b89
Create Date: 2026-10-07

The inbox row said which place it was about with ``topic_id``, under a foreign
key to ``topics`` — a room. A decision request asked in a task or a 支线 is not
about the room it hangs in, and the receipt written back when someone decides it
(`resolve`) went to the room's own line; the conversation that asked never read
the answer.

``conversations`` registers a room and a task under its own id (f7985445d2bf),
and every other row that belongs to a conversation already points at it
(b6fcc6362b79). This moves the foreign key there, so the column can hold a
room's own id, a task's or a 支线's. Existing rows name a room, which is a
conversation of its own, so the same ids carry forward unchanged.

Its name lags: the code reads and writes it as ``conversation_id``, mapped onto
the column it has always had (``mapped_column("topic_id")``). A rename is the new
name mapped onto the old column, and the physical rename — like the drop — is a
later release (`.claude/rules/migrations.md`, rule 4).
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "9f2b7c14a8e3"
# 接在 main 当下的链尾后面：本文件写下时那个头是 c3a8e5f1d702，此后 main 每加
# 一条迁移都要再往后挪一次（1ed9ee06ed4a、7d3e1c4b9a20、c11a23e6ea8d、
# a9c88363ec79、e241eeb9ffdb、4b7e2d9c1f30、ff58bbb04b89 ……）。几条互不相干，
# 只是不能分叉。
down_revision: str | Sequence[str] | None = "ff58bbb04b89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "fk_notification_topic_id_conversations"


def upgrade() -> None:
    # 旧外键指着 `topics`，这一列于是只装得下房间。换指 `conversations` —— 房间和
    # 任务都在里面注册。删旧外键也锁 `topics`（b6fcc6362b79 记着这个死锁），加新外
    # 键锁 `conversations`，两张都报给重试。
    with_lock_retries("notification, topics, conversations")

    op.drop_constraint("notification_topic_id_fkey", "notification", type_="foreignkey")
    # 表里已经有行：外键先 NOT VALID 加上（不扫表），再单独校验一次。
    op.create_foreign_key(
        _CONSTRAINT,
        "notification",
        "conversations",
        ["topic_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.execute(f"ALTER TABLE notification VALIDATE CONSTRAINT {_CONSTRAINT}")


def downgrade() -> None:
    raise NotImplementedError("A notification is about the conversation it names.")
