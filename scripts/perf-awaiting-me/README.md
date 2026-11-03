# 待我处理耗时分段

量一次 `/awaiting-me` 在事件循环上占多少 CPU，以及这些时间花在哪几段：取数、
逐条判定、组装 DTO、序列化。

指标是 `time.process_time`，不是墙钟。后端是一个进程、一个事件循环，20 个并发
请求在那里串行排队，所以要压的是「一次请求让循环忙多久」——墙钟里等数据库的那
部分不占循环，别的请求可以填进来。宿主机被别的租户占满时墙钟会翻几倍，CPU 不会。

## 怎么跑

1. 起本地 Postgres：

       pg_ctl -D <.../cheesex-dev-db/pg17> \
           -o "-p 5433 -h 127.0.0.1 -k /tmp -c shared_preload_libraries=pg_search" start

2. 建库、上迁移：

       PGPASSWORD=cheesex createdb -h 127.0.0.1 -p 5433 -U cheesex cheese_perf
       cd backend && DATABASE_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf \
           uv run alembic upgrade head

   alembic 认 `DATABASE_URL`；`TEST_PG_BASE` 只有测试用例读，给它上迁移会跑到
   别的库上。库名换过就用 `PERF_DATABASE_URL`（造数）和 `PERF_DB_URL`（计时）
   指过去。

3. 按 dev 行数造数（13 个项目、1917 条活、48 万条消息块、1572 张验收卡）：

       uv run --project backend python scripts/perf-awaiting-me/seed_awaiting_me.py

   它是 asyncpg 直插，先 `TRUNCATE` 再灌，只碰 `cheese_perf`。`seed_awaiting_me.py`
   内置 `assert_safe_perf_dsn` 双重校验（解析连接串 + 连接后核对 `current_database()`），
   强制要求主机为本机回环（`127.0.0.1` / `localhost` / `::1`）且库名包含 `perf` 或
   `test`，防止误指业务库导致清表。所有内容都归 `perfviewer`，一屏摆 72 行。
   `conversations` 那几行不用自己写——`topics`、`tasks`、`threads` 上的 BEFORE INSERT
   触发器会登记。

   主项目里另有 18 个**边界房间**：6 个空房间、6 个归档房（各带一道指着
   `perfviewer` 的题）、6 个「芝士问完自己又接了一句」的房间（#2046）。三类都看得
   见、都不该摆上清单，所以 66 这个数不变 —— 反过来把归档房改成 `active`、或者删掉
   那 6 句接话，行数各多 6 行（2026-10-14 实测 66 → 72）。改取数的改动要能和它们
   对上账，见下面第 6 步。

4. 出分段表：

       uv run --project backend python scripts/perf-awaiting-me/measure_awaiting_me.py

   每个取数入口和每个逐条判定函数都套了一层 `process_time` 计时，所以它还能报出
   单独某条查询占多少。末尾附一份 `cProfile`，落在 `/tmp/awaiting-me.prof`。
   注意 `cProfile` 的 `tottime` 里 `select.epoll.poll` 那一行是等数据库的墙钟，
   不是 CPU，别当成热点。

5. 四个探针：

       uv run --project backend python scripts/perf-awaiting-me/probe_brief.py
       uv run --project backend python scripts/perf-awaiting-me/probe_concurrency.py
       uv run --project backend python scripts/perf-awaiting-me/probe_query_split.py
       uv run --project backend python scripts/perf-awaiting-me/probe_slow_endpoints.py

   `probe_brief.py` 量跨项目扫活那条：同一份查询换 1／7／13 个项目的 id 各跑一次，
   再跑一次只取一列的同样谓词。用来分清它的代价是跟着项目数走还是跟着行数走。
   `probe_query_split.py` 按语句拆「取数」那一段：SQLAlchemy 的
   `after_cursor_execute` 给出驱动的往返，和叶子自己的 `process_time` 相减就是
   我们把行变成对象的钱；每条语句还报它取回多少行 —— 代价跟着行数走时，缩结果比
   缩计划有用。`probe_concurrency.py` 量 20 并发一批的墙钟和循环 CPU——就是那个
   「4.1 秒」的场景。`probe_slow_endpoints.py` 把同一个问题问遍一次频道页要打的
   九个接口：每请求各占多少循环 CPU。采样轮数用 `PERF_ROUNDS` 调（`probe_brief.py`、
   `probe_query_split.py`、`probe_slow_endpoints.py`），批数和并发数用
   `PERF_BATCHES`、`PERF_CONCURRENCY` 调（`probe_concurrency.py`）——没有哪个探针
   读全部三个。

6. 改前改后各导一份响应，整份对一遍（改的是取数，答的得是同一份）：

       cd <改前那棵树> && python scripts/perf-awaiting-me/dump_body.py --out /tmp/before.json
       cd <改后那棵树> && python scripts/perf-awaiting-me/dump_body.py --out /tmp/after.json
       diff /tmp/before.json /tmp/after.json

   它跑的是自己所在那棵树的代码，所以两棵树各跑一次、比这两份文件。差别只应该
   出现在时间戳相同的两条之间——那两条的先后不保证。

   频道页那九个接口是另一种导法，一次一个目录、逐字段比：

       PERF_DB_URL=... python scripts/perf-awaiting-me/dump_endpoint_bodies.py /tmp/before
       PERF_DB_URL=... python scripts/perf-awaiting-me/dump_endpoint_bodies.py /tmp/after
       diff -r /tmp/before /tmp/after

   它把每个响应当成 JSON 比，`last_output_at` 这类由时钟决定、不是行上的事实的字段
   先剔掉。

## 读数的坑

- `per_round` 里每段的中位数和下面的入口明细要对得上。明细是按「每个入口自己的
  中位数」算的，别把所有入口的样本混在一起取中位数——没被那个入口碰到的那几轮
  会补零，中位数直接腰斩。
- 引擎要用生产那套连接池。`poolclass=NullPool` 会把每条连接的握手和鉴权摊到每个
  入口头上，量出来的不是请求的代价。
