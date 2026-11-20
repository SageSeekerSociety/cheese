# 页面打开与核心交互耗时测量

量「点开一个页面（或发起一次发送操作），多久之后目标内容真实绘制在屏幕上且关键控件可操作」，并给出可归因的分段与原始样本。只读运行中的前端，不改产品代码。

## 三种缓存与导航口径

三种模式在浏览器上下文、HTTP 缓存和 Service Worker 策略上严格隔离，不能混为一个「打开耗时」：

| 模式 | 规范名称 | 浏览器上下文与存储 | HTTP 缓存 | Service Worker | 适用场景 |
| --- | --- | --- | --- | --- | --- |
| `cold` | `true_cold` | 每轮新建独立 `BrowserContext`，只注入登录令牌（不带查询缓存或 IndexedDB） | CDP `Network.setCacheDisabled(true)` 禁用 | `service_workers="block"` 禁用 | 诊断无浏览器缓存与无 SW 时的完整网络与首屏加载成本（注：禁用 SW 不代表日常缓存体验；浏览器冷不等于服务端/数据库缓存冷） |
| `warm` | `warm_reload` | 复用已预热上下文，先打开目标页落定缓存与 SW，再同页 `reload` | 启用 | `service_workers="allow"` 启用 | 用户在已打开页面按刷新键的真实体验 |
| `walk` | `spa_walk` | 在已打开的 SPA 外壳内，从**不同于目标页**的起点路由调用 `$router.push` | 启用 | `service_workers="allow"` 启用 | 用户在应用内点击侧栏切页、切换频道/任务/文档的真实体验 |

## 完成判据与计时字段

每轮测量必须同时满足以下条件才算完成：

1. **到达目标路由**：`walk` 模式下 `$router.push` 已完成且地址已离开起点页，旧页面残留 DOM（`data-perf-stale`）不算完成。
2. **加载态消失**：页面骨架屏（`.skel`）、转圈（`.v-progress-circular`）、揭示门禁（`.reveal-gate--waiting`）及 `[aria-busy="true"]` 均已消失。
3. **关键内容可见**：`pages.py` 中该页的 `ready_selector` 存在、几何尺寸可见且文本长度达到 `min_text`（不再用静态外壳代替异步正文，如项目目标页核对 `.doc-prose` 正文而非外层 `.docs-tabs`）。
4. **关键操作可用**：若配置了 `action_selector`（如频道与任务页的输入框 `.composer-editor__input[role='textbox']`），该控件必须可见且未禁用。
5. **状态区分**：合法空态（`empty_selector`）、HTTP 403 无权限（`forbidden`）、HTTP 5xx 或错误横幅（`error`）、超时（`timeout`）与取消（`cancelled`）分别记录。默认 `allow_empty=False` 时，出现空态不会误判为正常内容完成。

每轮原始样本与汇总记录以下字段：

| 字段 | 含义 |
| --- | --- |
| `n_attempts` / `n_ok` / `n_failed` | 尝试次数、成功次数、失败次数。如 1 次成功 2 次失败，报告显式打印 `1/3 (2 失败)`；全部失败时 `ready_ms` 为 `null`（报告显示 `FAIL`），绝不报 `0 ms` |
| `dom_ready_ms` | 满足上述 DOM 完成判据的首个动画帧时刻（布局与绘制前）；在 `send-message` 操作中表示本地乐观上屏耗时 |
| `paint_ms`（即 `ready_ms`） | `dom_ready_ms` 之后下一帧真实绘制完成的时刻；在 `send-message` 操作中表示服务端 `POST /blocks` 200 确认并落定消息行的绘制时刻 |
| `stats` | 成功样本的 `min_ms`、`p50_ms`、`p75_ms`、`p95_ms`、`max_ms`（线性插值 `statistics.quantiles(..., method="inclusive")`）。当 `n_ok < 5` 时标注 `small_sample: true`，报告展示 `[min–max]` 范围，不拿小样本 `p95` 宣称胜负 |
| `critical_path_api_ms` | 在 `dom_ready_ms` 之前发出的**阻塞关键内容接口**的时间区间并集长度（并行请求取区间并集，不重复相加；`dom_ready_ms` 之后发出的后台/空闲请求归入 `background_api_timeline`，不计入关键路径） |
| `api_count` / `api_requests` | 窗口内实际发出的 `/api/` 请求总数与完整列表（保留 HTTP 方法与查询串，如 `GET spaces?pageSize=50`） |
| `duplicate_api_requests` | 同一窗口内**方法、路径与查询串完全相同**且发出超过 1 次的请求；不同查询参数的同名路径不算重复 |
| `blocking_api_timeline` / `background_api_timeline` | 每条接口请求的 `start_ms`、`ttfb_ms`、`end_ms`、`duration_ms`、`status`、`request_id`（`X-Request-ID`）与 `server_timing_app_ms`（`Server-Timing: app;dur=...`） |
| `long_task_ms` / `long_task_count` | 窗口内浏览器主线程长任务（>50ms）累计耗时与次数 |

