---
title: 后端结构与接口约定
kind: 参考
summary: 一份后端代码的四个进程、路由发现、统一信封与错误体、写面闸门、幂等与归属锁、周期任务和迁移约定。
covers:
  - backend/app/main.py
  - backend/app/device_connection_app.py
  - backend/app/llm_tunnel_app.py
  - backend/app/forge_events_app.py
  - backend/app/api/
  - backend/app/core/errors.py
  - backend/app/core/single_use_state.py
  - backend/app/core/ownership.py
  - backend/app/core/background.py
  - backend/app/domain/idempotency/
  - backend/alembic/
  - docs/api-conventions.md
---

# 后端结构与接口约定 {#backend-app}

后端只有一个 Python 包（`backend/app/`），按不同的启动命令跑成四个进程；请求进来先过中间件、再进按目录自动发现的路由，出口是一个统一的 JSON 信封。

> 讲：代码怎么组织、路由怎么挂上、响应和错误长什么样、一次写请求要过哪些闸。不讲：部署拓扑和发版换什么（见[部署拓扑](/dev/topology#planes)），哪个域做哪件事（各域自己那一页，清单见[按代码路径查文档](/dev/by-path)），每个接口的参数（见 `GET /api/openapi.json`）。

## 一份镜像，四个进程 {#processes}

四个入口共用 `ghcr.io/sageseekersociety/cheese/backend:<提交号>` 这一个镜像，差别只在启动命令（`deploy/compose/docker-compose.base.yml`、`deploy/llm-tunnel/compose.yml`、`deploy/forge-events/compose.yml`）：

| 进程 | 入口 | 端口 | 管什么 | 发版时 |
|---|---|---|---|---|
| 主 API | `app.main:app` | 8081 | 全部业务接口、投递与调度、Agent 轮、迁移 | 替换（滚动交接见[部署拓扑](/dev/topology#handover)） |
| 机器连接服务 | `app.device_connection_app:app` | 8082 | 设备与终端 WebSocket，以及进出设备的 RPC | 不重启（`release-device-connection.yml`） |
| 模型隧道 | `app.llm_tunnel_app:app` | 8091 | 远端机器把模型流量送进来的 WebSocket | 不重启（`deploy/llm-tunnel/up.sh`） |
| 代码托管事件中继 | `app.forge_events_app:app` | 8093 | 接 GitHub App 的 webhook，再经 WebSocket 推给各部署 | 单独部署 |

分开的原因写在各自模块的 docstring 里：机器连接服务要活过主 API 的替换，否则每次发版都会掐断设备链接；模型隧道单独成进程，是因为隧道 WebSocket 原来终结在后端进程里，换容器就切断了那个窗口内所有机器的模型流量（`llm_tunnel_app.py` 引 #551）。

三个旁路进程都不做路由自动发现：它们显式 `include_router` 自己那几条，另加一个 `/healthz`。

- **机器连接服务**用请求头 `x_device_connection_secret` 与 `settings.device_connection_auth_secret` 做常量比较（`_authorize`），不一致直接 403。它另外导出 `/internal/device-connection/{release-drain,release-resume,snapshot,call/<name>}`：`release-drain` 只在没有任何在跑的 RPC、没有待处理设备调用时返回 200，否则 409 —— 这是「现在就换，还是再等等」的判据。`call_executor` 那条路带 `asyncio.shield`：调用方（主 API）在发版中掉线，不能让已经发给设备的调用被取消。
- **模型隧道**故意不 import `app.main` 的任何东西，也不碰数据库：它只按 scoped token 自己的 claims 做 HMAC 校验，这正是它能脱离整个应用单独跑的原因。
- **事件中继**按订阅投递。它维护 `connections: dict[deployment, Connection]`，没有连着的部署就返回 HTTP 503（发送方把这次 webhook 当作失败，之后靠重建订阅补上）；同一个部署要连第二次时回 `1013` 关闭，让滚动发版中的新后端重试到旧后端退出为止。校验分两种：Forgejo 看 `x-forgejo-signature`，GitHub 看 `x-hub-signature-256`，都按部署的 relay key（可再按 `project_id` 派生）算 HMAC-SHA256，请求体上限 5 MiB。

## 路由是按目录发现的 {#routers}

`app/main.py` 的 `_discover_routers` 用 `pkgutil.iter_modules` 遍历 `app/api/routes/`，把**每个模块里所有模块级 `APIRouter` 实例**都 `include_router` 进来。所以加一个域 = 加一个 `app/api/routes/xxx.py` 并定义 `router`，不用改 `main.py`；一个模块导出多个 router 也可以（`seen` 按对象 id 去重）。

| 情形 | 结果 |
|---|---|
| 模块导入失败，`settings.environment != "production"` | 当场抛出，服务起不来 |
| 模块导入失败，生产环境 | 记一条 ERROR 日志，模块名进 `FAILED_ROUTE_MODULES`，进程继续起 |
| 生产环境有模块没挂上 | `GET /healthz`（`app/api/routes/health.py`）返回 `{"status": "degraded", "unmounted": [...]}` |

生产环境选择「一个坏模块不拖垮整个应用」，代价是一整组接口 404 而进程照样健康 —— 所以 `/healthz` 必须把没挂上的模块报出来。历史上 `/sandbox/hooks` 就这样整组消失过（每个 agent 事件 404），而唯一的症状出现在调用方那边。

路由路径是**裸的**，不带 `/api`：网关那段前缀由前端 nginx 的 `location /api/ { proxy_pass http://backend:8081/; }` 剥掉，约定见[接口约定择要](#conventions)。FastAPI 用 `redirect_slashes=False` 建应用（`main.py` 里的 `app = FastAPI(...)`），尾斜杠是 404 而不是 307。

## 出口统一信封 {#envelope}

`app/api/response.py` 只有两个函数，是成功响应的唯一形状：

| 函数 | 返回 |
|---|---|
| `ok(data, message="ok", warnings=None)` | `{"code": 200, "message": ..., "data": ...}`；`warnings` 非空才出现 |
| `page(items, total)` | `{"data": items, "total": total}`（分页用，不带 `code`/`message`） |

`warnings` 挂在 `data` **旁边**而不是里面：`data` 是这次动的那件东西，警告说的是「它哪里不像话」，同一个 `data` 上会挂不同的一串。空表不出现 —— 「没有警告」和「没有这个字段」对读的人是同一件事。

`app/api/routes/` 下 82 个模块里有 56 个 import 了它；其余（健康检查、指标、Webhook 入口等）按协议本身需要返回裸结构。

## 错误怎么变成响应 {#errors}

`app/core/errors.py` 装了**两套**异常体系和一张处理器表，全部经 `register_exception_handlers(app)` 注册（`device_connection_app.py` 装的是同一份）。

| 异常 | 状态码 | 响应体 |
|---|---|---|
| `BaseError` 及其子类（`BadRequestError`、`NotFoundError`、`ForbiddenError`、`ConflictError`、`QuotaExceededError`、`SystemBusyError` …） | 异常自带 | `{"code", "message": "类名: 原话", "error": {"name", "message", "data", "retryable": false}}` |
| `AppError` 及其子类（`ValidationError`、`UnauthorizedError`、`GatewayUnavailableError`） | 类属性 `code` | `{"code", "message", "data": null, "error": {"name", "message", "retryable": false}}` |
| `StarletteHTTPException`（路由里 `raise HTTPException(...)`） | 原状态码 | 走 `format_error_response`，`name` 恒为 `"Error"`；**异常自带 headers 会带出去** |
| `RequestValidationError`（请求体不合模型） | 400（不是 FastAPI 默认的 422） | `BadRequestError` 的形状，细节在 `error.data.details` |
| `DeviceOffline` | 409 | 带 `X-Device-Id` 头 —— 客户端靠它区分「机器不在」和「调用出错」，见[设备与机器接入](/dev/machines#failure)。子类 `LinkInterrupted`（链路断在调用半路，结果未知）另带 `X-Device-Link: interrupted` |
| `DeviceCallError` | 502 | 机器自己的原话，`failure_code` 挂在 `error` 下 |
| `ClientDisconnect`（浏览器读到一半挂了） | 499 | 只记一条 info，不当故障 |
| 其它任何异常 | 500 | `{"code": 500, "message": "服务器内部错误", "data": null}`，真正的原因只进日志 |

三条读这份表时要记住的：

- `error.name` 是调用方用来分辨「状态码一样但条件不同」的字段（`SudoRequiredError` 与普通 403 的区别就在这里）。
- `retryable` 在今天构造出的每一个错误体里都是 `false`（`BaseError.to_response_body`、`format_error_response`、`AppError` 那三个都写死）。它现在不是一个能读的信号。
- `Accept: text/event-stream` 的请求拿到的是 `event: error\ndata: <一句话>` 的 SSE 正文而不是 JSON；这条分支在四个处理器里各写了一遍，**只有 `BaseError` 和 `HTTPException` 那两支把异常的 headers 转发出去，SSE 与 validation 两支不转**。

处理器一律用 `closing_the_socket` 包一层：异常发生在 WebSocket 连接上时不能返回 HTTP 响应（uvicorn 会拒绝并报「Expected ASGI message ...」），改成记一条 WARNING 后按 1011 关掉。

## 写面闸门：谁是芝士 {#gate}

`main.py` 里 `cheese_token_gate` 这个中间件守着一张**用字符串写的**表 `_CHEESE_WRITE_PATHS`（8 条 `(方法, 正则)`）：会话里的 `cheese` CLI 能写、浏览器只读的那些接口。正则的捕获组必须叫 `topic` 或 `project`，闸门拿它和 `x-cheese-token` 比对。

- 令牌先试 `is_valid_cheese_token(token, project_id=..., topic_id=...)`（每轮一次性令牌，按 URL 里的 id 匹配）。
- 再试项目级凭据：`looks_like_project_agent_credential` 为真时不能按字符串比对（topic 路径里不带 project），走 `_credential_opens_gate` —— 它**在路由之前**就要读库，所以借 `app.dependency_overrides` 拿 `get_db` 的会话，保证测试换库时闸门和请求看的是同一个库。校验包含项目匹配、凭据未撤销、以及 `resolver.authorize_topic` / `authorize_project`。
- 不通过就返回 `{"code": 401, "message": "invalid sandbox token", "data": null}`。
- 通过之后把 `x-cheese-turn` 解析成 `current_work_id` 塞进 contextvar，这个请求写下的所有 block 都继承它。

这张表的正则就是闸门本身，**路由改名它不会跟着改**：#370 那次把路由前缀拍平之后 8 条全部失配 —— 失配不报错，它只是静默地把芝士的写面开放给任何够得到端口的人。测试抓到了这一条（`test_project_agent_credential`、`test_ask_options` 从「拒绝」变「放行」），这是唯一一次被发现。

「没列进白名单」本身拦不住任何东西：没列进去的写路由压根不过这个中间件，症状是静默放行而不是 401；真正拦住芝士的是路由上的登录校验加服务里的 `_forbid_ai`（同一形状的坑见[验收与采纳](/dev/accept#traps)）。

## 幂等：两张完全不同的表 {#idempotency}

| | `domain/idempotency/` | `core/single_use_state.py` |
|---|---|---|
| 存哪 | Postgres，一行 = 一个已经发生的副作用 | Redis，一个 key = 一张用过就作废的票 |
| 键怎么来 | `action_key(continuation_id, action, *parts)`：sha256 over「续跑 id + 动作名 + 区分位」 | 调用方给的 `jti` |
| 保证什么 | 「键存在」和「副作用发生过」**同一事务**，不可能对不上 | 签名与 TTL 都不管的「这张票已经花掉了」 |
| 给谁用 | 芝士自动续跑（`AgentWorkRunner._execute` 的 continuation id） | 浏览器里的 OAuth / 账号绑定 / 安装流程 state |
| 失败时 | 随事务一起回滚 | **fail closed**：连不上 Redis 就抛 `SingleUseUnavailableError`，调用方必须当拒绝处理 |

幂等键里**不能放 turn id**（续跑是新 turn，放进去就重做一遍），也不能跨无关的 turn 复用（同一句「好的」隔一周是两条消息）。同时满足这两条的是**续跑 id**：一次逻辑工作及其所有自动重试共享同一个。人在界面点按钮的请求没有续跑 id，于是整个跳过检查 —— 自动续跑才是这个机制存在的理由，人点两下就是想点两下。

`single_use_state` 的 `reserve(scope, jti, ttl_s=...)` 在签发侧写 key，`claim(scope, jti)` 在回调侧用 `DELETE` 花掉它（`DELETE` 是原子的，所以并发回调只有一个能看到 1）。TTL 要和令牌自己的有效期对齐。它解决的正是 #222：一个 OAuth state 被转发到别人已登录的浏览器，签名有效、时效未过，只有「服务端记得这张票花过了」能拦下来。

## 谁是「正在跑这些活的进程」 {#ownership}

滚动发版时新旧两个主 API 同时连着同一个库。答一个请求、起那个请求要求的轮，两遍都安全；「我在监听哪些会话、我在跑哪些轮、我上次扫到哪儿」不行 —— 那是从**本进程的内存**里判断出来的，两个进程同时做，各自会把对方的活看成孤儿，然后关掉它、重发它、或者把输出落第二遍。

`core/ownership.py` 用**一条 Postgres 会话级 advisory lock**（`OWNER_LOCK = 0x636865657365`，即 `"cheese"` 的字节）指定归属：

- 锁挂在一个**自己的连接**上（自己的 `NullPool` engine，`AUTOCOMMIT`），不占应用连接池的额度，也不会被池回收。
- 进程死掉 → 连接断 → 锁自动释放，下一个进程接过去，不需要任何人先发现它死了。没有表、没有租约、没有心跳要调。
- `acquire()` 拿不到就每 1 秒重试，期间照样服务请求，只是活还没接过来。
- `keep_holding()` 每 10 秒探一次：连接还在、锁还是自己的，继续；锁被别人拿走了，就 `SIGTERM` 自己，由容器的重启策略把它变回一个排队等锁的进程。这条是对「万一」的兜底。

`main.py` 的 `lifespan` 里，接过锁之后按顺序做：恢复会话订阅 → 清点孤儿轮 → 清点丢失消息 → 先扫一次超龄闸门卡 → 起 `periodic_jobs` 里所有周期任务。关停时反着来：先不再接新轮、等在路上的 prompt 落地、不再读会话、才放锁（顺序见 `lifespan` 的 `finally`）。

## 周期任务清单 {#periodic}

平台所有常驻巡检都在 `core/background.py` 的 `periodic_jobs()` 一个列表里，`lifespan` 起的就是它。放在一处而不是散在各域，是为了让「写完了、部署了、从来没有跑过」这种**没有报错、只有缺席**的失败有个能被看见的地方。

`PeriodicRunner` 把每条常驻任务都会踩的四个坑一次收掉：强引用（asyncio 只持弱引用，任务可能在 await 中途被 GC）；**interval ≤ 0 表示这台机器不跑它**（部署和测试共用的那个开关）；一轮崩掉只算一次而不是让循环死掉；只在**这一轮真的做了点什么**的时候打日志（`_worth_reporting` 对 mapping 的语义是「任何一个值非假就说话」，所以 `{"failed": 0}` 闭嘴）。

| 任务 | 间隔来源 | 做什么 |
|---|---|---|
| forge event subscriptions | 固定 300s | 对账仓库 webhook 订阅 |
| forge credential cache cleanup | 固定 3600s | 清过期的托管平台令牌缓存 |
| pr poll / task pr poll / draft pr sweep | `accept_pr_poll_interval_s` | 镜像 PR 合并态、给没卡的任务补卡、开 draft PR |
| orphan sweep | `orphan_sweep_interval_s` | 扫死了没带进程一起死的轮 |
| chat progress reminder | `chat_progress_check_interval_s` | 提醒沉默的轮 |
| gate sweep | `gate_sweep_interval_s` | 收超龄的闸门孤儿卡 |
| cloud host pool sweep / cloud warm pool | `machine_enroll_interval_seconds` | 云主机池：同步宿主机状态、入网、按需扩容和释放空闲宿主机；维护预热池 |
| subscription usage ingest | `subscription_ingest_interval_s`，未设 `SUBSCRIPTION_USAGE_LOG` 时为 0 | 把计量代理的账本吃进 `resource_usage` |
| backend error flush | `backend_error_flush_interval_s` | 把后端报错按窗口收口后记成运行记录 |
| run record expiry | `run_record_expiry_interval_s` | 删掉超过 30 天的运行记录 |
| notification email drain / push drain | `notification_email_drain_interval_s` / `notification_push_drain_interval_s` | 那两条 Redis 队列的唯一消费者，不跑就一封邮件、一条推送都不发 |
| delivery resend | `delivery_resend_interval_s` | 投递账本上「记下了没发出去」的行 |
| docs question retention | 固定 86400s | 清过期的问芝士记录 |
| timed deliveries | 固定 30s | 定时投递到点派发 |
| overdue deliveries | `delivery_overdue_check_interval_s` | 给 AI 的投递记下超过 30 分钟还没送出去，就发告警（同一个问题一小时一条） |
| routines | 固定 30s | 周期任务与事件触发的一次执行 |
| task deadline sweep | `task_deadline_sweep_interval_s` | 任务截止 |
| memory dream | `memory_dream_sweep_interval_s` | 记忆整理 |

`spawn()` 是不留句柄的那一类：任务可能被 GC 掉，所以平台持一个强引用集合 `_INFLIGHT`；`hold()` 给需要留句柄以便关停时取消的工作。两者的崩溃都记 ERROR 而不是 WARNING（告警只捡 ERROR）。

周期任务的执行细节见[例行与巡检](/dev/routine#sweep)。

## 数据库迁移 {#migrations}

结构由 Alembic 管，`lifespan` 里不建表。约定：

- 迁移放在 `backend/alembic/versions/`，配置在 `backend/alembic.ini` 与 `backend/alembic/env.py`。
- **一条链，一个 head**。并行开发各写一支会把链分叉，CI 的 `backend / static` job（`.github/workflows/test.yml`）跑 `uv run alembic heads` 数 `(head)` 的个数，不等于 1 就红，并提示「rechain 到当前 head 或跑 `alembic merge heads`」。
- `backend/alembic/HEAD` 这个文件记着当前 head 的 revision id，由 pre-commit 的 `merging would not fork the alembic chain` 钩子（只对 `^backend/alembic/` 生效）维护 —— 它同时检查 HEAD 文件点的是链的 head、以及这次改动不会分叉。

退役的列也留在迁移里而不是删文件（例如 `971b4765fa69_drop_ccproxy_columns.py`），`downgrade` 可能只把空列加回来。

## 请求都带一身上下文 {#request-context}

`request_context` 这层中间件给每个请求绑定并回显 `X-Request-ID`（尊重调用方传来的那个），量出耗时，按**路由模板**（不是原始路径）记指标。标签里**不能有原始路径**：路径里带 UUID，等于给每条反馈、每个项目各建一条时间序列，而直方图永久留在进程内存里；读不到路由模板时退回常量 `(unmatched)` 而不是退回路径 —— 404 的路径是请求方随手写的，标签空间会由外部输入决定。

`/health`、`/health/`、`/metrics` 三个探针不计入 `active_requests`、`http_requests_total` 和路由表：它们是基础设施按固定间隔敲的门，算进来会让 `/metrics` 稳坐调用次数第一，而「当前正在处理的请求数」恒 ≥ 1。

中间件的洋葱顺序就是注册顺序反过来：Starlette 的 `add_middleware` 把新的插到 `user_middleware` 列表**最前**（`starlette/applications.py`，本仓库实跑 1.3.1），所以**后注册的在更外层**。`main.py` 里最后注册的三个是 `ResponseIntegrityAudit`（只数真正离开进程的字节，健康响应不吭声）、`SiteHostMiddleware`、`PreviewHostMiddleware`。

## 接口约定择要 {#conventions}

完整约定在 `docs/api-conventions.md`，客户端作者只需要记这几条：

| 约定 | 是什么 |
|---|---|
| 基地址 | `https://<主机>/api` + 后端声明的裸路径，逐字拼接。**schema 自带这个基**：`GET /api/openapi.json` 的 `servers[0].url` 就是 `/api` |
| 尾斜杠 | 是 404，不是重定向。别想着用 `root_path` 修：Starlette 0.35.0 起不再把 `root_path` 放回 slash redirect |
| 浏览器路径 ≠ 路由路径 | 后端交给浏览器去解析的东西（代理 cookie 的 `Path`、改写进 HTML 的 URL、要放进 iframe 的 `url`）必须用 `app.api.proxy.browser_path()` 构造，不能用 `request.url.path`（那是被剥过的） |
| 两条路由不能答同一个 URL | FastAPI 静默取先注册的那个，另一个的端点直接不存在。`tests/contract/test_api_addressing_contract.py` 对任何一对同 URL 失败 |
| 200 不证明打对了地方 | 没匹配上的请求落到前端 nginx 的 `location /`，返回 `index.html`。响应体是 HTML 就说明这个路径根本没到后端 |
| 连接器不走 `/api` | 已入网设备直连 `/connector/…`（它有自己的 nginx location）；静态产物在源根 `<origin>/connector/latest/<target>/cheesehost` |

## 边界与坑 {#traps}

- **闸门是字符串，路由是代码。** `_CHEESE_WRITE_PATHS` 不跟随路由移动，失配不报错，只是静默放行。动路由前先看那张表。
- **`retryable` 恒为 `false`。** 三个构造错误体的地方都写死了它，没有任何一处会把它设成 `true`。字段在，语义不在。
- **SSE 分支不转发 headers。** 带 `Accept: text/event-stream` 的请求出 validation 错误或 `HTTPException` 时走 `PlainTextResponse`，`DeviceOffline` 的 `X-Device-Id` 这类头不会跟着出去 —— 只有 JSON 那两支转发。
- **`page()` 不套信封。** 它返回 `{"data", "total"}`，没有 `code`/`message`；读分页响应时别按 `ok()` 的形状解析。
- **路由导入失败在生产是「降级」而不是「崩溃」。** 一整个模块会安静地 404，只有 `/healthz` 会说出 `unmounted` 列表 —— 健康检查若只看进程活着，看不出这件事。
- **dev 环境导入失败直接起不来。** `_discover_routers` 在 `settings.environment != "production"` 时把异常抛出去，所以本地和测试里一个坏模块是当场可见的。
- **归属锁只有一把。** `OWNER_LOCK` 是任意一个固定 bigint，唯一要求是「这个库里没有别的东西用它」。换库时若别处也用了这个值，两个系统会互相抢锁。
- **两个旁路进程的鉴权是共享密钥的常量比较**（`device_connection_auth_secret`、每个部署一条 relay key），没有撤销列表、没有过期；轮换一次就是换一个值。
