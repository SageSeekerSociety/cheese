# 后端热点接口体检：用户等着的那 5 个请求，钱花在哪

回答一个问题：侧边栏和时间线背后的 5 个接口，每一个到底花在哪、有没有能省的。
全部数字都是在一个**造出来的真实规模库**上实测的，不是读代码推断的。

> **诊断已经落地成改动**。本文前半部分是诊断（改动前的测量），
> **改完之后的复测见文末「改完之后」一节**。诊断那部分保持原样没有回填，
> 因为它记录的是「当时看到的是什么」——把它改成现在的样子，
> 下一个人就再也看不出这几条是怎么被发现的了。

## 结论速览

| 接口 | SQL 条数 | p50 | 其中 SQL | 响应(原始/网上) | 判决 |
|---|---|---|---|---|---|
| `GET /topics?project_id=…` | **10**，无 N+1 | 42.3 ms | 10.9 ms | 119.8 KB / **10.1 KB** | 小省：`_last_activity()` 被算了两遍（3.3 ms） |
| `GET /projects/{id}/topic-unread` | **4**，无 N+1 | **217.6 ms** | **207.8 ms** | 5.3 KB / 3.0 KB | **大省：一条索引换掉全表扫，p50 217.6 → 39.8 ms** |
| `GET /projects/{id}/private-unread` | **4**，无 N+1 | 18.7 ms | 9.3 ms | 0.2 KB | **没得省**，已经很干净 |
| `GET /topics/{id}/blocks?limit=50` | **9**，无 N+1 | 23.4 ms | 7.7 ms | 51.0 KB / **6.0 KB** | 中省：删掉没人读的 `total`，省掉 38% 的 SQL 时间 |
| `GET /projects/{id}/members` | **35**，**有 N+1** | 32.8 ms | 12.7 ms | 6.5 KB / 1.1 KB | **中省：30 条重复查询 = 10.3 ms，占端到端 31%** |

一句话：**这一轮只有一个真正的性能问题（`topic-unread` 全表扫），一个真正的 N+1（`members`），
其余两个"直觉上很重"的地方（响应体积、中间件）实测都不是问题。**

## 路径更正

简报里写的 `GET /projects/{id}/topics` 这个路径**不存在**。话题列表是
`GET /topics?project_id=<uuid>`（`<&backend/app/api/routes/topics.py>` 第 257–258 行，
`router = APIRouter(prefix="/topics")`，`project_id` 是 query 参数），前端也是这么调的
（`<&frontend/src/api.ts>` 第 531 行）。下文一律用真实路径。
`topic-unread` / `private-unread` 确实挂在 `/projects/{id}/…` 下，因为它们用了另一个
router（同文件第 2184 行，注释解释了为什么必须分开）。

---

## 测量环境（可复现）

不是在小库上测的——小库上什么都快，测了等于没测。

```
PostgreSQL 17.10（独立容器，shared_buffers=256MB），alembic upgrade head 建的库
10 个 project（平台是多租户的，一个库里住着很多项目）
2,230 个 topic，其中目标项目 220 个房间 + 30 个私聊
990,386 条 block，blocks 表 625 MB
目标项目的"热房间"：2,500 条 block（跑过很多轮 agent 的那种）
30 个 project member，viewer 对一半房间有读游标、另一半没有
```

**为什么必须是多租户的**：第一版只造了一个 project（108k blocks），`topic-raw` 的
seq scan 只要 45 ms，看着还行。把库扩到 10 个租户后同一条 SQL 变成 234 ms——
**代价随整个平台的数据量长，不随这个项目长**。单项目库会把这个结论完全盖掉。

后端跑的是真 uvicorn + 真 HTTP，不是 TestClient。

### SQL 条数是怎么数出来的

不是读代码推断的。挂了一个 SQLAlchemy `after_cursor_execute` 事件监听器，
把每条语句和它的耗时记进一个 contextvar 里的 list；外面套一层**纯 ASGI**
（不是 `BaseHTTPMiddleware`，免得自己给自己加一层 stream hop）的包装，
每个请求结束时把 `{path, ms, bytes, n_sql, sql[]}` 落成一行 JSONL。
包装模块放在 `/tmp/perfapp.py`，`import app.main:app` 后再加监听器——**仓库代码一行没动**。

每个接口预热 8 轮后取 40 个样本。

---

## 1. `GET /topics?project_id=…`（进项目 + 每 30 秒轮询）

**1. 打了几条 SQL：10 条，没有 N+1。**

数出来的，每条的均耗时：

```
 #1   0.70 ms  SELECT "user" …                     ← 认证：解析调用者
 #2   0.36 ms  SELECT agent_bindings …             ← 认证：是不是分身
 #3   0.38 ms  SELECT project_members …            ← 授权：能不能进这个项目
 #4   3.60 ms  SELECT topics … ORDER BY coalesce((SELECT max(blocks.created_at) …))
 #5   0.58 ms  SELECT count(*) FROM topics …       ← page 的 total
 #6   3.28 ms  SELECT topics.id, coalesce((SELECT max(blocks.created_at) …))
 #7   0.50 ms  SELECT topic_memberships.topic_id … WHERE topic_id IN (220 个)
 #8   0.50 ms  SELECT accept_cards … WHERE topic_id IN (220 个)
 #9   0.51 ms  SELECT alerts … WHERE topic_id IN (220 个)
 #10  0.63 ms  SELECT accept_cards … （live cards）
```

`relevance_for_topics`（`<&backend/app/domain/topic/services.py>` 第 412 行）承诺的
"三条查询，不管批量多大"是真的——#7/#8/#9 就是那三条，220 个话题一次问完。
`_live_room_cards`（`<&backend/app/api/routes/topics.py>` 第 201 行）也是批的。
**这个接口的批量化做得很到位。**

**2. 执行计划：走索引，没有 seq scan。**

派生字段 `last_activity_at` 根本不是 `topics` 表上的列（`\d topics` 里没有），
是每次现算的相关子查询（`<&backend/app/domain/topic/repositories.py>` 第 25 行 `_last_activity()`）。
所以"`topics` 表按 `last_activity_at` 排序有没有索引"这个问题的答案是：**不需要，也没法建**——
Postgres 把这个子查询改写成了对 `ix_blocks_topic_id_created_at` 的 `Index Only Scan Backward … LIMIT 1`：

```
 Sort  (cost=187.75..188.36 rows=247 width=78) (actual time=4.059..4.077 rows=220 loops=1)
   Sort Key: (COALESCE((SubPlan 2), topics.created_at)) DESC
   Buffers: shared hit=672
   ->  Bitmap Heap Scan on topics  (actual time=0.144..3.917 rows=220 loops=1)
         Recheck Cond: (project_id = '1111…'::uuid)
         Filter: (is_private IS FALSE)
         ->  Bitmap Index Scan on ix_topics_project_id  (actual time=0.057..0.058 rows=250)
         SubPlan 2
           ->  Result  (actual time=0.016..0.016 rows=1 loops=220)
                 InitPlan 1
                   ->  Limit  (actual time=0.016..0.016 rows=1 loops=220)
                         ->  Index Only Scan Backward using ix_blocks_topic_id_created_at on blocks
```

