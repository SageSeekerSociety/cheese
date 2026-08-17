## 结论速览

| | 状态 | 原判断 | 实测 |
|---|---|---|---|
| ① 项目栏无名 | **已修** | 渲染层没把 `title` 落到 `<a>` | 属实 |
| ② 铃铛必 401 | **已修，但真因不是原判断** | 后端没这条路由 | 路由存在，带合法令牌 200；401 是令牌过期 |
| ③ blocks 间歇截断 | **只定位，未动** | 疑网关 buffer / 临时目录 | 指向 dev 的自动重部署窗口 |

---

## ① 项目切换栏没有可访问名称 —— 已修

`App.vue` 确实早就把 `title: p.name` 放进 rail item 了，漏在渲染层：
`RailItem.vue` 的 `<v-card :to>` 渲染出 `<a>`，但没有把 `item.title` 落成
`aria-label`。项目格子只画一个首字方块（`projectAvatar`），`innerText` 是空的，
于是链接没有任何可访问名称。

改动：`frontend/src/components/common/Navigation/RailItem.vue` 加
`:aria-label="item.title"`。**没有加原生 `title=`** —— 组件里已经有一个
`v-tooltip` 悬停浮层（`.rail-flyout`），再加原生 title 会在悬停时同时冒出浏览器
气泡和自定义浮层。悬停显示全名这条验收由那个浮层满足，测试里也钉住了。

测试：`RailItem.accessibleName.spec.ts`（3 条）。已做负向验证——把 `aria-label`
撤掉，其中 2 条立刻红。

---

## ② 铃铛未读数 401 —— 已修，但真因和原判断不同

### 原判断被实测推翻

「本仓后端根本没有这条路由」不成立。`backend/app/api/routes/notifications_flat.py`
在 2026-07-12 就接上了 `GET /notifications/unread-count`（change `vntmoxmxynsz`），
`_discover_routers` 挂在根路径上。四种凭证实测：

| 凭证 | 返回 |
|---|---|
| 无 | 401 |
| 乱码令牌 | 401 |
| 过期令牌 | 401 |
| **合法令牌** | **200 `{"count": 0}`** |

前端路径也是对的：`NEW_API_BASE_URL` 在部署里是 `/api`
（`deploy/compose/docker-compose.base.yml:92`），加上 `/notifications/unread-count`
= `/api/notifications/unread-count`；前端 nginx 的
`location /api/ { proxy_pass http://backend:8081/; }` 带尾斜杠，剥掉一层 `/api`，
后端正好收到 `/notifications/unread-count`。**按原方案改路径会把一条好路由改坏。**

### 401 这个信号本身没有分辨力

顺带实测了一条真不存在的路径 `/notifications/does-not-exist-xyz`：未认证时**同样
返回 401**（它匹配到 `/notifications/{notification_id}`，鉴权依赖先于 int 转换跑），
带合法令牌才露出 400。所以「401 而不是 404」推不出「路由不存在」——这个推理链条
在本仓是断的。

### 真因：访问令牌 15 分钟就过期，而启动时不看 exp

- `access_token_expires_seconds = 15 * 60`（`app/core/config.py:40`），刷新令牌 30 天。
- `AccountService.init()` 以前只看 localStorage 里有没有 `accessToken` + `user`，
  有就直接 `loggedIn = true`。`main.ts` 里这个 `init()` 还**不 await** 就 mount，
  于是 AppBar 的 `onMounted` 顶着一个可能已经死掉的令牌就去打 `/unread-count`。
- 只要距上次活动超过 15 分钟，这一发**必 401** —— 和「每次进页面都有」完全吻合。

### 为什么只有铃铛在报错

这个部署的 2.0 面基本不鉴权，所以别的请求根本不会暴露坏掉的会话：

- `/api/topics/{id}/blocks` **没有任何鉴权依赖**（实测：不带令牌是 404 而不是 401）。
- `/api/projects` 未认证时走 `list_all()` 分支（`routes/projects.py:103-111`），
  返回**全站项目**。

