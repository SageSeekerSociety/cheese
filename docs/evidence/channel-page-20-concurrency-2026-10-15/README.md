# 这一轮的原始输出

上一层的 `../channel-page-20-concurrency-2026-10-15.md` 里的实测数字都出自这里。
造库时的规模数（13 个项目、1917 条活、48 万个消息块）来自播种脚本
`scripts/perf-awaiting-me/seed_awaiting_me.py`，不在本目录。
生成命令和起库步骤写在报告最后的「怎么重算」一节。

| 文件 | 是什么 | 生成方式 |
|---|---|---|
| `leg1-asgi.json` | 同进程 ASGI 两路之一，7 批 20 并发，每接口的墙钟/CPU/序列化分布 | `concurrent_read_probe.py`，`PERF_LEG=asgi` |
| `leg2-http.json` | 真 uvicorn + 20 条保活 HTTP 连接；每接口只有路径、响应字节数和 7 批的 `{wall, cpu, worst}`，没有序列化分布 | `concurrent_read_probe.py`，`PERF_LEG=http` |
| `sql-census.txt` | 单请求串行读一遍，每接口的 DB 时间、SQL 条数、语句排行 | `sql_census.py` |
| `sql-rowcounts.txt` | 每个接口每条语句取回几行（`cursor.rowcount`）。用同一个脚本另跑一遍，那遍每接口只发一次请求，所以段头写 `rounds=1`；它和 `sql-census.txt` 的两遍时点不同 | `sql_census.py`，`PERF_ROWCOUNTS=1` |
| `profile-burst.txt` | 20 并发下循环 CPU 的去向，`cProfile` + `pstats` | `profile_burst.py`，`PERF_ONLY="channel detail,project task list"` |
| `explain-channel-detail.txt` | 频道详情那条 OR 语句七个执行计划（A、B、C、D1–D3、E） | `explain_channel_detail.py` |
| `explain-channel-detail-rerun.txt` | 同一脚本再跑一遍，用来看毫秒数的抖动 | 同上 |

带 `-after` 后缀的那几份是**同一套脚本在改完之后重跑的**，对应报告文末「修完之后」
一节。改的是产品代码（下推筛选、重写那条 OR、worker 数可配），探针脚本本身没动。

| 文件 | 是什么 | 生成方式 |
|---|---|---|
| `leg1-asgi-after.json` | 同进程 ASGI 路，改后重跑 | `concurrent_read_probe.py`，`PERF_LEG=asgi` |
| `leg2-http-after.json` | 真 uvicorn 路，改后重跑 | `concurrent_read_probe.py`，`PERF_LEG=http` |
| `sql-census-after.txt` | 改后单请求串行读数 | `sql_census.py` |
| `sql-rowcounts-after.txt` | 改后每条语句带回几行 | `sql_census.py`，`PERF_ROWCOUNTS=1` |
| `explain-channel-detail-after.txt` | 改后的两种计划形状：A 是应用现在的写法（索引位图），N 是把它还原成改前的 `OR threads.room_id IN (…)`（全表扫） | `explain_channel_detail.py` |

改后那几份里 `explain-channel-detail-after.txt` 只有 A、N、C、D1–D3 六段：
脚本改过之后「E」这种写法就是应用现在的写法，所以它成了 A，原来的 A 变成用来
对照的 N。

## 前后对照那组（`pair-after-fix/`）

**报告「修完之后」那两张表用的是这一组，不是上面单跑的那两份 `-after`。** 原因：
上面那两份改前的读数出自上午 load 90–160 的一轮，改后是 load 30–50 跑的，直接相减
会把收益算大。这一组是把改前的代码从同一个提交（`5783020a`）切一份出来，
**两棵树在同一台机器上交替跑同一条探针**：

| 名字 | 哪棵树 | 腿 | 批次 |
|---|---|---|---|
| `base-*` | 改前（`5783020a`） | `http` / `asgi` | 各 2 轮，每轮 7 批 20 并发 |
| `after-*` | 改后（任务分支） | 同上 | 同上 |

跑的顺序是 base-http、after-http、base-asgi、after-asgi，重复两轮；四次读数的 load
在 35–85 之间。`.json` 是完整原始样本（和 `leg1-asgi.json` 同格式），`.txt` 是那份
人读的摘要，开头一行是当时的主机负载。

判读时看 `awaiting-me (reference)` 那一行：它两轮都该在 1.00 s 上下、前后几乎不动，
它偏了就说明这一对读数不可比。`channel blocks` 和 `channel members` 前后差一成上下，
在这条基线的漂移范围里。

## 读的时候要知道的两处口径

- **`sql-rowcounts.txt` 段头括号里是 HTTP 状态码和响应字节数，不是条目数、也不是 SQL 行数。**
  例如 `== channel detail (200, 1875 B) 37 statements ==` 里的 200 是状态码、
  1875 B 是响应大小，37 才是这一遍跑到几条语句；每条语句带回几行看它自己那行 `rows=`。
- **`explain-channel-detail.txt` 的毫秒数每次跑会动几个百分点。**
  要认的是计划的形状（`Seq Scan` + `Rows Removed by Filter: 479832` 对
  `Bitmap Index Scan`），不是末位。报告 §2 记了复跑一遍的对照。
  文件里 E 那两个支线 id 是合成值：这份种子库 `threads` 表是空的。
