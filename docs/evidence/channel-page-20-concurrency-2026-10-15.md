# 频道页九个接口，20 并发下各花在哪，2026-10-15

只测量，没有改产品代码。目标指标「20 并发下最慢操作 < 1 秒」**没达到**：九条里三条
的最慢单请求过了 1 秒（项目任务列表、频道详情、待我处理），其余六条在 1 秒以内。

全部数字是在一个造出来的真实规模库上实测的：13 个项目、1917 条活、48 万个消息块
（`EXPLAIN` 里整表扫掉 479,832 行）。**这台机器全程 load 86–160（4 核）**，
所以绝对值偏大，只能读同一轮里的相对构成和排序，不要拿它当生产容量。

## 结论表

同一进程内 ASGI 与真 uvicorn + 20 条保活 HTTP 连接两路，各 7 批，取中位数。
`CPU` 是事件循环的进程时间；`CPU/墙钟` 是循环被占住的比例，剩下的
`墙钟 − CPU` 才是循环能拿去等 I/O 的空闲。

| # | 接口 | 墙钟 ms | CPU ms | CPU 占比 | 最慢单请求 ms | DB/请求 ms | SQL 条/请求 | 判决 |
|---|---|---|---|---|---|---|---|---|
| 1 | 项目任务列表 | 2751–3125 | 2450–2742 | **88%** | 2743 | 35 | 11 | **可控：别全量加载（要定语义）** |
| 2 | 频道详情 | 2245–2439 | 884–1220 | 39–50% | 2434 | **158** | **37** | **可控：一条查询** |
| 3 | 待我处理（参照） | 1043–1281 | 830–915 | 72–80% | 1031 | 20 | 12 | 可控：两处 |
| 4 | 频道 blocks | 558–632 | 430–490 | 77–78% | 624 | 11 | 14 | 待挖：不是 SQL，原因未定 |
| 5 | 频道 task list | 605–619 | 490–502 | 81% | 598 | 12 | 16 | 可控：响应可以缩小 |
| 6 | 频道 members | 490–545 | 390–396 | 72–81% | 537 | 4 | 12 | 可控：响应可以缩小 |
| 7 | 频道 threads | 391–554 | 263–380 | 67–69% | 545 | 3 | 8 | 可控：响应可以缩小 |
| 8 | 频道 environment | 430–447 | 303–330 | 70–74% | 438 | 5 | 14 | 可控：响应可以缩小 |
| 9 | 频道 pins | 367–541 | 240–370 | 65–68% | 527 | 3 | 8 | 可控：响应可以缩小 |

「可控」当且仅当能指出一条具体的省法。两路排名一致，数字在小范围内互相印证。

## 前五名逐条

### 1. 项目任务列表：2.75–3.12 s，88% 是循环 CPU

**它最贵的地方不是数据库，是构造 ORM 对象。**

`cProfile` 在同样 20 并发、3 批共 60 个请求下数得很清楚（`profile-burst.txt`）：
`101,160` 次 SQLAlchemy 实体装配（`_populate_full`，即每请求 1686 次）、
`90,420` 次 JSON 列解码（`_json_decoder`，每请求 1507 次）。这两笔正好对应下面两条查询：

| 语句 | 返回行数 | 说明 |
|---|---|---|
| `SELECT tasks … WHERE project_id = $1` | **1437** | 整个项目的活，一条不漏 |
| `SELECT topics … WHERE project_id IN (…)` | **219** | 整个项目的频道 |
| `SELECT tasks.id, (SELECT max(blocks.created_at) …)` | 82 | 这一页的活 |
| `SELECT accept_cards … WHERE task_id = ANY(…)` | 28 | 这一页的卡 |

请求 `/projects/{id}/tasks?status=open` 只摆 82 条活（响应 86.7 KB），
却在行过滤之前把 **1437 行活和 219 个频道**装配成了 ORM 对象：
1437 + 219 = 1656 行，对得上每请求那 1686 次装配。行数复跑两次完全一致，不是抖动。

