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

## 量过的数（200000 行、随机中文正文，本机 Postgres 16.10）

索引帮不帮得上忙，由**词的长度**决定，不是由命中率决定。下表每一行都是同一条
`ILIKE '%词%'`，只换词的宽度：

| 词的宽度 | 命中行数 | 计划器实际选的 | 强制走索引 |
|---|---|---|---|
| 一个汉字 | 1953 | 顺扫 11.9 ms | —— |
| 两个汉字 | 2 | 顺扫 293 ms | 扫回 **200000** 行、recheck 掉 199998，**1205 ms** |
| 三个汉字 | 1 | **索引扫 0.195 ms** | 同 |

**两个汉字那一行是重点：索引不是用不上，是用了更糟。** pg_trgm 要从词里切出
完整的三元组才敢用它，两个汉字切不出来，于是它退化成「扫完整条索引、把每一行
都 recheck 一遍」，比顺扫慢约四倍。计划器知道这件事（真跑起来它选顺扫，上表
最后一列必须关掉 `enable_seqscan` 才看得见），代价是**短于三个汉字的词在这条
路上拿不到索引的好处**。这不是退回 BM25 的理由，但它划出了索引的作用边界。

建索引的代价也量过：200000 行上一条 GIN trigram 索引 **2.3 秒**建完；四条在
78 MB 的表上共占约 30 MB。部署环境那张 `feedback` 表**实测 4 行**，所以这几条
索引现在的意思是**别再退化**，不是为了今天提速 —— 数字写在这里，是为了以后有人
问「为什么有四条 GIN 索引」时不必重测一遍。

## 为什么还是选它而不是 BM25

trigram 按字符切、不按词，所以中文不会被分词挡住；BM25（镜像里预载的
`pg_search`）要先分词，分词之后「正文中段的半句话」就不再匹配了，而反馈的搜索框
恰恰大量收到这种东西。上表说的那条边界（三个汉字起效）是它的代价。

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
