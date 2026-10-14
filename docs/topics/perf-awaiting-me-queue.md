# 「待我处理」的 20 并发：排队的是那条事件循环

2026-10-14。只报告发现，没有改任何行为代码。

**一句话：** `GET /awaiting-me` 一个请求要让事件循环忙 ~35 ms，20 个请求就是 ~0.7 s 的循环
工作必须在**一个**核上做完。后端整个是一个 uvicorn 进程、一条事件循环（镜像 CMD 里没有
`--workers`，compose 的 backend 服务也没有覆盖），所以 20 条保活连接不是排在数据库、不是
排在锁、也不是排在连接池上，而是排在那一条循环上。同一批请求交给两个工作进程，墙钟直接
砍半——这一条把「串行点是循环」钉死了。

## 一、后端只有一个进程、一条循环

这不是推断。镜像的启动命令是（`backend/Dockerfile` 第 221 行）：

    CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081",
         "--timeout-graceful-shutdown", "10", "--no-access-log"]

没有 `--workers`，uvicorn 的默认就是 1。`deploy/compose/docker-compose.base.yml` 里 backend
服务（第 132 行起）**没有 `command:`**，所以它跑的就是上面这条；整个 compose 文件里唯一
明写工作进程数的是 `preview-connection`（第 107 行 `--workers 1`），而那是另一个进程。

`backend/app/core/loop_lag.py` 的模块说明第一句就是这个前提：「Everything the backend does
shares one event loop」。这个文件也是这件事的仪表：循环迟到了多久、要不要 py-spy 抓栈，都
写在它里面。

## 二、一个请求要花 ~35 ms 循环 CPU

在本机按 dev 行数造的库上（13 个项目、1917 条活、479,778 条消息块、1572 张验收卡，
`perfviewer` 名下一屏 66 行、响应 29,667 字节），单请求：

| 段 | CPU/请求 | 占比 |
|---|---:|---:|
| 取数 fetch | 27.41 ms | 78.1% |
| 组装 DTO assemble | 5.15 ms | 14.7% |
| 逐条判定 judgement | 1.98 ms | 5.7% |
| 序列化 serialize | 0.48 ms | 1.4% |
| 会话/连接 session | 0.06 ms | 0.2% |
| **合计** | **35.10 ms** | |

墙钟 48.5 ms，其中循环占 72%——剩下 28% 是等数据库，那部分循环是空的，别的请求可以填
进来。取数里最贵的四个叶子（都是每请求一次）：`blocks.questions` 7.26 ms、
`tasks.rows` 4.31 ms、`cards.listed` 3.22 ms、`topics.rows` 2.48 ms。

同一批请求并发起来量（每档 5 批，取中位）：

| 并发 | 墙钟 | 循环 CPU | 每请求 CPU | 循环占墙钟 |
|---:|---:|---:|---:|---:|
| 1 | 49.3 ms | 35.5 ms | 35.5 ms | 72.1% |
| 4 | 178.7 ms | 167.1 ms | 41.8 ms | **93.5%** |
| 8 | 475.7 ms | 321.5 ms | 40.2 ms | 67.6% |
| 20 | 1118.3 ms | 697.7 ms | 34.9 ms | 62.4% |

墙钟几乎是并发数乘单请求耗时（49 → 179 → 476 → 1118 ms），每请求成本基本不变。**4 个并发
的时候循环已经忙到 93.5%**——队列就是从这里开始的，离 20 还有很远。20 个并发时循环只占
墙钟 62%，是因为等数据库的那段被拉长了（本机宿主机 load ~82），但那 700 ms 的 CPU 是排队
的硬地板：它不能并发。

## 三、对照：把同一批交给两条循环

真 uvicorn、真 HTTP、20 条保活连接（一个进程内 20 个 ASGI 任务和 20 条连接在循环眼里是
同一串工作，这里用真连接再量一遍）：

| | 墙钟 | 最坏一个请求 | 每请求 CPU | 进程 CPU / 墙钟 |
|---|---:|---:|---:|---:|
| 1 个工作进程 | 1.11 / 1.16 / 1.25 / 1.71 s | 1.09–1.70 s | 40–70 ms | 63–82% |
| 2 个工作进程 | 0.64 / 0.66 / 0.66 / 0.68 / 0.88 s | 0.64–0.88 s | 38–44 ms | **100–132%** |

右边那一列是关键：一个进程时循环最多只能占满一个核（≤100%）；两个进程时**合计 CPU 超过
了墙钟**，意思是那两个核确实同时在干活。总工作量没变（每请求 CPU 还是 ~40 ms），墙钟却
减半——串行点因此可以确定是「循环的条数」。

顺带：这两个数正是 dev 上那个症状的本地复现——单进程时最坏请求 1.09–1.70 s，跟 dev 上
静默窗口 1.02–1.14 s 是同一个量级，而它由「20 × 每请求 CPU」直接算得出来。

## 四、顺带钉掉的两个猜测

**不是连接池。** backend 用的是默认 20（`db_pool_size`）+ 15（`db_max_overflow`）= 35 条
（`backend/app/core/config.py` 第 115–116 行）。compose 里那两个覆盖不是给它的：
`DB_POOL_SIZE=5` 在第 32–33 行、属于 `device-connection`，`3` 在第 90–91 行、属于
`preview-connection`。20 个并发请求根本够不着池子天花板，所以排队不在池子上。（这个数字
也说明了加工作进程的代价——见下一节。）