每个话题 0.016 ms，220 个话题 3.5 ms。`(topic_id, created_at)` 这条索引在这里**正好用对了**。

**3. 响应体积：原始 119.8 KB（220 个房间，558 B/房间），网上 10.1 KB。**

字段占比（原始字节）：`presentation` 11.1%、`project_id` 9.3%、`id` 7.9%、
`last_activity_at` 7.5%、`created_at`+`updated_at` 13.0%、`title` 5.8%。
每行有 8 个字段在**所有** 220 行里都是 `null`/`false`
（`upgraded_from_block_id` / `agent_instance_id` / `accepted_by` / `accepted_at` /
`archived_at` / `parent_id` / `awaits_me` / `running`），合计 27.7 KB = 23.1%。

**但这 23% 在网上等于零**：gzip 之后 9.0 KB vs 8.9 KB，只差 0.5%。见下面"响应体积那一节"。

**4. 省得掉吗：能省 3.3 ms，别的没得省。**

`_last_activity()` 这个相关子查询算了**两遍**：#4 里用来排序，#6 里用来报给前端
（`list_for_project` 排序 + `last_activity_for_topics` 取值，
`<&backend/app/domain/topic/repositories.py>` 第 101 / 125 行）。
#6 独立的 3.28 ms 是纯重复劳动——同一批 220 个话题、同一个子查询。
把它 select 进 #4 一起返回就省掉，代价是 `list_for_project` 的返回类型不再是
`list[Topic]`，而第 125 行的 docstring 明说这是故意的取舍。3.3 ms / 42.3 ms = 7.8%。

**剩下的 31.4 ms 是 Python，不是数据库**（42.3 ms 端到端 − 10.9 ms SQL）：220 次
`TopicOut.model_validate` + `model_dump(mode="json")` + `presentation.room_presentation()`。
这才是这个接口的大头，但它是真在干活，不是浪费。

---

## 2. `GET /projects/{id}/topic-unread`（每 30 秒轮询）—— 唯一的真问题

**1. 打了几条 SQL：4 条，没有 N+1。** 简报里"不是 N+1"这条核实无误。

```
 #1   0.71 ms  SELECT "user" …          ← 认证
 #2   0.48 ms  SELECT agent_bindings …  ← 认证
 #3   1.46 ms  SELECT projects …        ← 授权
 #4 207.80 ms  SELECT blocks.topic_id, count(*) …   ← 就是它
```

端到端 p50 **217.6 ms**，其中 207.8 ms 是那一条 group-by。

**2. 执行计划：`Parallel Seq Scan on blocks`——整张 990k 行的表，每 30 秒一次。**

`EXPLAIN (ANALYZE, BUFFERS)` 原文（缓存已预热）：

```
 Finalize GroupAggregate  (cost=71826.40..72451.62 rows=2200 width=24) (actual time=219.073..233.657 rows=125 loops=1)
   Group Key: blocks.topic_id
   Buffers: shared hit=22570 read=41096
   ->  Gather Merge  (cost=71826.40..72407.62 rows=4400 width=24) (actual time=219.061..233.569 rows=303 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=22570 read=41096
         ->  Partial GroupAggregate  (cost=70826.37..70899.73 rows=2200 width=24) (actual time=212.518..214.044 rows=101 loops=3)
               Group Key: blocks.topic_id
               Buffers: shared hit=22570 read=41096
               ->  Sort  (cost=70826.37..70843.49 rows=6848 width=16) (actual time=212.493..213.146 rows=8112 loops=3)
                     Sort Key: blocks.topic_id
                     Sort Method: quicksort  Memory: 193kB
                     Buffers: shared hit=22570 read=41096
                     ->  Hash Left Join  (cost=66.80..70390.10 rows=6848 width=16) (actual time=0.670..210.532 rows=8112 loops=3)
                           Hash Cond: (blocks.topic_id = topic_read_states.topic_id)
                           Filter: ((topic_read_states.last_read_at IS NULL) OR (blocks.created_at > topic_read_states.last_read_at))
                           Rows Removed by Filter: 7697
                           Buffers: shared hit=22556 read=41096
                           ->  Hash Join  (cost=61.68..70330.93 rows=20542 width=24) (actual time=0.325..206.506 rows=15809 loops=3)
                                 Hash Cond: (blocks.topic_id = topics.id)
                                 Buffers: shared hit=22550 read=41096
                                 ->  Parallel Seq Scan on blocks  (cost=0.00..69781.49 rows=185464 width=24) (actual time=0.038..186.220 rows=146750 loops=3)
                                       Filter: ((task_id IS NULL) AND ((author)::text <> 'alice'::text) AND ((kind)::text = 'message'::text))
                                       Rows Removed by Filter: 183379
                                       Buffers: shared hit=22502 read=41096
                                 ->  Hash  (cost=58.59..58.59 rows=247 width=16) (actual time=0.230..0.232 rows=250 loops=3)
                                       Buckets: 1024  Batches: 1  Memory Usage: 20kB
                                       ->  Bitmap Heap Scan on topics  (cost=6.22..58.59 rows=247 width=16) (actual time=0.079..0.171 rows=250 loops=3)
                                             Recheck Cond: (project_id = '11111111-1111-1111-1111-111111111111'::uuid)
                                             Filter: ((is_private IS FALSE) OR ((private_owner)::text = 'alice'::text) OR ((private_peer)::text = 'alice'::text))
                                             Heap Blocks: exact=6
                                             ->  Bitmap Index Scan on ix_topics_project_id  (cost=0.00..6.16 rows=250 width=0) (actual time=0.054..0.054 rows=250)
                           ->  Hash  (cost=3.56..3.56 rows=125 width=24) (actual time=0.087..0.088 rows=125 loops=3)
                                 ->  Seq Scan on topic_read_states  (cost=0.00..3.56 rows=125 width=24) (actual time=0.013..0.052 rows=125 loops=3)
                                       Filter: ((user_handle)::text = 'alice'::text)
 Planning:
   Buffers: shared hit=467
 Planning Time: 2.561 ms
 Execution Time: 234.022 ms
```

关键三行：

- `Parallel Seq Scan on blocks`，`rows=146750 loops=3` = **440,250 行**被逐行过滤，
  为了得出 **125 个数字**。
- `Buffers: shared hit=22570 read=41096` = **63,666 个 8KB buffer ≈ 500 MB** 的缓冲区流量，
  其中 41,096 个是从磁盘读的（表 625 MB > shared_buffers 256 MB）。