那 1437 行也占掉 `sql_census.txt` 里项目任务列表 35 ms/请求 DB 时间中的
**23.0 ms**（`SELECT tasks`，1 次/请求），因为它搬回了 1437 行，
而这个表 46 列里绝大多数这一页用不上。装配加 JSON 解码占 `cProfile` 总时间的
**47%**（`_populate_full` 16.0% + `_instance` 14.2% + `_json_decoder` 9.1%
+ 它底下的 `json.decode` 7.8%），**这几乎全是那 1437 行白搬出来的。**

- 可控性：**能省，但省在哪要先定行为**。页大小是 82，读却是全项目——要么把
  `status` 过滤下推到 SQL（现在看起来是 Python 侧过滤），要么把这一页的 id 先定死、
  只按 id 取活、只取要用的列。前者改语义要确认，后者是纯优化。
  这是九条里最大的一笔，也是唯一要产品判断的一条。
- 另外一条独立的开销：`SELECT blocks,tasks` 2 次/请求共 4.7 ms/请求，是那 82 行
  每行一个关联子查询取「最后一次发言」，`ix_blocks_conversation_created_at` 上
  反向读一行。这条已经是最优写法，不用动。

### 2. 频道详情：2.24–2.44 s，循环只占四到五成

**这是一条查询的问题，而且已经定位到具体那一条。**

`sql_census.txt`：频道详情 37 条 SQL、158 ms/请求 DB 时间，其中
**122.5 ms 是第一条** `SELECT blocks, threads, topic_read_states`，占 78%。
它是「谁回复了我、我还没处理」那盏黄灯的查询，每次打开一个频道都要算一遍；
但它的代价和房间大小无关——这个房间一共只有 51 个块。

`EXPLAIN (ANALYZE, BUFFERS)` 的原因很直白：

```
Nested Loop Left Join  (actual time=121.832..121.834 rows=0 loops=1)
  ->  Seq Scan on blocks  (cost=0.00..4078134.77 rows=1)
        Filter: (... (refs)::jsonb @> '["user:perfviewer"]'::jsonb OR EXISTS(SubPlan 1))
        Rows Removed by Filter: 479832
```

查询里「直接挂在这个频道下的块」和「挂在这个频道的支线下的块」用 `OR` 连着
（`Block.conversation_id IN (…rooms…) OR threads.room_id IN (…rooms…)`），
规划器没法把它拆成两次索引扫，只能整表扫 48 万行，再靠过滤条件逐行判断。
把 OR 去掉单独测同一条查询的另一半：

| 写法 | 执行时间 |
|---|---|
| `conversation_id IN (…)` 单独 | **0.178 ms**（`Bitmap Index Scan on ix_blocks_conversation_seq`） |
| 原样带 `OR threads.room_id IN (…)` | **70.245 ms** |

**差约 400 倍。** 该频道一共只有 51 个块。

- 可控性：**高，一条查询**。把它拆成「本频道的线」和「本频道支线」两次索引扫再合并，
  或者用 `UNION ALL`／把房间收窄成一份 id 清单。不需要新索引——`blocks` 上已经有
  `(conversation_id, *)` 的多个索引，问题是它们没被用上。
- 这块归 `_replied_to_me_statement`（`backend/app/domain/topic/repositories.py`），
  上游是 `TopicRepository.replied_to_me`。

### 3. 待我处理（参照）：1.04–1.28 s

数据库 19.7 ms/请求、12 条 SQL，最大一笔是 `SELECT blocks,tasks` 2 次/请求 7.3 ms。
循环 CPU 830–915 ms，其中约 0.27 ms 是序列化（响应 25–27 KB），其余在 ORM 装配。
**它是参照不是目标**，九条里排第三。

- 可控性：同 4–9 那六条一类（响应与装配），不是新的形状。

### 4. 频道 blocks：558–632 ms

响应 20 KB、51 行、14 条 SQL、DB 11 ms/请求。**它没有任何一条语句值得指出来**：
最大一笔是取那 51 行本身（3 ms/请求）。DB 不是问题，循环 CPU 占 77% 也不是问题
——它本来就是「读 51 个块再序列化」。**唯一可疑的是绝对值**：51 行数据不该花
0.6 秒。响应体积那笔账前一份热点报告已经算过，这里也没有新的发现。

