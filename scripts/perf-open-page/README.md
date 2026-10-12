# 页面打开耗时测量

量「点开一个页面，多久之后它的内容出现在屏幕上」，按页面给一张排序表。
只读运行中的前端，不改产品代码。

## 怎么跑

    # 1) 起本地全栈（见仓库 README「Quick Start」；本目录的 setup 步骤另见下）
    #    后端 :8081，前端 :3000
    # 2) 量
    uv run --with playwright python scripts/perf-open-page/measure.py \
        --base-url http://127.0.0.1:3000 \
        --username alice --password demo12345 \
        --runs 3 --modes walk,cold --out /tmp/perf-open-page.json

第一次跑要先装浏览器：`uv run --with playwright python -m playwright install chromium`。

`--only inbox,devices` 只量某几页；`--runs` 是每个页面重复几遍（取中位数）。

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