- 唯一有选择性的谓词 `project_id` 只作用在 `topics` 上；`blocks` 那边只有
  `kind`/`task_id`/`author` 三个低选择性条件，**没有任何一条能走索引**，
  所以计划器只能全表扫，然后 hash join 回 topics 去筛。

**代价随平台总数据量增长，不随这个项目增长。** 单项目库 108k blocks 时它只要 45 ms；
扩到 10 个租户 990k blocks 就变成 234 ms。每多一个租户，所有人的侧边栏都慢一点。

**没有读游标的房间会 count 全部历史**——简报的怀疑成立，`Rows Removed by Filter: 7697`
就是被读游标挡掉的那部分，剩下 8,112 行是真数进去的，其中大头来自那 125 个没游标的房间。
但这**不是**主要成本：主要成本是为了找到这 8,112 行而扫过的 44 万行。

**3. 响应体积：5.3 KB 原始 / 3.0 KB gzip。** 125 个 `{"uuid": 数字}`。没有夹带。

**4. 省得掉吗：能省 176 ms，一条索引的事。**

实测（在同一个库上 `CREATE INDEX CONCURRENTLY`，测完已 `DROP`）：

```sql
CREATE INDEX ix_probe_unread ON blocks (topic_id, kind, task_id, created_at) INCLUDE (author);
```

同一条 SQL 的新计划：

```
 HashAggregate  (cost=5016.54..5038.67 rows=2213 width=24) (actual time=50.534..50.584 rows=125 loops=1)
   Group Key: blocks.topic_id
   Buffers: shared hit=1295
   ->  Hash Left Join  (cost=11.77..4933.86 rows=16537 width=16) (actual time=19.026..44.013 rows=24336 loops=1)
         Hash Cond: (blocks.topic_id = topic_read_states.topic_id)
         Filter: ((topic_read_states.last_read_at IS NULL) OR (blocks.created_at > topic_read_states.last_read_at))
         Rows Removed by Filter: 23091
         ->  Nested Loop  (cost=6.64..4798.21 rows=49610 width=24) (actual time=0.161..32.126 rows=47427 loops=1)
               ->  Bitmap Heap Scan on topics  (actual time=0.085..0.305 rows=250 loops=1)
                     Recheck Cond: (project_id = '11111111-1111-1111-1111-111111111111'::uuid)
                     ->  Bitmap Index Scan on ix_topics_project_id  (actual time=0.070..0.071 rows=250 loops=1)
               ->  Index Only Scan using ix_probe_unread on blocks  (cost=0.42..17.17 rows=202 width=24) (actual time=0.026..0.096 rows=190 loops=250)
                     Index Cond: ((topic_id = topics.id) AND (kind = 'message'::text) AND (task_id IS NULL))
                     Filter: ((author)::text <> 'alice'::text)
                     Rows Removed by Filter: 5
                     Heap Fetches: 0
                     Buffers: shared hit=1285
 Planning Time: 2.619 ms
 Execution Time: 43.402 ms
```

| | 基线 | 加索引后 | 变化 |
|---|---|---|---|
| SQL 执行时间 | 234.0 ms | 43.4 ms | **−81%** |
| shared buffers | 63,666（41,096 读盘） | **1,295**（`Heap Fetches: 0`） | **−98%** |
| 扫描方式 | Parallel Seq Scan 全表 | Index **Only** Scan，只碰这 250 个房间 | |
| **接口 p50** | **217.6 ms** | **39.8 ms** | **−82%** |

`Heap Fetches: 0` 是这条索引的关键——`INCLUDE (author)` 让 `author != me` 这个过滤
也在索引里做完，一次堆访问都不用。

另一个更便宜的半吊子方案（**不建索引**，只在 where 里补一个 `Block.project_id == project_id`，
让计划器能用现成的 `ix_blocks_project_id`）实测 **156.8 ms**，7,026 buffers。
比基线好，但远不如索引；而且它把"项目 id"这个冗余事实写进了查询语义。

> 边界提示（诊断，不是本轮要动的）：这个 `topic-unread` 每 30 秒被每个开着的标签页调一次。
> 索引本身是纯收益，但轮询频率本身也值得单独算一笔账。

---

## 3. `GET /projects/{id}/private-unread`（每 30 秒轮询）

**1. 打了几条 SQL：4 条**（3 条认证/授权 + 1 条 group-by），没有 N+1。

**2. 执行计划：走索引，没有 seq scan——和上面那条同源同表，结局却完全不同。**

```
 GroupAggregate  (cost=74.29..1761.22 rows=1 width=37) (actual time=0.456..9.925 rows=15 loops=1)
   Group Key: topics.id
   Buffers: shared hit=934
   ->  Nested Loop  (cost=74.29..1760.87 rows=67 width=29) (actual time=0.355..9.685 rows=1791 loops=1)
         Join Filter: ((topic_read_states.last_read_at IS NULL) OR (blocks.created_at > topic_read_states.last_read_at))
         Rows Removed by Join Filter: 1989
         ->  Merge Left Join  (cost=66.46..67.10 rows=1 width=37) (actual time=0.280..0.333 rows=30 loops=1)
               Merge Cond: (topics.id = topic_read_states.topic_id)
               ->  Sort  (actual time=0.183..0.190 rows=30 loops=1)
                     ->  Bitmap Heap Scan on topics  (actual time=0.122..0.140 rows=30 loops=1)
                           Recheck Cond: (project_id = '11111111-1111-1111-1111-111111111111'::uuid)
                           Filter: ((is_private IS TRUE) AND (((private_owner)::text = 'alice'::text) OR ((private_peer)::text = 'alice'::text)))
                           Rows Removed by Filter: 220
                           ->  Bitmap Index Scan on ix_topics_project_id  (actual time=0.048..0.048 rows=250 loops=1)
               ->  Sort  (actual time=0.094..0.106 rows=113 loops=1)
                     ->  Seq Scan on topic_read_states  (actual time=0.006..0.042 rows=125 loops=1)
                           Filter: ((user_handle)::text = 'alice'::text)
         ->  Bitmap Heap Scan on blocks  (cost=7.84..1691.25 rows=202 width=24) (actual time=0.030..0.294 rows=126 loops=30)
               Recheck Cond: (topics.id = topic_id)
               Filter: ((task_id IS NULL) AND ((author)::text <> 'alice'::text) AND ((kind)::text = 'message'::text))
               Rows Removed by Filter: 205
               Heap Blocks: exact=834
               ->  Bitmap Index Scan on ix_blocks_topic_id  (actual time=0.019..0.019 rows=331 loops=30)
                     Index Cond: (topic_id = topics.id)
 Planning Time: 2.312 ms
 Execution Time: 10.132 ms
```