**不是「某个 await 卡太久」。** 最容易被想到的那个修法——在逐条判定那一段（20 次连续
同步查询，每次 1–2 ms 行构造）之间插 `await asyncio.sleep(0)` 把循环让出去——实测没有用：
20 并发的墙钟 861 ms → 914 ms，在噪声里。循环不是被某一处长时间不让出卡住的，它总体就是
忙的，插让出点救不回总量。

数据库侧也单独量过（同一批语句，不经 Python，直接 20 个客户端并发）：479k 行的
`count(*)` 从 28.5 ms 涨到 532 ms，取 438 行的那条从 100 ms 涨到 1410 ms。**这张表不能用来
说「库不是瓶颈」**——本机只有 4 个核、宿主机还满载，库那边也扩不动。把库排除掉的是对照三，
不是这张表。

## 五、「加 worker」不是一个启动参数

把 `--workers 2` 加上去会在三个地方动到别的东西，都不是小事：

- **连接预算。** 每个工作进程有自己的池，各 35 条。`config.py` 第 92–113 行那笔账是按
  「两个后端进程（旧的 + 新的）+ 连接持有者」算的：2 × (35 + 1) + 10 + 10 = 92 / 97，dev 的
  服务器 2026-09-18 才提到 200 条连接。一个容器里跑两个 worker 就是同一条进程的池多一份，
  上线时的三份变四份。
- **进程内状态。** `ChatService.live`（`backend/app/domain/agent/chat.py` 第 253–256 行）
  装着「这一进程正在跑的活」：`active_turn_ids`、座位锁与房间锁、`hook_work`、几张 note
  表。它自己的说明是「lives and dies with the process」（`turn/state/live.py` 第 1–6 行）。
  读路径就在读它：`running_tasks`（`backend/app/domain/agent/liveness.py` 第 84–115 行）用
  `chat.live.active_turn_ids` 判断「这条活有没有一轮在跑」，调用点在
  `backend/app/api/awaiting_deps.py` 第 175 行。它同时还有一条查 `AgentTurn` 的兜底，所以
  多进程下不会立刻错，但「在跑」这个信号会变成「另一条进程看见的 + 库里刚起来的」。
- **单进程假设不止这一处。** 同一段注释里点了名的还有 `_gateway_lock`（对
  `project.settings` 的读改写）和周期性任务。

所以这一步的结论不是「去加 worker」——加不加 worker 取决于 dev 那台机器给容器几个核（
compose 里 backend 没有 `cpus:` 限制，也没人量过），而且要先决定上面这三样怎么办。

## 六、要进 1 秒，得动的是什么

把 20 个请求压在 1 秒里，就是要求循环每个请求最多花 ~50 ms——**本机实测一个请求 35 ms
（进程内）/ 40–70 ms（真 HTTP，同一个核）**，这个任务早先在 dev 上记到的数是 50–60 ms，
量级一致。所以这是一个 CPU 预算问题，不是「哪条查询慢了」的问题。按第二节的表，省钱的顺序是 取数（27.4 ms）→ 组装 DTO（5.2）→ 逐条判定（2.0）；
取数内部最贵的是 `blocks.questions`，而按语句拆过的数字看，钱主要花在把行变成对象上，
不在数据库往返上。

三条路，代价各不相同，都还没有定：

1. **削减每请求 CPU**（不动进程模型，只是要继续削同一条读路径）。这条路上一轮已经削过
   一次（p50 1418 → 881 ms）；剩下的余地要看 `blocks.questions` 那 7.3 ms 具体在构造什么。
2. **加工作进程**（`--workers N`，或按读路径拆一个只读进程）。直接线性，但要先付第五节的
   三笔账。
3. **让这一夜清单不必每次重算**（缓存 / 增量）。这一条会动到「一屏上到底该有谁」的语义，
   不在这一步的范围内。

## 附：怎么复现

本机没有系统装的 Postgres，用的是这台沙箱里现成的一份 postgres 17.11 发行版
（`-c shared_preload_libraries=pg_search`）。新开一个库自己用，不碰别人的库；`alembic
upgrade head` 后按 `scripts/perf-awaiting-me/README.md` 的配方播种（`PERF_DATABASE_URL`
指过去），然后：

    PERF_DB_URL=… PERF_HANDLE=perfviewer uv run --project backend \
        python scripts/perf-awaiting-me/measure_awaiting_me.py     # 第二节那张分段表
    PERF_DB_URL=… PERF_CONCURRENCY=1/4/8/20 … probe_concurrency.py  # 第二张表

真 HTTP 那一张用 uvicorn 直接起 `app.main:app`，把鉴权依赖换成固定的 `perfviewer`
（`.dependency_overrides`，仓库代码不动），用 20 条保活连接打 `/awaiting-me`；进程 CPU 读
`/proc/<pid>/stat`。两个工作进程就是同一个命令加 `--workers 2`，CPU 取两个子进程的和。

注意本机宿主机是满载的（load ~82，cpuset 4 核），**墙钟会抖，看 CPU 那两列**。
