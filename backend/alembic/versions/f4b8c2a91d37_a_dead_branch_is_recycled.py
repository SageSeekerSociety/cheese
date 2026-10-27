"""a_dead_branch_is_recycled —— threads.recycled_at

一条支线里报错，而没人回来接手，它就一直把整间频道点红：`block.waits` 只认
「恢复」（有人在里面又开口，或者一个会话读进了输入），不认时间，所以一条死掉的支线
的红灯永远亮着。三天之后把它收起来 —— 不再点红、移出默认的支线列表，里面的块一条
都不删（`thread.recycle`）—— 需要记下收起来的时刻。

新增一列，可空；降级删掉它，也就丢掉了「这条被收过」这个事实（支线本身照旧）。

Revision ID: f4b8c2a91d37
Revises: d7e1a2b3c4f5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "f4b8c2a91d37"
down_revision: str | Sequence[str] | None = "a9c3e5f7b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ix_threads_recycled_at"
WHERE = "recycled_at IS NOT NULL"


def _invalid() -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": NAME},
        )
        .scalar()
    )


def upgrade() -> None:
    # 加一列要 ACCESS EXCLUSIVE，而正在服务的那个后端还在写这张表。
    with_lock_retries("threads")
    op.add_column(
        "threads",
        sa.Column("recycled_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 收起来的支线按这一列筛（「这间房还开着的支线」每读一页都要问一次）。部分索引
    # 只收收起来的那几条；CONCURRENTLY，因为正在服务的那个后端还在往下插行。
    with op.get_context().autocommit_block():
        if _invalid():
            op.drop_index(NAME, table_name="threads", postgresql_concurrently=True)
        op.create_index(
            NAME,
            "threads",
            ["recycled_at"],
            postgresql_where=sa.text(WHERE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            NAME, table_name="threads", postgresql_concurrently=True, if_exists=True
        )
    op.drop_column("threads", "recycled_at")