**为什么它没被 seq scan 掉，而 `topic-unread` 被了**：`is_private IS TRUE` +
`private_owner/peer = me` 把 topics 先收敛到 **30 行**，计划器于是敢用 nested loop
从 topics 钻进 `ix_blocks_topic_id`（`loops=30`）。`topic-unread` 那边保留了 220 个
房间，计划器算下来 nested loop 不划算，就翻成了全表扫。**同一段代码逻辑，选择性差 7 倍，
计划天差地别**——这也是为什么必须实测而不是读代码。

**3. 响应体积：226 B。** 15 个 `{"handle": 数字}`。

**4. 省得掉吗：没得省，已经很干净了。**

18.7 ms 里 9.3 ms 是 SQL（其中 2.6 ms 是认证/授权那三条），9.4 ms 是 Python + 框架。
它读了每个私聊 331 条 block 才筛出 126 条（`Rows Removed by Filter: 205`），
理论上上面那条索引也能覆盖它——但 10 ms 的绝对值不值得为它单独做什么。
如果 `topic-unread` 的索引建了，这条顺带也会变快，白捡。

---

## 4. `GET /topics/{id}/blocks?limit=50`（每次打开一个话题）

在那个 2,500 条 block 的房间上测的。

**1. 打了几条 SQL：9 条，没有 N+1。**

```
 #1   0.76 ms  SELECT topics …            ← place_or_404
 #2   0.38 ms  SELECT work_trees …        ← place_or_404
 #3   0.39 ms  SELECT "user" …            ← 认证
 #4   0.36 ms  SELECT agent_bindings …    ← 认证
 #5   0.37 ms  SELECT topic_memberships … ← 授权：在不在这个房间
 #6   0.38 ms  SELECT project_members …   ← 授权：在不在这个项目
 #7   1.40 ms  SELECT blocks … ORDER BY created_at DESC, id DESC LIMIT 51   ← 真正的分页
 #8   2.94 ms  SELECT count(*) FROM blocks WHERE topic_id = …               ← 全量 count
 #9   0.90 ms  SELECT block_reactions … WHERE block_id IN (50 个)           ← 批量，不是 N+1
```

简报说的两条都核实无误：`count_for_topic` 是全量 count（#8），
`reactions_for_blocks` 已经是批的（#9，50 个 id 一次问完）。

**2. 执行计划：分页查询近乎完美，全量 count 是唯一随历史增长的部分。**

分页查询（`<&backend/app/domain/block/repositories.py>` 第 347 行 `page_for_topic`）：

```
 Limit  (cost=5.25..210.32 rows=51 width=814) (actual time=0.195..0.250 rows=51 loops=1)
   Buffers: shared hit=18
   ->  Incremental Sort  (cost=5.25..8775.18 rows=2181 width=814) (actual time=0.194..0.243 rows=51 loops=1)
         Sort Key: created_at DESC, id DESC
         Presorted Key: created_at
         Full-sort Groups: 2  Sort Method: quicksort  Average Memory: 48kB  Peak Memory: 48kB
         Buffers: shared hit=18
         ->  Index Scan Backward using ix_blocks_topic_id_created_at on blocks  (cost=0.42..8690.16 rows=2181 width=814) (actual time=0.041..0.107 rows=52 loops=1)
               Index Cond: (topic_id = 'd423ef78-4d5d-420b-b3c6-d2f3729d2ecf'::uuid)
               Filter: ((task_id IS NULL) AND ((kind)::text <> ALL ('{doc_node,comment,doc}'::text[])))
               Buffers: shared hit=9
 Planning Time: 1.387 ms
 Execution Time: 0.314 ms
```

**0.31 ms，18 个 buffer，只读了 52 行**。`(topic_id, created_at)` 这条索引给了
`Presorted Key: created_at`，所以 `ORDER BY created_at DESC, id DESC` 走的是
`Incremental Sort` 而不是全排序——**只对同一毫秒的那一小撮做排序**。
这是这次体检里最漂亮的一条 SQL，2,500 条历史和 50 条历史对它是一样的。

全量 count（`<&backend/app/domain/block/repositories.py>` 第 396 行）：

```
 Aggregate  (cost=7468.43..7468.44 rows=1 width=8) (actual time=3.078..3.080 rows=1 loops=1)
   Buffers: shared hit=168
   ->  Bitmap Heap Scan on blocks  (cost=29.33..7462.98 rows=2181 width=0) (actual time=0.179..2.855 rows=2501 loops=1)
         Recheck Cond: (topic_id = 'd423ef78-4d5d-420b-b3c6-d2f3729d2ecf'::uuid)
         Filter: ((task_id IS NULL) AND ((kind)::text <> ALL ('{doc_node,comment,doc}'::text[])))
         Heap Blocks: exact=164
         Buffers: shared hit=168
         ->  Bitmap Index Scan on ix_blocks_topic_id  (cost=0.00..28.78 rows=2181 width=0) (actual time=0.124..0.125 rows=2501 loops=1)
               Index Cond: (topic_id = 'd423ef78-4d5d-420b-b3c6-d2f3729d2ecf'::uuid)
               Buffers: shared hit=4
 Planning Time: 1.361 ms
 Execution Time: 3.134 ms
```

**它必须回堆**（`Heap Blocks: exact=164`），因为 `kind` 和 `task_id` 不在
`ix_blocks_topic_id` 里。2,500 条 block 要 168 个 buffer，**和话题历史成正比**：
一个跑了 20,000 条 block 的房间，这一条要 ~25 ms，而分页查询还是 0.3 ms。

**3. 响应体积：原始 51.0 KB（50 条，1,044 B/条），网上 6.0 KB。**

字段占比：

```
   meta          11,293 B  21.6%
   content       10,179 B  19.5%
   reactions      4,091 B   7.8%
   topic_id       2,500 B   4.8%   ← 50 行里全是同一个 uuid
   author         2,254 B   4.3%
   id             2,200 B   4.2%
   turn_id        2,042 B   3.9%
   created_at     1,807 B   3.5%
   ── 以下 9 个字段在 50 行里全是 null，合计 8.3 KB = 16.3% ──
   upgraded_to_topic_id 1,400 B / upgraded_to_task_id 1,350 B / struct_parent 1,050 B
   struct_order 1,000 B / anchor_quote 1,000 B / node_type 850 B / mime_type 850 B
   reply_to 800 B / refs 500 B
```

一个真实观察：这一页 50 条里 28 条是 `kind=event`（工具调用），它们的 `content`
（1,764 B）和 `meta`（10,949 B）**装的是同一件事**——schema 自己写着
"`content` 是 baked-text fallback"（`<&backend/app/domain/block/schemas.py>` 第 57 行）。
两份都发。

