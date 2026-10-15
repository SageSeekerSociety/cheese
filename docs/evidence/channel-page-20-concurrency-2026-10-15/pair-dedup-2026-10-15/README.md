# 第二轮改动（去掉重复取数）的原始输出

这是 2026-10-15 第二轮那四处改动的前后配对测量。四份 `pair-*.json` 是原始样本，
`run.txt` 是四次运行的完整 stdout，其余是各装置的原始输出。

跑出来的 `.log` 一律进不了仓库（根目录 `.gitignore` 第 14 行是 `*.log`），所以这里的
stdout 存的是 `run.txt`。

## 被测的两棵树

| | 树 | 说明 |
|---|---|---|
| before | `/tmp/base-tree` | `git archive 7867ee023 \| tar -x -C /tmp/base-tree`，只读导出，不改 `.git` |
| after | 任务工作目录 | 四个源文件已改（见 PR） |

两棵树只差那四个源文件，测量脚本、数据库、种子数据完全相同。

## 重跑

测试库：`127.0.0.1:5433/cheese_perf`（本机自建，见 `.claude/scripts/dev-db.sh`）。

```bash
W=<任务工作目录>
export PERF_DB_URL=postgresql+asyncpg://…@127.0.0.1:5433/cheese_perf
export PERF_REDIS_URL=redis://127.0.0.1:6379/0
```

### 1. 20 并发配对（`pair-*.json`、`run.txt`）

```bash
export PERF_LEG=asgi PERF_CONCURRENCY=20 PERF_BATCHES=5
export PERF_ONLY="channel detail,awaiting"
cd <树> && PERF_OUT=<输出>.json \
  uv run --project "$W/backend" python .claude/scripts/concurrent_read_probe.py
```

四轮按 before / after / before / after 交替跑，同样的库、同样的房间。
每轮开头的 `loadavg` 记在 `run.txt` 里：96.2 / 92.0 / 73.6 / 67.1。
`pair-b*` 是 before，`pair-a*` 是 after。

### 2. 每条请求发了哪些 SQL（`sql-census-{before,after}.txt`）

```bash
export PERF_LEG=asgi PERF_CONCURRENCY=1 PERF_ROUNDS=5
export PERF_ONLY="channel detail,awaiting"
cd <树> && uv run --project "$W/backend" python .claude/scripts/sql_census.py
```

`sql_census.py` 按「动词 + 碰到的表」把语句归成形状：表头的 `statements/req` 是每请求
**执行**了多少条，下面列的是耗时最高的八种形状，各自的 `calls/req` 加起来就是它。它不认
`PERF_BY_CALLER`（那是 `sql_callers.py` 的开关），也不打印 distinct——「29 条不同」要看
第 5 节那两份。

### 3. 每条语句读回多少行（`sql-rowcounts-{before,after}.txt`）

```bash
export PERF_LEG=asgi PERF_CONCURRENCY=1 PERF_ROUNDS=5 PERF_ROWCOUNTS=1
export PERF_ONLY="awaiting"
cd <树> && uv run --project "$W/backend" python .claude/scripts/sql_census.py
```

### 4. 响应体有没有变（`response-bodies.sha256.txt`）

```bash
cd <树> && uv run --project "$W/backend" \
  python scripts/perf-awaiting-me/dump_endpoint_bodies.py /tmp/bodies-<树>
```

把九个端点的响应体规范化后写成文件，再逐字节比对两棵树。`response-bodies.sha256.txt`
是逐个文件的两列哈希对照。

## 结论

改动是实的（频道详情每请求 34 → 32 条语句；待我处理的房间行 144 → 87），
但在这个负载下测不出 CPU 的差别。四轮的单请求循环 CPU，取每轮的最小值是
35.5/36.1（before）与 41.2/33.3（after），取每轮的中位数是 38.1/38.8 与 45.7/34.0
（`pair-*.json` 里的 `asgi["channel detail"]["single"][*]["cpu"]`，探针自己的汇总用的是
中位数）。两种取法下，同一棵树两轮之间的差（6 ms 和 12 ms）都大于这次该省下的约
1.4 ms——结论一样，但报的是哪个统计量得说清楚。确定性证据是语句数和行数。

### 5. 每条语句由哪一行代码发出（`sql-callers-channel-detail-{before,after}.txt`）

```bash
export PERF_LEG=asgi PERF_CONCURRENCY=1 PERF_ONLY="channel detail,awaiting"
cd <树> && uv run --project "$W/backend" python .claude/scripts/sql_callers.py
```

`sql_callers.py` 把 `AsyncSession.execute/scalars/get/flush/stream` 包起来，记下
最内层的 `backend/app` 帧，再从 `before_cursor_execute` 读回来（SQLAlchemy 异步版把
同步会话跑在 greenlet 里，直接在监听器里取栈是取不到我们自己的帧的）。

表头那行就是结论：before 树「34 statements, 29 distinct」，after 树「32 statements,
29 distinct」——两边不同的都是 29 个问题，去掉的那两条正是同一张表问三遍。
`…-before-2.txt` 是同一条命令在 before 树上的另一次运行（表头相同，语句次序不同），
留着做交叉核对。加 `PERF_BY_CALLER=1` 时表头换成「by call site, N sites」，那是另一种
口径；这批读数没有用到它。

### 6. 单请求循环 CPU 的分段去向（`burst-cpu-split-channel-detail.txt`）

```bash
PERF_ONLY="channel detail,awaiting" PERF_ROUNDS=7 \
  uv run --project "$W/backend" python .claude/scripts/split_topic_cpu.py
```

### 7. worker 数（`worker-scaling-run1.json`、`worker-scaling-run2.json`）

```bash
PERF_OUT=<输出>.json \
  uv run --project "$W/backend" python .claude/scripts/worker_scaling_probe.py
```

两次运行都留了原始样本，报告里那四个数按每批的最坏值取：run1 是 2014 / 7779 ms，
run2 是 1648 / 12182 ms（前者 1 个 worker，后者 2 个）。CPU 是所有 worker 进程加起来
的（worker 由 multiprocessing 起，按 `--port` 找命令行是找不到的）；run1 里 2 worker
那一列的 CPU 读成 0，是那一次的快照没走到 worker 进程，所以那一份只用它的 wall。
这份读数只用来支持「多进程是明确排在外面的」这个判断，不是一条候选路线。