输出 JSON 顶层 `_meta` 固定记录：构建版本（git HEAD）、实际运行版本（`/api/health`）、浏览器版本、设备 CPU/视口、网络配置、数据集参数、缓存与 SW 策略、每模式样本数、起止时间窗口及分位数算法。

## 怎么跑

1. 启动本地隔离 Postgres（`:5433`）与 Redis（`:6379`）：

       CHEESEX_DEV_DB_DIR=/tmp/perf-dev-db bash .claude/scripts/dev-db.sh start

2. 创建隔离演示库 `cheese_demo_perf` 并执行迁移与造数（`seed_demo.py` 内置主机与库名门禁，拒绝非本机或生产库）：

       PGPASSWORD=cheesex psql -h 127.0.0.1 -p 5433 -U cheesex -d postgres -c "CREATE DATABASE cheese_demo_perf;"
       cd backend && DATABASE_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_demo_perf uv run alembic upgrade head
       cd backend && DATABASE_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_demo_perf PYTHONPATH=. uv run python ../scripts/perf-open-page/seed_demo.py

3. 构建并启动生产前端预览（`:3000`，需注入 `VITE_API_BASE_URL=/api`）、文档协作服务（`:8902`）与后端（`:8081`）：

       cd frontend && pnpm run build:collab && VITE_API_BASE_URL=/api pnpm run build
       PORT=8902 COLLAB_ADDRESS=127.0.0.1 COLLAB_BACKEND_URL=http://127.0.0.1:8081 COLLAB_SECRET=perf-collab-secret node dist-collab/main.mjs &
       cd ../backend && DATABASE_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_demo_perf REDIS_URL=redis://127.0.0.1:6379/0 COLLAB_SECRET=perf-collab-secret COLLAB_INTERNAL_URL=http://127.0.0.1:8902 uv run uvicorn app.main:app --host 127.0.0.1 --port 8081 &
       cd ../frontend && BACKEND_URL=http://127.0.0.1:8081 COLLAB_URL=ws://127.0.0.1:8902 npx vite preview --host 127.0.0.1 --port 3000 --strictPort &

4. 运行受控故障与校准自检（验证关键请求延迟、后台请求延迟、部分失败保留、全失败不报 0、空态/403/500、三种缓存模式区分与查询串去重）：

       uv run --no-project --with playwright python scripts/perf-open-page/verify_calibration.py

5. 运行测量并生成报告：

       uv run --no-project --with playwright python scripts/perf-open-page/measure.py \
           --base-url http://127.0.0.1:3000 \
           --username alice --password demo12345 \
           --runs 3 --modes walk,warm,cold --out scripts/perf-open-page/measurements.json
       python scripts/perf-open-page/report.py scripts/perf-open-page/measurements.json --mode walk
       python scripts/perf-open-page/report.py scripts/perf-open-page/measurements.json --mode warm
       python scripts/perf-open-page/report.py scripts/perf-open-page/measurements.json --mode cold

6. **Run controlled fault calibration & interleaved A/B verification**

       # Build Before (origin/main) and After (working tree) bundles for interleaved A/B:
       git worktree add --detach /tmp/before-wt origin/main
       ln -s "$(pwd)/frontend/node_modules" /tmp/before-wt/frontend/node_modules
       (cd /tmp/before-wt/frontend && VITE_API_BASE_URL=/api ./node_modules/.bin/vite build)
       cp -r /tmp/before-wt/frontend/dist /tmp/dist-before && git worktree remove --force /tmp/before-wt
       (cd frontend && VITE_API_BASE_URL=/api ./node_modules/.bin/vite build && cp -r dist /tmp/dist-after)

       uv run --no-project --with playwright python scripts/perf-open-page/verify_calibration.py \
           --base-url http://127.0.0.1:3000
       uv run --no-project --with playwright python scripts/perf-open-page/verify_phase2.py \
           --base-url http://127.0.0.1:3000 --rounds 3 \
           --before-commit a9385f9c7153 --after-commit 55f77bce9c21
       backend/.venv/bin/pytest scripts/perf-open-page/test_calibration.py