**跑 agent 跑了很多轮的房间，50 条 block 的 JSON 是 51 KB；整个话题不分页是 2.0 MB**
（实测 `GET /topics/{hot}/blocks` 无 limit：2,068,060 B，p50 196 ms）。
分页把它砍成了 1/40——这个设计是对的。

**4. 省得掉吗：能省 38% 的 SQL 时间，删一行代码。**

`total = await repo.count_for_topic(topic_id)`（`<&backend/app/api/routes/topics.py>` 第 386 行）
是 2.94 ms，占这个接口 SQL 总时间（7.7 ms）的 **38%**，而且是唯一随话题历史线性增长的部分。

**这个 `total` 前端一个字都没读。** 消费方只有 `<&frontend/src/lib/blockCache.ts>`
第 68–70 行，它取的是 `payload.data` / `payload.has_more`；`BlockPage` 类型
（`<&frontend/src/api.ts>` 第 997–1000 行）在 `ListPayload` 之外只声明了
`has_more` 和 `oldest_id`。全仓库 grep 不到任何读 blocks 页 `total` 的地方。
翻页靠的是 `has_more` + `oldest_id` 游标，压根不需要总数。

唯一的绊脚石：`<&backend/tests/integration/test_block_paging.py>` 第 53/71/113/140 行
断言了 `payload["total"]`。删字段要连着改这 4 处断言。

体积上：删掉那 9 个恒为 null 的字段能省 16.3% 的原始字节，但 **gzip 后只省 2.9%**
（5.7 KB → 5.5 KB）。不值得。

---

## 5. `GET /projects/{id}/members`（进项目）—— 唯一的 N+1

**1. 打了几条 SQL：35 条。有 N+1，30 条是同一条查询换个参数。**

数出来的（40 次请求的均值）：

```
    0.80 ms  x 1   SELECT projects …                              ← 项目行
    0.55 ms  x 1   SELECT project_members …                       ← 花名册
    0.41 ms  x 1   SELECT count(*) FROM project_members …         ← page total
    0.93 ms  x 1   SELECT project_members … LEFT JOIN user_profile LEFT JOIN avatar   ← 头像/昵称，已经是批的
   10.33 ms  x30   SELECT "user".id, "user".username, … WHERE username = $1   ← ★ N+1
    0.39 ms  x 1   SELECT agent_bindings.user_id WHERE user_id IN (…)         ← 批的
```

罪证只有一行（`<&backend/app/api/routes/members.py>` 第 68 行）：

```python
rows = {m.user_handle: await users.get_by_handle(m.user_handle) for m in members}
```

一个 `await` 在字典推导里，每个成员一次往返。**30 人 = 30 条 SQL = 10.33 ms**。
有意思的是它上面第 61 行的 `ProjectRepository.list_members` 已经批量拿过一次同样的花名册
（那条 0.93 ms 的 LEFT JOIN），第 68 行是为了拿 `User.id` 再走一遍。

**2. 执行计划：每条都走 `ix_users_handle` 唯一索引，0.32 ms 一条。**

问题不在计划，在**次数**——30 次往返，每次 0.32 ms 里绝大部分是 asyncpg 的
round-trip 而不是查询本身。这是 N+1 的典型形态：每一条都"很快"，加起来是大头。

**3. 响应体积：6.5 KB 原始（30 人，222 B/人）/ 1.1 KB gzip。**

`project_id` 占 23.5%（30 行同一个 uuid）、`id`（成员行的 uuid）19.8%、
`created_at` 16.2%。真正有用的 `user_handle` + `name` + `role` + `avatar_id` 加起来 32.5%。
量太小，不值得动。

**4. 省得掉吗：能省 10.3 ms，占端到端 31%。**

把第 68 行换成一次 `WHERE handle IN (…)`（这行本来就只要 `User.id` 去喂
`agent_bindings.agent_user_ids`），35 条 SQL 变 6 条，10.33 ms 变 ~0.4 ms。
32.8 ms 的 p50 掉到 ~23 ms。

一个放大因子：**这个接口不要求认证**（`list_members` 第 47 行没有 `ActorResolverDep`，
无 Authorization header 实测返回 200 + 6,652 B；文件头注释写着"Reading the roster stays
open, as it was"，是有意为之）。这不是漏洞，但意味着任何人都能用一个请求让数据库跑 30 次往返。
项目人数越多倍数越大。

---

## 中间件那一节：怀疑不成立，但这是有价值的答案

`<&backend/app/main.py>` 上叠了 3 层 `@app.middleware("http")`
（`request_context` 第 359 行、`cheese_token_gate` 第 470 行、
`report_unhandled_to_room` 第 510 行）加一层纯 ASGI 的
`ResponseIntegrityAudit`（第 548 行）。前三层生成的都是 Starlette 的
`BaseHTTPMiddleware`，每层确实会把响应体塞进一个 anyio memory stream 转一手。

### 怎么测的

同一进程、同一个库、同一批请求，只改一个变量：在 `/tmp` 的包装模块里按类型
把这 4 层从 `app.user_middleware` 里摘掉（`BaseHTTPMiddleware` + `ResponseIntegrityAudit`），
CORS 等其余层保留。摘掉时打印确认：

```
PERF: dropped middleware: ['ResponseIntegrityAudit', 'BaseHTTPMiddleware', 'BaseHTTPMiddleware', 'BaseHTTPMiddleware']
```

计时用的是最外层那个**纯 ASGI**包装自己的 `perf_counter`，所以两组的观测点一模一样，
观测本身不引入 stream hop。每组预热后取 40 个样本；为了排除漂移，"有中间件"跑了两遍
（重启后重测，见最右列，差异 < 3%）。

### 数字

| 接口 | 响应大小 | 有中间件 p50 | 摘掉后 p50 | **差** | 占比 | 有中间件(复测) |
|---|---|---|---|---|---|---|
| `/health` | 87 B | **2.525 ms** | **1.880 ms** | **0.645 ms** | 25.6% | — |
| `/projects/{id}/private-unread` | 0.2 KB | 18.69 | 17.17 | 1.52 ms | 8.1% | 19.90 |
| `/topics/{id}/blocks?limit=50` | 51 KB | 23.40 | 21.48 | **1.92 ms** | 8.2% | 24.33 |
| `/projects/{id}/members` | 6.5 KB | 32.76 | 32.13 | 0.63 ms | 1.9% | 33.86 |
| `/topics?project_id=…` | 120 KB | 42.29 | 41.55 | 0.74 ms | 1.7% | 43.24 |
| `/topics/{id}/blocks`（不分页） | **2.0 MB** | 196.21 | 189.53 | **6.68 ms** | 3.4% | 196.86 |
| `/projects/{id}/topic-unread` | 5.3 KB | 217.62 | 215.19 | 2.43 ms | 1.1% | 237.35 |

`/health` 那一行是最干净的隔离（几乎没有业务逻辑，只剩框架）：
**4 层加起来 0.645 ms，平均每层约 0.16 ms**。