- 可控性：**要再挖一层才知道**。它不像 1、2 有条明确的省法，也不像 5–9 那五条
  能靠缩小响应解决。**建议它是下一轮的第一站**，用同样两路、把 51 行换成 1 行、
  5 行做一组对照，看这 0.6 秒里有多少是「行数无关的固定开销」。

### 5–9. 其余五条（371–619 ms）

环境、成员、置顶、支线、频道任务列表。共同形状：**DB 时间可以忽略
（3–12 ms/请求），循环 CPU 占七到八成，而响应很小**（37 B 到 12 KB）。
成员和支线的响应只有 56 B 和 37 B。

这不是「算得慢」，是**每请求约 8–16 条 SQL 的固定成本**：中间件、鉴权、ORM 装配、
以及那些返回 0 行却照样要走的查询（`sql_census.txt` 里每条都有好几条
`rows=0` 的语句）。五条里最贵的是频道任务列表（605 ms），响应 12 KB，是这五条里
最大的（其中 7 行是 `accept_cards`）。

- 可控性：**能省一点，省法是缩小响应与减少固定往返**，不是修 bug。
  这五条都远在 1 秒以内，**不要先动它们**。

## 怎么重算

```bash
# 起库（端口 5533/6399，状态目录全在 /tmp 下，不碰别人的）
CHEESEX_DEV_DB_DIR=/tmp/perf-db-1151b7c9 \
CHEESEX_DEV_SERVER_HOME=/tmp/perf-db-1151b7c9-servers \
CHEESEX_DEV_TOOL_CACHE=/tmp/perf-db-1151b7c9-cache \
CHEESEX_DEV_PG_PORT=5533 CHEESEX_DEV_REDIS_PORT=6399 \
  bash .claude/scripts/dev-db.sh start
# 建库、迁移、播种见 scripts/perf-awaiting-me/README.md

export PERF_DB_URL="postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5533/cheese_perf"
export PERF_REDIS_URL="redis://127.0.0.1:6399/0" ENVIRONMENT=development

# 两路 20 并发
PERF_LEG=asgi PERF_BATCHES=7 PERF_ROUNDS=7 PERF_OUT=/tmp/leg1.json \
  uv run --project backend python .claude/scripts/concurrent_read_probe.py
PERF_LEG=http PERF_BATCHES=7 PERF_ROUNDS=3 PERF_HTTP_PORT=5621 PERF_OUT=/tmp/leg2.json \
  uv run --project backend python .claude/scripts/concurrent_read_probe.py

# 每条语句花了多少 / 每次请求几条
uv run --project backend python .claude/scripts/sql_census.py

# 20 并发下循环 CPU 的去向
PERF_ONLY="project task list" PERF_CONCURRENCY=20 PERF_BURSTS=3 \
  uv run --project backend python .claude/scripts/profile_burst.py
```

原始输出都在本目录同名子目录下：`leg1-asgi.json`、`leg2-http.json`、
`sql-census.txt`、`sql-rowcounts.txt`、`profile-burst.txt`。

## 这一轮踩过的坑（会影响别人复现）

1. **探针不要把服务的 stderr 接进没人读的管道**。应用启动时吐近 90 KB 的
   ONNX 线程告警，64 KB 管道一满，服务的消息循环就永久卡在写日志上——
   症状是 `/health` 能应答、之后第一个真请求永远等不到，
   报出来是 `uvicorn never answered /health`，看起来像启动失败，其实不是。
   现在写文件（`concurrent_read_probe.py` 里 `run_http`）。
2. **`--lifespan off`**。应用的启动钩子（建 agent 实例、turn intake、设备 hub）
   与读路径成本无关，但会让启动在负载下不确定，其中一个还会和任何指向同一个库的
   东西抢 `(project_id, handle)` 那行。读的突发本来就会减掉自己的 CPU 基线，
   关掉它不会让数字变好看。
3. **别用错误输出判服务死活**：服务起来了，第一次真请求伪死时错误输出里只有
   噪声。手工 `curl` 三个接口（0.33 s / 0.16 s / 0.09 s）比读日志快得多。
