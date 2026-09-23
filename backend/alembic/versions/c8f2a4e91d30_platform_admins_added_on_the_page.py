"""admin: 页面上加的平台管理员

一张表，装「不是从配置里来的」那部分管理员。

`settings.platform_admin_handles`（部署时的环境变量名 `PLATFORM_ADMIN_HANDLES`，
旧名 `FEEDBACK_ADMIN_HANDLES` 还认）一直是**唯一**的判据，这一版把它降级成**根管
理员**：部署时仍然必填（没填后端起不来，`config.py` 的那道闸门原样留着），但页面
上**删不掉**。页面上加的那些进这张表。判据是并集。

表名是 `platform_admins` 而不是 `feedback_admins`：管理员是**平台**的，不是反馈的。
这份名单同时管着反馈队列和「谁来加下一个管理员」，以后还会管别的；挂在反馈名下的话，
反馈功能哪天被砍，管着平台权限的这张表就成了它的附属物。模型在
`app/domain/admin/models.py`。

为什么两份而不是「干脆全搬到表里」：加人的入口本身也要管理员。全在表里的话，
一次误操作（或一次 SQL）把表清空，就再没有人能打开那个页面把人加回来 —— 而配置
那一份要改回去得有服务器权限，那正是这种补救该在的位置。这是「起不来比静默没
管理员好」那次判断的同一面。

不建外键、不存 user id：`handle` 是这个平台上「谁」的一贯写法（`author_handle`、
`assignee_handle`、`dismissed_by_handle` 都是快照），这一族表里没有一处挂
user 外键。`added_by_handle` 也按快照存 —— 加人的那个人注销之后，这一行仍然要说
得出是谁加的。

唯一约束是这张表唯一的索引，也是并发答案：两个人同时加同一个人，数据库拒掉后来
者，写入路径把它折成和「重复加」同一个答复（`repositories.add_admin` 用
`on_conflict_do_nothing` + `RETURNING`，和 `add_support` 同一个形状）。

不迁移任何数据：这份名单以前不存在，配置里那些人照旧从配置读，不抄进表里 ——
抄一份就等于多一个会和配置漂开的副本，而漂开的表现是「页面显示他在名单里、
实际上他不在」。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8f2a4e91d30"
down_revision: str | Sequence[str] | None = "b7c4e19f2a83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_admins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("handle", sa.String(length=64), nullable=False),
        sa.Column("added_by_handle", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("handle", name="uq_platform_admin_handle"),
    )


def downgrade() -> None:
    op.drop_table("platform_admins")