### 结论

**怀疑推翻了，但推翻得有用。** 拆开看是两项：

- **固定成本 ≈ 0.6–1.9 ms/请求**，与响应大小无关（4 层各自的 task/stream 建立）。
- **随体积的成本 ≈ 3.3 µs/KB**：2 MB 的响应多花 6.68 ms − 0.6 ms 固定 ≈ 6.1 ms，
  6.1 ms ÷ 2020 KB ≈ 3.0 µs/KB。anyio memory stream 那一手确实存在，
  但它是纯内存搬运，**几百 KB 量级下完全淹没在噪声里**（120 KB 的 `/topics` 只差 0.74 ms）。

对照一下这次体检的其他数字：`topic-unread` 一条 SQL 就 207.8 ms，
`members` 的 N+1 就 10.3 ms，`/topics` 的 Python 序列化就 31.4 ms。
**中间件那 0.6–2 ms 排不进前三，摘掉它换来的是丢掉 request-id 关联、
统一的耗时日志和响应截断告警——不划算。**

一个值得记一笔的尾部现象：`/topics` 的 p95 有中间件时是 352.3 ms、摘掉后是 45.4 ms
（p50 两边都是 42 ms）。这是 40 个样本里的一两个离群点，不足以下结论，
但如果以后要追"偶尔卡一下"，`BaseHTTPMiddleware` 的 task group 是值得回头看的地方。

---

## 响应体积那一节：gzip 已经把这件事解决了

三个接口的原始体积看着不小（120 KB / 51 KB / 6.5 KB），而且里面确实有一堆
恒为 null 的字段。但**这些字节根本没上网**。

用仓库自己的 `<&frontend/nginx.conf>`（`gzip on; gzip_comp_level 6; gzip_min_length 1024;`
第 29–41 行，`gzip_types` 含 `application/json`），把 `__API_UPSTREAM__` 指向被测后端，
起一个 nginx 实测：

| 接口 | 后端产出 | 浏览器收到 | 压缩比 |
|---|---|---|---|
| `/api/topics?project_id=…` | 122,709 B | **10,116 B** | 12.1× |
| `/api/topics/{id}/blocks?limit=50` | 52,188 B | **6,036 B** | 8.6× |
| `/api/projects/{id}/topic-unread` | 5,385 B | 3,021 B | 1.8× |
| `/api/projects/{id}/members` | 6,652 B | 1,146 B | 5.8× |

也在跑着的生产栈上验证了同一条路径确实开着压缩（`GET /api/openapi.json` 通过
`cheese-frontend-1` 的 :8080 回来带 `Content-Encoding: gzip`）。

所以 "`exclude_none` 能省多少" 的实测答案是：

| | 原始 | 去掉 null 字段 | gzip 原始 | gzip 去 null |
|---|---|---|---|---|
| `/topics` | 119.8 KB | 92.1 KB (−23.1%) | 9.0 KB | 8.9 KB (**−0.5%**) |
| `blocks?limit=50` | 51.0 KB | 42.5 KB (−16.6%) | 5.7 KB | 5.5 KB (**−2.9%**) |
| `members` | 6.5 KB | 6.0 KB (−7.7%) | 1.1 KB | 1.1 KB (**−2.0%**) |

**在体积上没得省。** 重复的 `"field": null` 压缩率极高，gzip 已经把它们吃干净了。
真正的成本不在字节，在 CPU：`/topics` 42.3 ms 里 31.4 ms 是 Python 序列化 220 个对象，
`blocks?limit=50` 23.4 ms 里 15.7 ms 是 Python。要省得从对象数量下手，不是从字段数量。

**一个真的坑（条件性的）**：`frontend/nginx.conf` 没有设 `gzip_proxied`，nginx 的默认值是
`off` —— 只要请求带了 `Via` 头（即前面还有一层代理），**压缩整个关掉**。实测：

```
$ curl -H 'Accept-Encoding: gzip' …/api/topics?…            → 10,116 B, Content-Encoding: gzip
$ curl -H 'Accept-Encoding: gzip' -H 'Via: 1.1 apisix' …    → 122,709 B, 无 Content-Encoding
```

目前的生产链路（浏览器 → frontend-nginx → api-front → backend）里没有谁加 `Via`，
所以现在是压着的（上面已实测）。但这是个悬崖式的开关：**哪天前面加一层加 `Via` 的网关，
所有 API 响应会静默地从 10 KB 变回 120 KB，没有任何告警。** 加一行
`gzip_proxied any;` 就锁死了。

---

## 索引静态核对

从活库 `pg_indexes` 读的（`alembic upgrade head` 之后的真实结果）。

**`blocks`（990,386 行，625 MB）**

```
blocks_pkey                     UNIQUE (id)
ix_blocks_project_id            (project_id)
ix_blocks_reply_to              (reply_to)
ix_blocks_struct_parent         (struct_parent)
ix_blocks_task_id_created_at    (task_id, created_at) WHERE task_id IS NOT NULL
ix_blocks_topic_id              (topic_id)
ix_blocks_topic_id_created_at   (topic_id, created_at)
ix_blocks_turn_id               (turn_id)
```

- `(topic_id, created_at)`：**有**。分页查询靠它，0.31 ms，`Presorted Key` 免了全排序。这条索引很值。
- `(topic_id, kind, task_id)`：**没有**。这正是 `topic-unread` 全表扫的原因。
- `ix_blocks_topic_id` 是 `ix_blocks_topic_id_created_at` 的**前缀冗余**——
  凡是能用前者的查询，后者都能用。实测 `count_for_topic` 选了前者（更窄、更少页），
  所以它不是纯浪费，但它是 990k 行上一条可以质疑的写入成本。

**`topics`（2,230 行）**

```
topics_pkey                 UNIQUE (id)
ix_topics_agent_instance_id (agent_instance_id)
ix_topics_parent_id         (parent_id)
ix_topics_project_id        (project_id)
```

**没有 `last_activity_at` 这一列**，所以也不存在"按它排序有没有索引"的问题。
排序键是现算的相关子查询，Postgres 把它改写成对 `ix_blocks_topic_id_created_at` 的
`Index Only Scan Backward … LIMIT 1`——已经是最优形态。

**`topic_read_states`（125 行）/ `project_members` / `users`**

```
uq_topic_read_user              UNIQUE (topic_id, user_handle)
ix_topic_read_states_user_handle (user_handle)
uq_project_member               UNIQUE (project_id, user_handle)
ix_users_handle                 UNIQUE (handle)
```

够用。`topic_read_states` 在两个 unread 查询里都被 seq scan
（125 行，2–6 个 buffer），表太小，计划器不用索引是对的。

---

## 附：核实简报里的"已核实事实"

