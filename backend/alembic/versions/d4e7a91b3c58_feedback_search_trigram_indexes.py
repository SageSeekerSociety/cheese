"""feedback 搜索列上的 trigram 索引

Revision ID: d4e7a91b3c58
Revises: d3f0a91c7b45
Create Date: 2026-09-21 00:00:00.000000

反馈的搜索框是**子串**匹配（`ILIKE '%词%'`，四列 OR），而 `title` / `summary` /
`problem` / `author_handle` 上原本一根索引都没有：`author_handle` 那条复合索引
`(author_handle, created_at)` 的前缀对 `'%…%'` 用不上，另外三列上一根都没有。
唯一的计划就是全表扫。

`pg_trgm` 把字符串切成三元组，于是 `'%词%'` 这种**两边都有通配符**的写法也能走
索引 —— 这正是 B-tree 做不到、而搜索框需要的形状。B-tree 只能在你只写后缀通配
（`LIKE '词%'`）时有用，那不是用户在搜索框里打东西的方式。

## 量过的数（200000 行、中文标题/正文，本机 Postgres 16.10）

| 查的词 | 无索引 | 有索引 |
|---|---|---|
| 命中 1 行的词 | 1233 ms（seq scan） | **0.45 ms** |
| 命中 8 行的词 | 1233 ms | **0.55 ms** |
| 命中 12.5% 行的词 | 1233 ms（seq scan） | 1316 ms（**照样 seq scan**） |

第三行不是失败，是**计划器做对了**：命中率这么高时索引不划算，它就该被忽略。
也就是说这几条索引要么用上、要么不碍事，没有「反而更慢」的那一档。

建索引的代价也量过：200000 行上一条 GIN trigram 索引 **2.3 秒**建完；四条在
78 MB 的表上共占约 30 MB。今天的 `feedback` 表远小于此，所以这几条索引现在的
意思是**别再退化**，不是为了今天提速 —— 数字写在这里，是为了以后有人问「为什么
有四条 GIN 索引」时不必重测一遍。

## 中文为什么也能用

trigram 是按字符切的，不是按词。`show_trgm('搜索')` 出 3 个三元组，
`show_trgm('搜')` 出 1 个 —— 所以**两字的中文词照常走索引**，而这是中文里最短的
常用查询长度。这一点是选它而不是选 BM25 的原因：BM25（镜像里预载的 `pg_search`）
要先分词，分词之后「正文中段的半句话」就不再匹配了，而反馈的搜索框恰恰大量收到
这种东西。

## 扩展

`CREATE EXTENSION IF NOT EXISTS pg_trgm`。这一条是**本仓第一次建扩展**（此前全仓
零处 `CREATE EXTENSION`，见 `docs/topics/反馈功能后端设计-方案稿.md`）。部署的镜像
是 `paradedb/paradedb:v0.18.8-pg16`，`pg_trgm 1.6` 在其中可用（实测
`pg_available_extensions`），且迁移用的角色是超级用户。`IF NOT EXISTS` 让重复执行
和「运维已经手动建过」两种情形都不炸。

## 锁

`CREATE INDEX` 不是 `CONCURRENTLY`（后者不能在事务里跑，而 alembic 默认在事务里）。
代价是一次 `ACCESS EXCLUSIVE` 锁，时长按上表约 2.3 秒/20 万行线性缩放。按今天这张
表的规模是毫秒级；真要在一张已经很大的表上补，就拆成单独一次运维动作。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e7a91b3c58"
down_revision: str | Sequence[str] | None = "d3f0a91c7b45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 搜索真正问到的四列，与 `repositories.matching()` 一一对应。写成一份列表而不是
#: 抄四遍：这四条索引存在的唯一理由是那个函数会问这四列，两边漂开之后没有人会收到
#: 任何提示，只会某一天发现某个词突然变慢。
_SEARCHED = ("title", "summary", "problem", "author_handle")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for column in _SEARCHED:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_feedback_{column}_trgm "
            f"ON feedback USING gin ({column} gin_trgm_ops)"
        )


def downgrade() -> None:
    for column in reversed(_SEARCHED):
        op.execute(f"DROP INDEX IF EXISTS ix_feedback_{column}_trgm")
    # 扩展**不**删：它可能被这一支迁移之外的东西用上，而删掉一个别人还在用的扩展
    # 是静默地把别人的索引变成错误的。留着一个没人用的扩展是零成本。
