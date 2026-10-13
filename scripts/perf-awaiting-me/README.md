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
       cd backend && TEST_PG_BASE=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433 \
           uv run alembic upgrade head

   库名换过就用 `PERF_DATABASE_URL`（造数）和 `PERF_DB_URL`（计时）指过去。

3. 按 dev 行数造数（13 个项目、1917 条活、48 万条消息块、1572 张验收卡）：

       uv run --project backend python scripts/perf-awaiting-me/seed_awaiting_me.py

   它是 asyncpg 直插，先 `TRUNCATE` 再灌，只碰 `cheese_perf`。所有内容都归
   `perfviewer`，一屏摆 66 行。`conversations` 那几行不用自己写——`topics`、
   `tasks`、`threads` 上的 BEFORE INSERT 触发器会登记。

4. 出分段表：

       uv run --project backend python scripts/perf-awaiting-me/measure_awaiting_me.py

   每个取数入口和每个逐条判定函数都套了一层 `process_time` 计时，所以它还能报出
   单独某条查询占多少。末尾附一份 `cProfile`，落在 `/tmp/awaiting-me.prof`。
   注意 `cProfile` 的 `tottime` 里 `select.epoll.poll` 那一行是等数据库的墙钟，
   不是 CPU，别当成热点。

5. 三个探针：

       uv run --project backend python scripts/perf-awaiting-me/probe_brief.py
       uv run --project backend python scripts/perf-awaiting-me/probe_concurrency.py
       uv run --project backend python scripts/perf-awaiting-me/probe_query_split.py

   `probe_brief.py` 量跨项目扫活那条：同一份查询换 1／7／13 个项目的 id 各跑一次，
   再跑一次只取一列的同样谓词。用来分清它的代价是跟着项目数走还是跟着行数走。
   `probe_query_split.py` 按语句拆「取数」那一段：SQLAlchemy 的
   `after_cursor_execute` 给出驱动的往返，和叶子自己的 `process_time` 相减就是
   我们把行变成对象的钱；每条语句还报它取回多少行 —— 代价跟着行数走时，缩结果比
   缩计划有用。`probe_concurrency.py` 量 20 并发一批的墙钟和循环 CPU——就是那个
   「4.1 秒」的场景。三个探针的 `PERF_ROUNDS`、`PERF_BATCHES` 都能用环境变量调。

## 读数的坑

- `per_round` 里每段的中位数和下面的入口明细要对得上。明细是按「每个入口自己的
  中位数」算的，别把所有入口的样本混在一起取中位数——没被那个入口碰到的那几轮
  会补零，中位数直接腰斩。
- 引擎要用生产那套连接池。`poolclass=NullPool` 会把每条连接的握手和鉴权摊到每个
  入口头上，量出来的不是请求的代价。