| 简报说的 | 核实结果 |
|---|---|
| `unread_counts` 不是 N+1，是单条带 `outerjoin` 的 group-by（约 231 行） | ✅ 行号精确命中（第 231 行），实测 4 条 SQL |
| 没有读游标的话题会 count 全部历史 | ✅ 成立（`Rows Removed by Filter: 7697` 是被游标挡掉的），但**不是主要成本**，主要成本是为了找到它们扫过的 44 万行 |
| 索引够不够没验证 | ✅ 不够，实测 `Parallel Seq Scan`，见上 |
| `list_topic_blocks` 在 topics.py:332 | ✅ 装饰器在 332，`async def` 在 333 |
| 它额外跑一个 `count_for_topic`（全表 count） | ✅ 实测 2.94 ms，且没人读 |
| `reactions_for_blocks` 是批查询不是 N+1 | ✅ 实测一条 `IN (50 个 id)` |
| 分页是 cursor 式，页大小前端固定 50 | ✅ `PAGE_SIZE = 50`（`<&frontend/src/lib/blockPaging.ts>` 第 16 行） |
| 前端每 30 秒轮询 unread + topics | ✅ `<&frontend/src/views/workspace/ProjectShell.vue>` 第 42–45 行 `setInterval(…, 30_000)` |
| 后端已经在给每个请求打耗时日志 | ✅ `request_context` 的 `ms=`（`<&backend/app/main.py>` 第 394 行） |
| 本地跑 backend 要先装 rustup | ✅ 照做通过，`srp_rs` 编好 |
| `GET /projects/{id}/topics` | ❌ 该路径不存在，实际是 `GET /topics?project_id=` |

## 附：诊断这一轮没有改任何代码

所有实验（SQL 监听器、摘中间件、建索引）都在 `/tmp` 的包装模块和一个一次性的
Docker Postgres 里做的，仓库工作区除本文件外零改动；探针索引 `ix_probe_unread`
测完已 `DROP`。**改动是下一轮做的，见下。**

---

# 改完之后

同一套测法、同一个库（10 项目 / 2230 话题 / 990,376 blocks / 624 MB），
改动前后各 40 个样本。

## 五个接口，前后对照

| 接口 | SQL 前→后 | p50 前→后 | SQL 时间 前→后 | 响应体积 |
|---|---|---|---|---|
| `/projects/{id}/topic-unread` | 4 → 4 | 244.1 → **40.3** ms（**−83%**） | 232.7 → 29.6 ms | 5.3 KB（一字节没变） |
| `/projects/{id}/members` | **35 → 6** | 35.8 → **12.9** ms（**−64%**） | 14.3 → 3.2 ms | 6.5 KB（一字节没变） |
| `/projects/{id}/private-unread` | 4 → 4 | 21.0 → **14.3** ms（**−32%**） | 10.2 → 4.4 ms | 0.2 KB（一字节没变） |
| `/topics/{id}/blocks?limit=50` | 9 → 9 | 27.1 → **23.9** ms（−12%） | 8.7 → 7.1 ms | 43.6 KB（一字节没变） |
| `/topics?project_id=…` | 10 → **9** | 46.4 → **41.0** ms（−11%） | 12.4 → 10.1 ms | 119.8 KB（一字节没变） |
| `/topics/{id}/blocks`（不分页，agent 读全量） | 9 → 9 | 205.5 → 199.2 ms（−3%） | 43.4 → 40.2 ms | 2.0 MB（一字节没变） |

**响应体积一字节没变**——这几条改的全是「怎么拿到同样的答案」，不是「答案是什么」。

## `topic-unread` 改后的 EXPLAIN (ANALYZE, BUFFERS)

```
 HashAggregate  (cost=6676.74..6698.85 rows=2211 width=24) (actual time=47.390..47.439 rows=125 loops=1)
   Group Key: blocks.topic_id
   ->  Hash Left Join
         Hash Cond: (blocks.topic_id = topic_read_states.topic_id)
         Filter: ((topic_read_states.last_read_at IS NULL) OR (blocks.created_at > topic_read_states.last_read_at))
         ->  Nested Loop
               ->  Bitmap Heap Scan on topics
                     Recheck Cond: (project_id = '1111…'::uuid)
                     ->  Bitmap Index Scan on ix_topics_project_id
               ->  Index Only Scan using ix_blocks_topic_kind_task_created on blocks
                     Index Cond: ((topic_id = topics.id) AND (kind = 'message'::text) AND (task_id IS NULL))
                     Filter: ((author)::text <> 'alice'::text)
                     Heap Fetches: 0
         ->  Seq Scan on topic_read_states  (rows=125 loops=1)
 Execution Time: 47.696 ms
```

对照改动前的 `Parallel Seq Scan on blocks` + `Buffers: shared hit=22570 read=41096`
+ `Execution Time: 234.022 ms`：

| | 前 | 后 |
|---|---|---|
| 扫描方式 | Parallel Seq Scan（全表 990k 行） | Index **Only** Scan，只碰这 250 个房间 |
| 回堆 | 每行都回 | **`Heap Fetches: 0`** |
| buffers | 63,666（41,096 读盘） | **1,295** |
| 执行时间 | 234.0 ms | **43–48 ms** |

## `_last_activity()` 合并成一趟：确认没有变成「一条查询里算两遍」

把派生列 select 出来的同时还要按它排序，值得担心的是 Postgres 会不会算两遍。
实测没有——计划里**只有一个 `SubPlan`**，`loops=220`（每个话题一次），
排序键直接引用同一个 SubPlan：

```
 Sort  (actual time=4.569..4.588 rows=220 loops=1)
   Sort Key: (COALESCE((SubPlan 2), topics.created_at)) DESC
   ->  Bitmap Heap Scan on topics  (actual time=0.258..4.440 rows=220 loops=1)
         ->  Bitmap Index Scan on ix_topics_project_id
         SubPlan 2
           ->  Result  (actual time=0.018..0.018 rows=1 loops=220)
                 InitPlan 1
                   ->  Limit  (actual time=0.018..0.018 rows=1 loops=220)
                         ->  Index Only Scan Backward using ix_blocks_topic_id_created_at on blocks
```

合并后一趟 4.59 ms；合并前是两趟 3.60 + 3.28 = 6.88 ms。

## 写入代价（加索引必须付的账）

单行 INSERT，服务端 `clock_timestamp()` 计时，每组 3 轮：

| 场景 | 前 p50 | 后 p50 | 变化 |
|---|---|---|---|
| **同话题连插**（缓存友好） | 0.108 ms | 0.110 ms | 测不出差别，组内波动比组间大 |
| **随机话题插**（碰冷叶页，更接近真实） | 0.160 ms | **0.186 ms** | **+0.026 ms（+16%）** |
| 随机话题 mean | 0.207 ms | 0.272 ms | +0.065 ms |
| 随机话题 p99 | 0.665 ms | 0.889 ms | +0.224 ms（仍是亚毫秒） |

