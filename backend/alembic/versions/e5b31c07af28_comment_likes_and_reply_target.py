"""feedback: 评论点赞 + 楼内回复的指代

两件小事，都是「评论现在还缺的那一半」。

**`feedback_comment_likes`**：形状照抄 `feedback_supports` —— 一条
`UniqueConstraint(comment_id, author_handle)`，别的都不加。点赞和支持是同一类事实
（一个人、一样东西、一个时间，重复点要折成一行），两张表表达同一件事就该长成同一个
样子。**不往 `feedback_comments` 上加计数列**：读者自己那一份状态（「我点过没有」）
天然是每人一条、不可能是计数器，所以 per-person 的行无论如何都得存；行存下了，计数器
就成了同一个数的第二份拷贝。唯一约束同时是并发答案：两个人同时点，数据库拒掉后来者，
写入路径把这次拒绝折成和「重复点」同一个答复。

索引只靠这条唯一约束（它的第一列就是 `comment_id`），不另建单列索引 —— 读它的每一次
查询都带 `comment_id`，再建一个就是给同一个读者建第二个索引。

**`feedback_comments.reply_to_handle`**：`parent_id` 只指顶层，回复的回复会被折到
祖辈上（`FeedbackService.comment`），折的那一刻「这条在回谁」就丢了，而人读一栋楼时
问的正是这一句。所以在这里存一个**当时那个人的 handle 快照**，写入时从服务端自己读出来
的那一行取，客户端猜不出来也编不了。存 handle 而不是 `reply_to_comment_id`：删评论是软删，
被回的那条可能已经被过滤出帖子而回复还在，指针那一刻正好悬空成「回复 (已删除)」，而快照
照旧说得出是谁 —— 那句仍然为真，也仍然是有用的那一半。这和 `author_handle` 同样是快照。

只在**被回的那条本身是回复**时才写（回楼主的那些不写）：那条回复本来就紧挨着楼主渲染，
写了等于每条楼内回复都挂一句没有信息量的「回复 楼主」。NULL 因此读作「没有指代对象」，
前端不显示这一句。

**存量行不补**：这一列以前不存在，历史回复的指代对象没有任何地方记过（`parent_id` 已被
折叠），补不出来，也没有一个能编的默认值。留给 NULL —— 渲染上和「没有指代对象」同一条路。

**顺带回填一批孤儿回复**。删评论现在会连它下面的回复一起软删（`soft_delete_comment`），
而在这之前只盖了被删的那一行。所以库里可能有「父亲已经软删、回复的 `deleted_at` 还是
NULL」的行：它们被算进评论数、又被 `GET .../comments` 返回，而客户端是「取顶层、再问每
条的回复」，于是谁也画不出来 —— 一条数得出来、看不见、也没有按钮能删掉的评论。回填用的
是和新代码同一个判据，修的是这次改动之前就存在的那种行。

回填**不是**可见性的保证：级联挡不住「同一瞬间插进来的回复」（外键检查拿的是
`FOR KEY SHARE`，和删除那条 `UPDATE` 的 `FOR NO KEY UPDATE` 不冲突）。读的时候挡
（`live_comment_clause`）才是那道保证，回填负责让表里的 `deleted_at` 不再撒谎。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b31c07af28"
down_revision: str | Sequence[str] | None = "b4d1a70c9e52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feedback_comments",
        sa.Column("reply_to_handle", sa.String(length=64), nullable=True),
    )

    op.execute(
        """
        UPDATE feedback_comments AS child
        SET deleted_at = now()
        FROM feedback_comments AS parent
        WHERE child.parent_id = parent.id
          AND parent.deleted_at IS NOT NULL
          AND child.deleted_at IS NULL
        """
    )

    op.create_table(
        "feedback_comment_likes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("author_handle", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["comment_id"], ["feedback_comments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "comment_id", "author_handle", name="uq_feedback_comment_like"
        ),
    )


def downgrade() -> None:
    op.drop_table("feedback_comment_likes")
    op.drop_column("feedback_comments", "reply_to_handle")