铃铛是这个页面上极少数真的走 `require_auth_user` 的请求 —— 看着像「只有铃铛坏了」，
其实是「只有铃铛在说实话」。**旁证**：截图里 12 个项目、4 个都是「机」字方块，
正是未认证分支 `list_all()` 的样子；真登录用户走的是 `list_visible_to`。

### 改动

`frontend/src/services/account.ts`：新增 `isTokenExpired()`（解 JWT 的 `exp`，
带 30 秒余量，解不开一律当过期），`init()` 改成——令牌已过期就先用 httpOnly 的
`REFRESH_TOKEN` cookie 续签，**成功了才** `loggedIn = true`；续签也失败就登出，
不再顶着一个假登录态把 401 一路撒进 console。

消费方不用改：`AppBar.vue` 和 `useNotifications.ts` 本来就 `watch(loggedIn)`，
loggedIn 翻 true 时会自己重取一次，那时手里一定是新令牌。

测试：`account.init.spec.ts`（8 条，已负向验证 3 条真的依赖这次改动）+
`test_notifications_unread_count_auth.py`（4 条，把「401 = 凭证问题不是路由问题」
钉成可执行凭据）。

---

## ③ blocks 间歇截断 —— 定位结论（未动生产配置）

### 先纠正两个前提

1. **`deploy/gateway/` 不是 HTTP 网关**，是 LiteLLM 的模型网关（`config.yaml` 里
   是 glm-4.6 / deepseek 的路由和计价）。查 proxy buffer 查不到东西。
2. 真正的 HTTP 反代链路是：
   **浏览器 → APISIX（ghg 边缘，在另一台机器上，不在本仓）→ 前端容器 nginx :8080
   → 后端 :8081**（`docs/infrastructure.md:42-44`）。前端 nginx 的配置在
   `frontend/nginx.conf`，APISIX 那一跳我们在这个仓里看不到也改不了。

### 临时目录那条线可以排除

`location /api/` 里是 `proxy_buffering off`。buffering 关掉时 nginx 根本不落临时
文件，`proxy_max_temp_file_size` / 临时目录权限那一类经典故障在这条路径上不成立。
（磁盘也有 153G 空闲。）

另外 `gzip on` 但**没有配 `gzip_proxied`**（默认 off），所以反代回来的 JSON 不压缩，
Content-Length 是后端原样声明的 —— 这也解释了浏览器为什么能拿到一个确定的
Content-Length 去比对，从而报 `ERR_CONTENT_LENGTH_MISMATCH` 而不是别的错。

### 最强解释：dev 的自动重部署窗口

`proxy_buffering off` 的代价是：nginx 先把上游的响应头（含 Content-Length）直接
转给浏览器，再流式转发 body。**这时上游一旦断开，浏览器拿到的就是一个「声明了
269KB、实际收到一半」的响应**，也就是 `ERR_CONTENT_LENGTH_MISMATCH`；如果开着
buffering，nginx 会攒完再发，同样的故障会变成一个干净的 502。

而 dev 是**合并到 main 就自动重部署**，`deploy/deploy-docker.sh:393` 是
`dc up -d backend frontend` —— 没有排空、没有蓝绿，backend 和 frontend 两个容器
都会被重建，中间有一个实打实的停机窗口。**今天（2026-08-11）这个 workflow 跑了
25 次**，平均半小时一次。

这一条把证据全部对上了：

| 现象 | 对应 |
|---|---|
| 74ms / 53ms 就 "Failed to fetch" | 连接被关，不是超时 |
| limit 10→400 全部 200 | 窗口过去了就好 |
| 同一话题连着三次失败 | 重试都落在同一个窗口里 |
| 504 同时打在 `/blocks?limit=50`、`/topic-unread`、`/topics?project_id=` | **不同路由同时挂 = 共用的后端不在，不是某条路由的 bug**；而且 nginx 的 `proxy_read_timeout` 是 3600s，这个 504 只可能来自连不上上游 |
| 「不是大小阈值，是间歇性」 | 与请求内容无关，只与时间窗口有关 |

