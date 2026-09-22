"""resource_usage(created_at) / feedback_timeline(at) —— 管理看板按天的两个范围扫

管理看板三块分类里有两块要问「**某个时间窗口里**发生了什么」：

* **用量**（`GET /admin/stats/usage`）按天聚合 `resource_usage`，读窗口是
  `created_at >= since AND created_at < since + days`。这张表是 schema 里增长最快的
  一张（计量代理每调一次 `/v1/messages` 写一行），而 `created_at` 上一根索引都没有
  —— 已核实的索引只有 `project_id` / `topic_id` / `task_id` / `turn_id` 四条，全是
  等值键，对范围条件一个也用不上。计划因此只能是全表顺序扫，而且窗口越小越不划算：
  扫的行数和窗口无关，扫完才把大部分行丢掉。
* **反馈的「解决/上线」序列与筛选**（`GET /admin/stats/feedback` 的 series，
  `GET /admin/feedback` 的 `resolved_since` / `deployed_since`）问的是
  `feedback_timeline` 里「这条在窗口内**有过**该状态的变迁」，条件是
  `status = ? AND at >= since`。既有索引 `ix_feedback_timeline_feedback_at` 的**打头
  列是 `feedback_id`**，所以它服务的是「这一条的变迁」；**跨反馈按时间范围查用不上
  它** —— 打头列不是 `at` 就不是范围扫的入口。新索引的 `at` 打头补的正是这一条。

## 预期效果

两条都是单纯的 B-tree，无数据变更。预期是把上面两个范围读从全表顺序扫变成索引范围
扫（用量那条还能顺带靠索引序避免排序，聚合本身仍要读命中的行 —— 索引省的是**扫描
范围**，不是聚合）。行数少的时候规划器**正确地**仍会选顺序扫，那不是索引没用。

## 量过的数（自建合成表，本机 Postgres 16，窗口取「最近 7 天」）

在这个仓库自己的临时库里造的合成表，没有往共享测试库里灌数据（那是别的 run 在读的
库）。两张表都 `ANALYZE` 过、缓存预热、`TIMING OFF`，取三次里最快的一次：

| 表 | 有索引 | 无索引 |
|---|---|---|
| `resource_usage` 30 万行、`created_at` 铺开 400 天 | **13.2 ms**（`Bitmap Index Scan` → 4926 行、3014 个堆块） | 37.7 ms（`Parallel Seq Scan`，每个 worker 滤掉 98358 行） |
| `feedback_timeline` 20 万行、`at` 铺开 400 天 | **9.1 ms**（`Bitmap Index Scan` → 3386 行，`count(DISTINCT)` 得 806） | 28.6 ms（`Parallel Seq Scan`，每个 worker 滤掉 99597 行） |

「无索引」那一侧是同一份合成表、`SET enable_indexscan=off; SET enable_bitmapscan=off`
跑出来的 —— 不是估算，是同一台机器上的对照。规划器在 `ANALYZE` 之后两次都选了新索引，
也就是这两条确实**用得上**、不是摆设。

差别只有两三倍而不是数量级，这一点要如实说：30 万行窄表在这个库的缓存里放得下，
而顺序扫那一侧还开了并行（2–3 个 worker）。真正的对比不是「快多少」而是**扫描的行数
和窗口无关** —— 无索引那一侧每次都扫全表再筛掉 99% 以上，差距随表长线性拉大；索引
那一侧读的是命中行所在的那三千来个块，不随表长变。这也是为什么行数少的时候规划器
**正确地**仍会选顺序扫：那是这个判断的另一半，不是索引没用。

顺带记一个坑：本机 Postgres 的 `TimeZone` 是 `Asia/Shanghai`，单参数的
`date_trunc('day', timestamptz)` 会按**会话时区**切天 —— 看板的按天序列要写
`date_trunc('day', col, 'UTC')` 才是 UTC 的天。写在这里是因为它不报错，只是把每天
的边界挪了八小时，两边的图各自看着都正常。

## 锁

`CREATE INDEX` 不是 `CONCURRENTLY`（后者不能在事务里跑，而 alembic 默认在事务里）。
代价是一次 `ACCESS EXCLUSIVE` 锁：建索引期间这张表**读写都被挡住**。按今天这两张
表的规模是毫秒级；`resource_usage` 是增长最快的一张，将来要在一张已经很大的表上补
同类索引时，这里是一条要拆成单独运维动作的线。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e7f12b60"
down_revision: str | Sequence[str] | None = "d4e7a91b3c58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        op.f("ix_resource_usage_created_at"),
        "resource_usage",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feedback_timeline_at"),
        "feedback_timeline",
        ["at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_feedback_timeline_at"), table_name="feedback_timeline")
    op.drop_index(op.f("ix_resource_usage_created_at"), table_name="resource_usage")