磁盘：新索引 94 MB，被替换掉的 `ix_blocks_topic_id` 只有 7 MB
（PG13+ 的 btree 去重把重复 topic_id 压得极扁），**净增约 87 MB**，
blocks 表的 +14%。索引条数持平（8 → 8）。

换算：一次 `topic-unread` 轮询省下的时间 ≈ 7,800 次 block 插入的增量。

## 删掉 `ix_blocks_topic_id` 的两项核对

新的复合索引以 `topic_id` 打头，所以它是旧单列索引的超集。但「理论上覆盖得住」
和「计划器真的会用」是两件事，所以两项都实测了。

**其一，另外 4 个接口的计划有没有退化**——没有，还白捡两个改进：

| 查询 | 改后走的路 | 结论 |
|---|---|---|
| 话题列表按最后活动排序 | `Index Only Scan Backward using ix_blocks_topic_id_created_at` | 不变 |
| `blocks?limit=50` 分页 | `Index Scan Backward using ix_blocks_topic_id_created_at`，18 buffers | 不变 |
| `count_for_topic` | `Index Only Scan`，`Heap Fetches: 0`，168 → 34 buffers | **3.13 → 1.38 ms** |
| `private_unread_counts` | `Index Only Scan`，`Heap Fetches: 0` | **原来是 Bitmap Heap Scan** |
| 单纯按 topic_id 查 blocks | `Bitmap Index Scan on ix_blocks_topic_id_created_at` | 仍走索引，**没退化成 Seq Scan** |

**其二，级联删除**——这是最容易因为「少一条索引」从毫秒掉到分钟的地方，
平时没人测。删一个 2500 blocks + 300 reactions 的话题：

| | round1 | round2 | round3 | 中位 |
|---|---|---|---|---|
| 前（有 `ix_blocks_topic_id`） | 1126.9 | 1154.3 | 1249.2 | 1154.3 ms |
| 后（已删） | 1146.3 | 1117.9 | 1134.6 | **1134.6 ms** |

**没有退化**，差异在噪声内。级联走的是 `ix_blocks_topic_id_created_at`，
它一直都在，且同样以 `topic_id` 打头。

## `total`：留着，一个字没改

上一轮说它「占 `blocks?limit=50` SQL 时间的 38%、而且没人读」，结论没错，
但**它指向的解法是错的**。

`page(items, total)` 是 `app/api/response.py` 里两行的共享 helper，
被 12 个路由文件、29 个调用点用着——它是全站响应信封的一部分。
（`docs/api-conventions.md` 里一个字都没提它，那份文件只管 URL 寻址；
判据是共享 helper 本身。）为 1.38 ms 让 `blocks` 变成唯一一个信封缺一块的接口，
不划算，后面每个人都得单独记住这个例外。

**而真正的解法根本不是删字段，是索引**：为 `topic-unread` 加的那条索引顺手覆盖了
`count_for_topic`，一行代码没改——

```
前: Bitmap Heap Scan, Buffers: shared hit=168,             Execution Time: 3.134 ms
后: Index Only Scan,  Heap Fetches: 0, Buffers: hit=17 read=17, Execution Time: 1.376 ms
```

**这条值得单独记一笔**：一个看起来像「删掉冗余字段」的问题，
底下其实是「缺索引」。按第一直觉动手，会破坏一个全站约定，
换来的收益还不如加索引——而且加索引连带把另外两个查询也修了。
**一个字段贵，不一定是这个字段该死，可能只是问它的方式不对。**

## 顺带发现、但没有动手的

按边界只诊断不动刀，记在这里：

- **`GET /projects/{id}/members` 不需要认证**（`members.py` 第 47 行没有
  `ActorResolverDep`，无 Authorization 头实测 200）。文件头注释写着
  "Reading the roster stays open, as it was"，是有意为之，所以不是漏洞。
  只是它意味着任何人都能触发这个接口——N+1 修掉之后，这件事的放大倍数
  从「人数 × 往返」降到了常数，顺带也算收窄了一点。
- **`ix_blocks_project_id`（6.6 MB）目前没有任何热点查询在用**。
  `unread_counts` 本来可以用它（给 where 补一个 `Block.project_id`，实测
  234 → 157 ms），但加了复合索引之后这条路不再需要。它是否还有别的读者
  没有查，**没动**。

## 本轮改了什么

| 文件 | 改动 |
|---|---|
| `backend/alembic/versions/b7e4d21c9a06_*.py` | 新索引 `(topic_id, kind, task_id, created_at) INCLUDE (author)`，并替换掉 `ix_blocks_topic_id`；upgrade/downgrade 都在真库上验过 |
| `backend/app/domain/block/models.py` | 模型与 migration 对齐（含去掉 `topic_id` 的 `index=True`） |
| `backend/app/api/routes/members.py` | `get_by_handle` 逐个查 → 已有的 `get_by_handles` 批查 |
| `backend/app/domain/topic/{repositories,services}.py`、`api/routes/topics.py` | `_last_activity()` 从算两遍改成排序那一趟顺带 select 出来 |
| `frontend/nginx.conf` | 补 `gzip_proxied any;`（补悬崖，不是修 bug——今天没人加 `Via`，压缩一直是开着的） |
| `backend/tests/integration/test_hot_path_queries.py` | 8 条测试，见下 |

## 测试

`tests/integration/test_hot_path_queries.py`，8 条全绿。它们盯的不是绝对速度
（那是 benchmark 的事），而是**形状**：

- **往返次数不能随列表长度增长**——花名册从 3 人涨到 15 人、话题从 3 个涨到 12 个，
  SQL 条数必须一模一样。这正是 N+1 唯一的可观测症状：答案一直是对的。
- **索引必须真的在收窄扫描**——EXPLAIN 里 `kind` 和 `task_id` 必须出现在
  `Index Cond`/`Recheck Cond` 里，而不是只在 `Filter` 里。
  改动前它们就在 `Filter` 里，意味着把整个话题的 block 都取上来再扔掉。
- **删掉单列索引之后，按 topic_id 查仍然走索引**（级联删除依赖这条路）。
- 外加行为不变的功能断言：花名册的 `name`/`agent`/`avatar_id`、
  每一行都有 `last_activity_at`、在房间里说话会把它顶到列表最前面。

关于 `Heap Fetches: 0`：它是这条索引最值钱的性质，但**测试里没有断言它**。
index-only scan 要求 visibility map 是新的，那需要一次 `VACUUM`，
而事务里的测试跑不了 `VACUUM`。断言它会变成一条看运气的测试。
所以测试断言的是稳定的那半（索引在收窄），`Heap Fetches: 0` 由上面的
EXPLAIN 原文佐证。
