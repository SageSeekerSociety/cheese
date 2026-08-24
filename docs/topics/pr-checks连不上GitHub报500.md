# pr-checks 连不上 GitHub 就报 500

## 目标
@彭文博 报的后端报错：`GET /api/topics/{id}/pr-checks` 抛 `httpx.ConnectError`，堆栈直接刷进房间（同一条报错在这个话题里被推了 3 次）。修掉它。

## 结论：是代码的错误处理漏了一层，不是接口逻辑坏了

根因分两半：

1. **外部原因（触发）**：后端连 `api.github.com` 时 TLS 握手没建起来（堆栈最深处是 `start_tls`）。这属于服务器出网抖动一类的环境问题，代码消不掉，只能优雅降级。
2. **代码原因（放大）**：`/pr-checks` 的注释白纸黑字写着「永不报错，只答 `available: false`，好让 UI 无脑轮询」，但它只捕了 `GitHubPRError` 一种异常。网络层的 `httpx.ConnectError` 不在其中，于是一路冒到最外层 → 500 → `report_unhandled_to_room` 把整段堆栈发进房间。而这个接口是**前端定时轮询**的，所以不是报错一次，是每隔几秒报一次，刷屏。

顺带发现同一处还有两个没人管的漏口：
- `GitHubPRError` 的注释自称「GitHub 拒绝了操作（**或者网络拒绝了**）」，但网络异常从来没被翻译成它——写的和做的不一致。
- 旧的 try 只圈住了两个 GitHub 调用，前面的 token 铸造、上游地址读取都在圈外，同样会 500。

## 改法（两层）

1. `<&backend/app/domain/review/github_pr.py>`：给 `GitHubPRClient` 的 `open_pr / pr_view / merge_pr / check_runs` 加装饰器，把 `httpx.RequestError`（连接失败、超时、读写中断都在内）翻译成 `GitHubPRError`。包整个方法而不只是那次请求，因为**铸 installation token 也要联网**，网络挂了同样可能挂在那一行。这样全仓已有的 `except GitHubPRError` 分支自动开始生效。
2. `<&backend/app/api/routes/accept.py>`：`/pr-checks` 主体挪进私有函数，路由整段兜底 —— 业务异常（404 之类）照旧抛，其余一律 `logger.exception` 记录后返回 `{"available": false, "reason": ...}`。兑现注释里承诺的契约。

前端本来就 try/catch 了这个请求，UI 不会炸；这次修的是「后端往房间刷堆栈」，以及 reason 现在能带出来。

**范围到此为止**：两阶段采纳的轮询器（`SchedulerService.poll_open_prs`）已经有 per-card `except Exception` 兜底，网络故障只会写日志、不会刷房间，所以没动它。

## 测试（全部实跑，绿）
- `<&backend/tests/unit/test_github_pr.py>`：四个方法各喂一次 `ConnectError` → 必须抛 `GitHubPRError`。
- `<&backend/tests/integration/test_accept_pr_publish.py>`：GitHub 不可达 → 200 + `available:false` + reason 带 `ConnectError`；GitHub 调用之外的地方炸（读上游）→ 同样 200。

跑的结果：
- accept 相关集成测试 69 passed
- `tests/unit` 全量 + PR 相关集成测试 **3375 passed, 1 skipped**
- ruff、pyright（改动文件）、check-repo-rules.sh 全绿

沙箱备注：首次 `uv sync` 因 rustup 没装上而失败（srp-rs 要现编 Rust），手动跑一次 rustup-init 后正常。

## 进展
- [x] 定位 → 改完 → 测试写好 → 全部跑绿
- [x] 递验收卡

## 还没解决的部分（要人看一眼）
代码这层只能保证「连不上时不刷屏、界面显示读不到 CI 状态」。**服务器为什么连不上 api.github.com 是运维问题**——如果这不是偶发抖动而是常态，PR 状态、CI 镜像、自动合并都会时好时坏。建议 @andy 或 @李甘 在那台机器上确认一下出网情况。