### 加了什么观测（只加日志，没改行为）

要把这个结论钉死，缺的是「截断发生时后端那侧是什么状态」。原来的 `req` 日志行是在
handler 返回时打的，**body 还没上线**，所以截断的请求在后端日志里长得和正常的 200
一模一样 —— 这正是它没法定位的原因。

- `app/core/obs.py` 新增 `ResponseIntegrityAudit`（纯 ASGI，挂在最外层，紧贴
  transport）。健康响应完全不出声；只在两种情况告警：
  - `response truncated`：实际发出的字节数 ≠ 声明的 Content-Length（带
    `declared` / `sent` / `missing`）。**说明切口在我们这一侧。**
  - `response aborted mid-body`：响应头已发出、写 body 时抛异常。**连接在我们
    写的过程中死了。**
  没有 Content-Length 的流式/SSE/WS 不审计。
- `app/main.py` 的 `req` 行补上 `bytes=`（声明值）。

**判读方式**：截断复现时，如果后端日志里有这两条 warning，切口在后端/容器；
如果一条都没有、`req` 也显示完整的 200，那 body 是完整离开进程的，切口在
nginx ⇄ APISIX ⇄ 浏览器之间——那就该去看 APISIX 那一跳。

测试：`tests/unit/test_response_integrity_audit.py`（7 条）。

### 建议（等你确认了再动）

1. **先验证**：把你那批 console 的时间戳和当天 `Deploy (dev/test box)` 的运行时间
   对一下。对得上，③ 就结案了，是部署窗口，不是代码 bug。
2. 真要缓解，方向是**部署侧**不是路由侧：给 backend 做排空/蓝绿（仓里已经有
   `deploy/deploy-blue-green.sh`），或者让前端对 GET 的截断也走
   `RETRYABLE_GET_STATUSES` 那套重试。
3. `frontend/nginx.conf` 的 `location /api/` 还无条件下发
   `proxy_set_header Connection "upgrade"`（`$http_upgrade` 为空时这是个畸形头）。
   和这次的截断未必同源，但该修 —— 标准写法是用 `map $http_upgrade $connection_upgrade`。

---

## Rebase 到最新 main（2026-08-13）

前两轮都被平台中断（05:09 部署重启、07:16 沙箱没起来），期间 main 往前走了
**107 个提交**，其中 **10 个动过我改的那 4 个文件**。在旧基线上递卡会静默回退别人
的改动，所以先 `jj rebase -s <change> -d main@upstream`。

- `RailItem.vue`：main 上没人动过，无风险。
- `obs.py`（被 #300 动过）、`account.ts`（被 #306 动过）：jj 自动合上，无冲突。
- `main.py`：**真冲突，2 处**，人工解的。

`main.py` 第二处冲突值得记一笔：main 上 #283 新加的 `report_unhandled_to_room`
也声明自己"注册在最后 = 最外层用户中间件"，和 `ResponseIntegrityAudit` 抢同一个
位置。按语义分层解——

- **审计必须在最外层**。`@app.middleware("http")` 建的是 `BaseHTTPMiddleware`，
  套在它里面数到的是内层 app 的消息，而那个数字在响应被截断时恰恰是"健康"的，
  等于这个中间件白加了。
- **异常上报不需要在最外层**，只要在路由外层。审计只数字节、`raise` 原样抛出，
  所以每一个未处理异常照样到得了它。

顺带把 #283 那段 docstring 里"最外层"的说法改准了（现在写明只有审计在它外面，
且不影响它收异常）——这是改了别人的注释，采纳时值得看一眼。

Rebase 后 `jj git export` 已确认 git 侧 `topic/517c0465` 指向重排后的提交，
不是空提交（防「空 PR」）。

---

## 本地验证（2026-08-13 rebase 后重跑）

