# 页面打开耗时测量

量「点开一个页面，多久之后它的内容出现在屏幕上」，按页面给一张排序表。
只读运行中的前端，不改产品代码。

## 怎么跑

1. 起本地全栈（后端 :8081 + Postgres :5433 + 前端），见仓库 README「Quick Start」。
2. 前端要跑生产构建（`npm run build`），静态起在 :3000，并把 `/api` 转给 :8081。
   开发模式的 HMR 会让数字失真。
3. 造演示数据并写出 `params.json`：

       cd backend && PYTHONPATH=. uv run python ../scripts/perf-open-page/seed_demo.py

   它会建好项目、频道、支线、任务、演示技能和演示小队，把它们的 id 写进本目录的
   `params.json`。页面判据要读的那些区块得先有数据，否则页面永远画不出来、量到的是
   超时。重新播种后 id 会变，`measure.py` 每次启动都重读 `params.json`。
4. 量：

       uv run --with playwright python scripts/perf-open-page/measure.py \
           --base-url http://127.0.0.1:3000 \
           --username alice --password demo12345 \
           --runs 3 --modes walk,cold --out /tmp/perf-open-page.json

5. 出表：

       python scripts/perf-open-page/report.py /tmp/perf-open-page.json

第一次跑要先装浏览器：`uv run --with playwright python -m playwright install chromium`。

`--only inbox,devices` 只量某几页；`--runs` 是每个页面重复几遍（取中位数）。

## 上次那批数字

`measurements.json` 是 2026-10-12 那次全量跑的原始输出，`report.py` 直接读它就能
重出那张表，不必先起栈。

`measurements.json` 里的 `api_timeline` 去掉了查询串，同一路径、参数不同的两次读
看起来像同一个请求打了两次；现在的 `measure.py` 保留查询串。

### 那次报告里撤回的两条

当时的结论（PR #3530 的描述）里有两条复核后不成立：

- **「频道页 blocks → read → environment 三段串行，改并发约省 300 毫秒」**：不成立。
  `read` 发出时不需要 `blocks` 的结果；`environment` 是频道名册（`TopicMembers.vue`）
  故意等浏览器空闲才读的。三段只是先后发出，不是互相等，300 毫秒是把后两段的时长
  全算成了等待。
- **「文档页 `my-teams`、`spaces`、`projects` 在同一遍里各打两次」**：只对一部分。
  两条 `/spaces` 参数不同（导航栏取 50 条，空间页取 12 条），不是重复；上面说的去掉
  查询串让它们看起来一样。`my-teams` 的重复是真的：导航栏和团队页各取一次，走的是
  旧代码路径，这次没有改。`projects` 是否真的重复没有核实。

## 读「串行段」

`api_serial_ms`（表里的「串行段」）是第一批之后各批请求的时长之和。后一批晚发，可能
是要等前一批的结果，也可能是故意推迟、什么都不等。所以它只是「改成并发最多能省多少」
的上限；要算真能省多少，先去代码里看那个请求为什么晚发。

## 两个口径

- `walk`：应用已经开着（缓存是热的），脚本把路由推过去并计时。这就是「点一下侧栏」的场景。
- `cold`：整页重新加载、浏览器缓存空着。

每页记：

| 字段 | 意思 |
| --- | --- |
| `ready_ms` | 从路由切过去（walk）或开始导航（cold）到「这个页面自己的内容画出来」的毫秒数 |
| `last_response_ms` | 窗口内最后一份接口响应结束的时刻 |
| `network_ms` | 第一份请求发出到最后一份响应结束的跨度 |
| `requests` / `api_requests` | 这一遍一共发了几个请求、其中哪些是接口 |
| `long_task_ms` / `long_task_count` | 主线程长任务（>50ms）累计时长和次数 |

「内容画出来」由 `pages.py` 里每页的判据决定：一个选择器底下有足够长的真实文字，
且骨架屏不在。判据写在配置里而不是写死在这段代码里，加一页就加一行。

## pages.py

一行一页：路由（带占位符）、完成判据的选择器、文字长度门槛、要排除的骨架屏选择器。
