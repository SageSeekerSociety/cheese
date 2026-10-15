# 这一轮的原始输出

上一层的 `../channel-page-20-concurrency-2026-10-15.md` 里的每个数都出自这里。
生成命令和起库步骤写在报告最后的「怎么重算」一节。

| 文件 | 是什么 | 生成方式 |
|---|---|---|
| `leg1-asgi.json` | 同进程 ASGI 两路之一，7 批 20 并发，每接口的墙钟/CPU/序列化分布 | `concurrent_read_probe.py`，`PERF_LEG=asgi` |
| `leg2-http.json` | 真 uvicorn + 20 条保活 HTTP 连接，同上 | `concurrent_read_probe.py`，`PERF_LEG=http` |
| `sql-census.txt` | 单请求串行读一遍，每接口的 DB 时间、SQL 条数、语句排行 | `sql_census.py` |
| `sql-rowcounts.txt` | 同上那一遍里每条语句取回几行（`cursor.rowcount`） | `sql_census.py` 的 rowcount 模式 |
| `profile-burst.txt` | 20 并发下循环 CPU 的去向，`cProfile` + `pstats` | `profile_burst.py`，`PERF_ONLY="channel detail,project task list"` |
| `explain-channel-detail.txt` | 频道详情那条 OR 语句七个执行计划（A、B、C、D1–D3、E） | `explain_channel_detail.py` |
| `explain-channel-detail-rerun.txt` | 同一脚本再跑一遍，用来看毫秒数的抖动 | 同上 |

## 读的时候要知道的两处口径

- **`sql-rowcounts.txt` 段头那个数是响应里的条目数，不是 SQL 行数。**
  例如 `== channel detail (200, 1875 B) 37 statements ==` 里的 200 是响应条目，
  37 才是这一遍跑到几条语句；每条语句占多少行看它自己那行 `rows=`。
- **`explain-channel-detail.txt` 的毫秒数每次跑会动几个百分点。**
  要认的是计划的形状（`Seq Scan` + `Rows Removed by Filter: 479832` 对
  `Bitmap Index Scan`），不是末位。报告 §2 记了复跑一遍的对照。
  文件里 E 那两个支线 id 是合成值：这份种子库 `threads` 表是空的。