| 检查 | 结果 |
|---|---|
| `ruff check` + `ruff format --check` | 通过，715 个文件已格式化 |
| `pyright app` | **0 errors** |
| `pnpm run typecheck`（ratchet） | **0 errors**（基线容许 31） |
| `pnpm run lint` | 0 errors / 287 warnings |
| 本次新增前端用例 | **12 passed** |
| 后端全量 `pytest tests/ -n 4` | **4507 passed / 31 skipped / 23 failed** |

23 条失败全是沙箱缺件（`FileNotFoundError: 'kill'` —— procps / openssh-client 都
不在，集中在 `test_machine_service.py` + `test_tmux_control.py`），没有一条来自本次
改动。上一轮那条 `test_cheese_cli` 的环境泄漏在新 main 上已经不复现。

---

## 本地验证（2026-08-12 首次，旧基线 fe949e17）

上一轮在中途断了，这一轮把全部检查在干净的容器里重跑了一遍：

| 检查 | 结果 |
|---|---|
| `ruff check` + `ruff format --check`（app + tests） | 通过，653 个文件已格式化 |
| `pyright app` | **0 errors** |
| `vue-tsc --noEmit`（`--max-old-space-size=1500`） | **0 errors** |
| `pnpm run lint` | 0 errors / 287 warnings（基线 288，不拦） |
| 本次新增前端用例（`RailItem.accessibleName` + `account.init`） | **12 passed** |
| 本次新增后端用例（`test_response_integrity_audit` + `test_notifications_unread_count_auth`） | **12 passed** |
| 后端全量 `pytest tests/ -n 4` | **4044 passed / 31 skipped / 23 failed**，失败全部与本次改动无关，见下 |

`ResponseIntegrityAudit` 是挂在最外层的全局中间件，所以全量套件是必须的——4044
条通过说明它没有把任何正常响应误判或改坏。

23 条失败的归属：

- **22 条** 是已知的沙箱缺 procps（`test_machine_service.py` 21 条 +
  `test_tmux_control.py` 1 条，`FileNotFoundError: 'kill'`）。CLAUDE.md 已记。
- **1 条新的**，也不是本次改动引入的：
  `test_cheese_cli.py::test_await_log_lives_outside_the_worktree`。
  它只 monkeypatch 了 `HOME`，而 `_await_log_path()`（`backend/sandbox/cheese:219`）
  **优先读 `CHEESE_AWAIT_LOGS`**，容器里这个变量恰好被设成
  `/home/node/.claude/cheese-await`，于是断言必红。CI 不设这个变量所以是绿的——
  是测试的环境泄漏，不是产品缺陷，修法是在测试里一并 `delenv CHEESE_AWAIT_LOGS`。
  超出本话题范围，没有顺手改。

---

## 沙箱环境备忘

- 后端 venv 之前是坏的（`openai._models` 缺失，容器重建打断了同步），`uv sync
  --all-groups` 修不好 pyright，要 `uv sync --group dev --reinstall-package pyright`。
  **容器重建后 pyright 会整个消失**（`Failed to spawn: pyright`），同一条命令能装回来。
- **pnpm 也会随容器重建消失**（`pnpm: command not found`），要重跑
  `mkdir -p $HOME/.local/bin && corepack enable --install-directory $HOME/.local/bin`。
- 上一轮 OOM 留下了 **1.5 GiB 的 core dump**（`backend/core.*`、`frontend/core.*`），
  躺在 `jj status` 的未跟踪列表里，已删。`vue-tsc` 被 OOM kill 时就会掉一个，
  看见了直接删——它们绝不能进提交。
- 容器内存上限 **2 GiB**（`/sys/fs/cgroup/memory.max`）。`vue-tsc` 用默认堆会被
  OOM kill（exit 137），要 `node --max-old-space-size=1500
  node_modules/vue-tsc/bin/vue-tsc.js --noEmit` 才跑得完。
- `corepack enable --install-directory $HOME/.local/bin` 之前得先 `mkdir -p` 那个目录。

