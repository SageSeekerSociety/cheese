---
title: 目标架构
kind: 概念
summary: 后端和前端要长成什么样：分几层、领域包按什么方向依赖、变更怎么推到浏览器、后台工作和租约怎么领取回收、前端数据怎么取怎么合并；每一项给出选过的方案和取舍、迁移顺序和能被机器验证的完成标准。
covers:
  - backend/app/
  - backend/.importlinter
  - backend/scripts/check_deferred_imports.py
  - backend/app/domain/agent/runtime.py
  - backend/app/core/ownership.py
  - backend/app/core/background.py
  - backend/app/api/routes/notifications_live.py
  - frontend/src/
  - .claude/rules/architecture.md
---

# 目标架构 {#architecture}

这一页定后端和前端的终点：分几层、领域包按什么方向依赖、变更怎么到浏览器、后台工作怎么领取和回收、前端数据怎么取和合并。每一项都按最长远的方案定，实施拆成多步，每步一个能单独合并的 PR。

> 讲：目标形状、为什么选它不选别的、先做哪步后做哪步、做到什么程度算完。不讲：每个数今天怎么量（见[架构指标](/dev/arch-metrics#metrics)），三道闸门各自的规则（仓库里的 `.claude/rules/architecture.md`），某一个领域内部的业务（各域自己那一页）。

## 一屏看完 {#summary}

六条线，每条都有一个终点和一道只许变好的检查。数字是 2026-10-07 `main@3d9039c7` 上量的。

| 线 | 今天 | 终点 | 看住它的检查 |
|---|---|---|---|
| [领域分层](#backend) | 72 个领域包里 40 个在同一个环里，双向依赖 46 对 | 五层，跨层只向下；同层的依赖显式登记且无环；向上只走事件 | `.importlinter` 的 C1–C3，加一条分层契约 |
| [函数内导入](#deferred-imports) | 850 条，绝大多数没说为什么 | 没注明原因的为 0 | `backend/scripts/check_deferred_imports.py` |
| [变更推送](#realtime) | 29 处手写 `announce_stale`，只发「某类资源变了」，断线不补 | 同一事务写变更日志，带序号推整行，断线按游标补发 | 「不许新增 `announce_stale`」守卫，之后是「面板读到的模型都有变更映射」 |
| [后台工作与租约](#work) | 一把全局归属锁下跑 29 个周期任务；工作租约藏在 JSON 列里 | 工作和调度都是表里的行，任何副本按条件 SQL 领取；不再需要全局归属锁 | 租约读写只经一个模块；周期任务逐个迁出的清单 |
| [前端数据层](#frontend-data) | 四份手写缓存、两套请求栈、30 秒轮询 | 一个 QueryClient，按变更帧就地合并；一套由 OpenAPI 生成的客户端 | 缓存、轮询、请求栈各一道「只许减少」的守卫 |
| [前端组件分级](#frontend-components) | D 级 98 个，场景 debt 77 个，组件边界违规 24 处 | `components/` 下没有 C、D；debt 和边界违规为 0 | `lint:scenes`、`lint:boundary` |

## 后端：五层，只向下依赖 {#backend}

后端的终点是五层领域加两层技术底座，一个包只能依赖比它低的层；同一层里的依赖要登记、不能成环；低层要知道高层发生了什么，只能订阅事件。

今天的问题不在目录，在依赖方向。`app/domain` 下 72 个包有 395 条跨包边，其中 40 个包落在同一个强连通分量里（2026-08 量的时候是 42 个包里的 26 个）。按边的种类剔除做反事实：只经 `models` 的边全部去掉，环里还剩 34 个包；只留经 `services` 和具体模块的边，还剩 33 个。所以环不是 ORM 关系一家造成的，**是业务调用本身在双向走**：`agent` 依赖 31 个包，`project` 被 25 个包依赖，它们又互相依赖。[领域包解环](https://github.com/SageSeekerSociety/cheese/blob/main/docs/topics/领域包解环.md)早就量过：把 repository 调用改成 service 调用，包级的边一条不少。解环只能去掉依赖本身。

### 分层 {#backend-tiers}

从下往上：

```
api            路由、WebSocket 端点：解析、鉴权声明、调用领域的公开面、套信封
──────────────────────────────────────────────────────────────────────────
6 报表与查询    platform_stats、feature_stats、dashboard、project_progress、search
                只读；可以读任何下层的查询面；没有任何包依赖它
5 编排与触达    notification、delivery、routine、scheduler、task_quiet
                订阅下层事件，决定通知谁、什么时候唤起谁；调用 4 和 3 的命令
4 智能体执行    agent 拆出的 conversation / turns / live / devices / harness /
                gateway / preview / forge，agent_session、agent_instance、machine、
                device、local_fs、shell、site、remote_mcp、integration、repository
3 协作内容      block、thread、pin、mentions、room_task、task、review、
                living_doc、documents、library、memory、feedback、space 及题目相关包
2 身份与归属    identity、user、team、project、topic、membership、topic_membership、
                authz、policy、invite、passkey、oauth
1 记账          usage、run_record、idempotency、ratchet：任何层都能写，它们不依赖领域
──────────────────────────────────────────────────────────────────────────
core / db      配置、错误、Redis、数据库会话、归属锁；不认识任何领域
```

最关键的一条：**3 不依赖 4**。今天 `review`、`task`、`room_task` 会直接调用 `agent` 去起一轮、发一条提示；目标是它们只写下「任务已创建」「采纳卡已通过」这类事件，由第 4、5 层订阅后决定唤起谁。反方向（第 4 层的一轮结束后往房间里写一条消息、改一张卡）是向下调用，合法。

各包今天该放进哪一层，以「它被谁依赖、它依赖谁」为准，不以名字为准；分不清的包先放进它当前依赖最多的那一层，由分层契约的冻结名单记下越界的边，之后逐条还。

### 为什么选这个方案 {#backend-options}

| 方案 | 做法 | 结论 |
|---|---|---|
| A. 只收紧现有三条契约 | 目录不动，继续把冻结条目一条条还掉 | 不选。C3 只说「兄弟包无环」，不说谁该在上谁该在下；一条边还掉之后，常常换一个方向又长出来 |
| **B. 按依赖方向分层，层在契约里声明** | 包名和目录不动；`.importlinter` 加一条 layers 契约，按上面五层列出各包，同层用 `:` 连接表示允许互相依赖但受 C3 约束 | **选它**。方向写成了机器能判的规则；某个包解耦后换层，只改契约里的一行，不搬目录、不断 `git blame` |
| C. 按层建目录（`domain/kernel/user/`…） | 把包移进层目录 | 不选。层的归属会随解耦变化，每变一次就是一次全仓改 import；目录表达不了「同层允许、但要无环」 |
| D. 拆成多个服务 | 按层或按域拆进程 | 不选。今天的耦合在进程内都解不开，拆进程只会把函数调用换成网络调用；部署上已经按可用性拆了四个进程（见[后端结构](/dev/backend-app#processes)），够用 |

### 一个领域包的样子 {#backend-package}

每个领域包对外只露一面，内部分读写。目标形状：

```
app/domain/<包>/
  __init__.py     对外的面：别的包和路由只能从这里 import（服务函数、DTO、事件类型）
  models.py       ORM。只有本包、alembic 和本包的测试能 import
  repository.py   本包私有的持久化
  commands.py     写：开事务、校验、写行、同一事务里记变更和事件
  queries.py      读：返回 DTO，不把 ORM 对象交出包外
  events.py       本包发布的事件
  handlers.py     本包对别的包事件的订阅
  schemas.py      DTO
```

三条规矩，后两条要新契约：

1. 路由只调领域的公开面。C2 今天只禁路由直接碰别包的 `models`，终点是连 `repository` 一起禁。
2. 跨包的 ORM `relationship()` 改成只留外键列。跨包的联表查询放进第 6 层的查询模块，或者放进拥有那张主表的包的 `queries.py`。今天只经 `models` 的跨包边有 169 条，这是解环里最大、也最机械的一块。
3. 事件在写入方的同一个事务里落表（见[变更推送](#realtime)），订阅方在提交之后处理；处理器必须幂等。Sentry 的跨域 outbox 和 Saleor 的「事务里的事件自动推迟到提交后」是同一个做法。

### `agent` 包拆成什么 {#agent-split}

`agent` 一个包 66,454 行，包着模型网关、设备链路、预览隧道、代码托管凭据和整个轮次运行时，所以它依赖 31 个包、又被 20 个包依赖。按职责拆，拆完各自进第 4 层：

| 新包 | 从 `agent/` 搬走的 | 职责 |
|---|---|---|
| `conversation`（并入现有同名包） | `chat.py` 的消息收发、`mentions`、`turn_inputs`、`turn_speakers`、`prompt`、`pending_messages` | 一条消息进来，决定谁说话、拼出这一轮的输入 |
| `turns` | `runtime.py` 的 `AgentWorkRunner`、`recovery`、`liveness`、`turn_*`、`turn_adoption` | 一轮的生命周期；运行状态写进库，不留在进程内存 |
| `live` | `runtime.py` 的 broker、`live_frames`、`live_notices`、`room_events`、`announce` | 变更日志、序号、连接扇出（见[变更推送](#realtime)） |
| `devices`（并入现有 `device`） | `device_hub*`、`device_link`、`device_provider`、`device_storage`、`machine_*`、`executor_transport` | 机器链路与远程调用 |
| `harness` | `agent/harness/` | 各 CLI 的适配 |
| `gateway` | `gateway*` | 模型网关、目录、计费 |
| `preview`（并入现有同名包） | `preview_hub`、`preview_owner`、`preview_tunnel` | 预览隧道 |
| `forge` | `github_app`、`forgejo_tokens`、`forge_cli` | 代码托管凭据和调用 |

拆的顺序按依赖：先拆没人依赖、也不依赖 `agent` 其余部分的叶子（`gateway`、`forge`、`preview`），再拆 `devices`，最后才是 `chat.py` 和 `runtime.py`。后两步要等「合并异常基类」那条任务进 main，否则两边同时大改同一批文件。

### 函数内导入 {#deferred-imports}

函数体里的 `import` 只有带着原因才合法：同一行或上一行写 `# deferred-import: <原因>`。它让依赖藏在运行时，读代码的人和多数工具都看不见；仓库里大多数是为了躲环，也有一部分只是拆文件时原样搬过来的。

- 合法的原因只有四类：打破一个还没还的环（写明环的另一端）、可选依赖、启动开销、测试替身需要在调用时取模块属性。
- 没写原因的按文件冻结在 `backend/deferred-import-baseline.json`：旧文件只许减少，新文件必须为 0。检查是 `backend/scripts/check_deferred_imports.py`，规则和用法写在 `.claude/rules/architecture.md`。
- 为躲环而写的那一类，会随着分层契约的冻结条目一起还掉：环解开，它就能上提。
- 没有直接用 ruff 的 `PLC0415`（禁止顶层之外的 import）：ruff 没有基线，接入就要在 850 处各加一个不说原因的 `noqa`。等基线降到 0，再换成 `PLC0415`，`noqa` 后面写原因。

## 变更推送：同一事务记账，按序号补发 {#realtime}

终点是：每次写入在同一个事务里记一行「什么对象变成了什么样」，提交后分到一个单调的序号，按订阅范围推给在线连接；浏览器带着游标重连，缺的从库里补，补不齐就整页重取。

今天的做法是在写入之后手工调 `announce_stale(room_id, 资源名)`（`backend/app/domain/agent/runtime.py:425`，29 处），发一帧 `{"type":"state","resource":...}`，前端整类重取。它有四个缺口：漏调就不刷新，没有任何检查；只按房间推，跨房间（侧栏、任务列表）靠前端 30 秒轮询；断线重放只缓存在跑轮次的帧（`runtime.py:159` 的 `_buffer`，每频道 512 帧，轮次结束即清）；帧里没有序号，前端判断不了自己缺了什么。`review/live.py` 已经用 SQLAlchemy 的 after_commit 自动发采纳卡的更新，`/notifications/live`（`backend/app/api/routes/notifications_live.py:84`）已经是「客户端报游标、服务端从库补发、25 秒心跳」，两者是终点的两块样板。

### 变更日志 {#change-log}

```
change_log
  id         bigserial           插入顺序，只用来排队
  seq        bigint null         可见顺序，由分配器在提交后填；游标用它
  project_id uuid
  topic_id   uuid null           订阅过滤
  entity     text                room_task | accept_card | topic | member | pin | thread | ...
  entity_id  text
  op         text                upsert | delete | invalidate
  data       jsonb null          upsert 时是整行的对外表示，和对应 GET 返回的同一个 schema
  created_at timestamptz
```

- **谁写**：ORM 的 after_flush 监听器按「模型 → 实体」映射表，在同一个事务里插入变更行，`seq` 留空。写代码的人不再记得要发什么。
- **为什么要两个号**：Postgres 的序列按 `nextval` 的先后分配，不按提交的先后。事务 A 先取到 10 后提交、事务 B 取到 11 先提交，客户端先见 11 把游标推过去，10 就永远收不到。所以游标不能是 `id`。
- **谁填 `seq`**：一个分配器，提交后被唤醒，执行 `UPDATE change_log SET seq = nextval('change_seq') WHERE id IN (SELECT id FROM change_log WHERE seq IS NULL ORDER BY id FOR UPDATE SKIP LOCKED) RETURNING *`。只有已提交的行看得见，又只有一个分配器在跑，所以 `seq` 在可见顺序上单调。分配完按 `topic_id`、`project_id` 扇出。
- **怎么到每个副本**：分配器填完 `seq` 后发一条 `NOTIFY`，只带新的最大号（Postgres 的 `NOTIFY` 本来就在提交后才投递）。每个持有连接的副本 `LISTEN`，按号从表里读，再按连接的订阅范围过滤推送。这样连接落在哪个副本都一样。
- **保留多久**：7 天。游标早于最小保留的 `seq` 时回 `resync`。

分配器要求同一时刻只有一个。三种做法：

| 做法 | 代价 | 结论 |
|---|---|---|
| 挂在现有的全局归属锁下 | 跟着归属锁一起交接；锁在就有分配器 | 第一步用它 |
| 插入时取 `pg_advisory_xact_lock` 串行 | 所有写事务在 flush 到 commit 之间排队 | 不选，写入吞吐被一把锁卡住 |
| 不要分配器，按 `xid8` 和 `pg_snapshot_xmin` 判断「比这更早的都已提交」 | 任何一个长事务（迁移、慢查询）都会卡住所有推送 | 不选，故障面太大 |
| **分配器是一个[租约行](#work)上的角色** | 要先有租约表 | **终点**：归属锁退役后，分配器和其他单例角色一样按租约领取 |

### 帧和协议 {#realtime-frames}

房间连接和用户级连接用同一种帧：

```jsonc
// 客户端连上后第一帧，形状和 /notifications/live 一致
{"after": 104233}                     // 取自最近一次 GET 的 X-Change-Seq；没有就 null
// 服务端
{"type": "hello", "seq": 104240}      // 当前头部，随后补发 (after, 104240]
{"type": "change", "seq": 104235, "entity": "room_task", "id": "…", "op": "upsert", "topic_id": "…", "data": {…}}
{"type": "change", "seq": 104236, "entity": "topic", "id": "…", "op": "delete"}
{"type": "change", "seq": 104237, "entity": "files", "id": "<topic_id>", "op": "invalidate"}
{"type": "resync", "seq": 104240}     // 游标太旧或补发超上限：前端全部失效重取
```

- **列表和详情的 GET 带 `X-Change-Seq`**：读之前的最大 `seq`。前端丢掉 `seq` 不大于快照号的帧，不需要「先暂存、后重放」的队列。
- **写请求的响应也带 `X-Change-Seq`**：前端乐观更新后，等流追上这个号再结束等待，不再补一次 GET。
- **用户级连接**：把 `/notifications/live` 扩成用户的变更流，按「这个人能看见的项目和话题」过滤。侧栏和任务列表的 30 秒轮询由它取代。
- **旧帧**：`{"type":"state","resource":R}` 在迁移期间照发，前端当成对一组查询的 `invalidate`。
- **运行中**：频道列表的「运行中」今天由 `running_topic_ids()`（`runtime.py:724`）读本进程内存。终点是轮次状态在库里，「运行中」是一个查询，它的变化也是一条变更。

| 方案 | 结论 |
|---|---|
| A. 保留「某类资源变了」的失效帧，只补上序号和补发 | 不选。每次变更仍是整类重取，房间越大越贵；也解决不了跨房间 |
| **B. 变更日志：整行 upsert 为主、`invalidate` 兜底** | **选它**。参照 Linear 同步引擎的整行 delta 加全局序号、Zulip 的「补不齐就整页重载」、我们自己 `/notifications/live` 的游标补发 |
| C. 逻辑复制 / CDC（读 WAL 推变更） | 不选。按行做权限过滤要在复制流之外重写一遍，运维面多一个常驻组件 |

## 后台工作与租约：表里的行，按条件领取 {#work}

终点是：每一件后台工作和每一个需要独占的角色，都是表里的一行，带领取者和到期时间；任何一个后端副本都可以用一条带条件的 SQL 领取、续期、交还，死掉的领取者由到期时间自然放手。全局归属锁退役。

今天有两套机制：

- **全局归属锁**（`backend/app/core/ownership.py`）：一条 Postgres 会话级 advisory lock，持锁的进程跑 `core/background.py` 里全部 29 个周期任务，并持有会话订阅这类进程内存状态。它简单可靠，但所有后台工作挤在一个进程里，发版交接期间全停，哪件工作卡住了从库里看不出来。
- **工作租约**（`agent_sessions.work_lease`，一个 JSON 列）：预约、续期、完成各写一遍 JSON，读写散在 15 个文件里（`session_work.py` 一处就 15 次）。领取的条件写在 Python 里，不在 SQL 里，所以「两个进程同时判断它过期了」只能靠先锁行。

### 两张表 {#work-tables}

```
work_items                          一件要做的事
  id, kind, payload jsonb, dedupe_key（部分唯一索引）
  state       queued | running | succeeded | failed | cancelled
  run_at, attempt, max_attempts, last_error
  lease_owner, lease_until          领取者和到期时间

leases                              一个要独占的东西：会话的工作机器、分配器、某个周期任务
  name (PK)                         如 session:<id>、role:change-sequencer、schedule:pr-poll
  owner, token, generation
  lease_until, data jsonb
```

领取、续期、交还都是一条语句：

```sql
-- 领取：没人拿着，或者上一个已经过期
UPDATE leases SET owner=:me, token=:t, generation=generation+1, lease_until=now()+:ttl
 WHERE name=:n AND (owner IS NULL OR lease_until < now()) RETURNING generation;
-- 续期、完成：只认自己那张票
UPDATE leases SET lease_until=now()+:ttl WHERE name=:n AND token=:t;
-- 取一件工作
UPDATE work_items SET state='running', lease_owner=:me, lease_until=now()+:ttl, attempt=attempt+1
 WHERE id = (SELECT id FROM work_items WHERE state='queued' AND run_at<=now()
             ORDER BY run_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *;
```

- **判活靠领取者的心跳，不靠工作该跑多久**：`lease_until` 由持有者每隔几秒续一次，续不上才算死。不要把「一次安装最多 660 秒」这类时长当成 TTL：multica 的注释记过纯按时长判死误杀正常任务（MUL-6558）。
- **执行状态和机器状态是两条状态机**：一轮在跑不在跑，和它那台机器在不在，分开存、分开推进。OpenHands 把两者合在一起时出过「沙箱停了，会话还卡在运行中」（software-agent-sdk#4893）。
- 会话的工作租约从 JSON 列搬进 `leases`（`name = session:<id>`），机器、目录这些装好之后的信息放进 `data`。读写只经一个模块。
- 周期任务变成 `schedule:<名字>` 的租约加 `work_items`：到点的那个副本领到租约，排一件工作，排完把 `run_at` 往后推。每个处理器必须幂等，因为领取者可能在做到一半时死掉，工作会被另一个副本重做。
- 进程内存里的会话订阅，改成每个会话一张租约；哪个进程拿着，哪个进程订阅。最后一项迁完，全局归属锁就没有东西要保护了。

| 方案 | 结论 |
|---|---|
| A. 继续用全局归属锁 | 不选作终点。它把「谁在做」变成「哪个进程活着」，做到一半的工作没有痕迹 |
| **B. Postgres 里的工作表和租约表** | **选它**。不加基础设施；排工作可以和业务写入在同一个事务里，不会出现「业务写了、工作没排上」；状态能直接查。multica 的任务表（`FOR UPDATE SKIP LOCKED` 领取、租约是行上的列、每个副本都跑清扫）、River、Oban、procrastinate 都是这个形状 |
| C. Celery 加 Redis（dify 的做法） | 不选。排工作和业务写入分在两个系统里，要么丢工作要么重复；定时器 beat 只能起一个实例，单点没有消失；多一个要部署和监控的组件 |

是否直接采用 procrastinate 这类现成库，在第一步动手前评估：它的表结构和我们的租约表能不能共存、异步驱动和我们的 asyncpg 能不能共用连接池。

## 前端：一个查询缓存，按变更合并 {#frontend}

前端的终点是：服务端数据只经一个查询缓存（`@tanstack/vue-query`）取和存，变更帧按实体就地合并进去；请求只走一套由 OpenAPI 生成的客户端；页面读路由、调组合函数，组件只吃 props 和事件。

### 目录 {#frontend-tree}

```
frontend/src/
  api/          由 /api/openapi.json 生成的类型和客户端，按资源分文件；唯一的请求栈
  query/        QueryClient、查询键表、变更帧的应用（applyChange）、各资源的 queryOptions
  features/<域>/ 该域的组合函数（包 useQuery / useMutation）、乐观更新、域内客户端状态
  views/        路由容器：读路由、调 features、渲染同目录的 <页面>View.vue
  components/   只吃 props 和事件的组件（含各 *View.vue）
  layouts/ router/
  lib/          纯函数，不 import 上面任何一层
  stores/       只放客户端状态：外壳、草稿、偏好
```

今天和终点的差：`src/api.ts`（2,779 行、270 个导出，fetch 一套）和 `src/network/`（axios 一套，14 个资源）两套请求栈并存；`services/` 里还有账户和推送；`stores/feedback.ts`（1,708 行）把服务端状态和客户端状态混在一起。

### 数据层 {#frontend-data}

「先给旧数据、背后重取」今天手写了四份：`lib/pageCache.ts`、`composables/useCachedResource.ts`、`lib/topicPanelCache.ts`、`lib/blockCache.ts`。它们各自做了 vue-query 的一部分（同键去重、保留旧数据、作废），都没有按前缀失效、只重取在看的、结构共享。终点：

- **一个 QueryClient**：退出登录 `clear()`；`useCachedResource` 先改成 vue-query 上的薄壳，调用方不动。
- **不可变更新**：今天有页面就地改取回来的对象（`useCachedResource.ts:44` 为此用了深层 ref）。改成 `setQueryData(key, old => 新值)`。这也是「推送回来的数据把本地改动盖掉」这类问题的根。
- **变更帧进缓存**：一张「实体 → 查询键」表，`upsert` / `delete` 就地改对应列表，`invalidate` 和不认识的实体按键前缀失效，不去猜。WebSocket 重连时带游标，收到 `resync` 全部失效。
- **轮询退场**：`ProjectShell.vue` 和 `ProjectSidebar.vue` 的 30 秒轮询在用户级变更流上线后删掉。
- **聊天时间线不迁**：`blockCache` 本来就是带分页窗口的增量合并，`useInfiniteQuery` 给不了更多。

| 方案 | 结论 |
|---|---|
| A. 继续手写，把四份合成一份 | 不选。合出来就是第五份，按前缀失效、结构共享、只重取在看的都要再写一遍 |
| **B. 采用 @tanstack/vue-query** | **选它**。它只管服务端状态，和 pinia 不冲突；结构共享让「整块重取回来只变一行」时其余子树保持同一个引用 |
| C. Linear 式本地对象池加 IndexedDB | 不选。全量模型注册和离线存储对我们太重；只借它的三样：整行 delta、快照带序号、写响应带序号 |

### 请求栈 {#frontend-api}

终点是一套由后端 OpenAPI 生成的类型化客户端，按资源分文件。后端已经在 `/api/openapi.json` 发出完整描述（`servers[0].url` 就是 `/api`），手写的 `api.ts` 和 `network/api/*` 和它之间没有任何检查，改了后端字段前端照样编译通过。生成之后，字段对不上在类型检查里就红。迁移按资源一组一组换，换完一组就从 `api.ts` 删一组；`network/` 的拦截器（鉴权、刷新令牌、错误上报）先搬进新客户端的中间件。

### 组件分级 {#frontend-components}

`components/` 下的终点是全部 A 级：只吃 props 和事件。读路由、取数、读业务 store 的只能是 `views/` 下的容器页和 `features/` 下的组合函数。判据和闸门已经有了（[场景清单](/dev/scenes#standalone)、`lint:boundary`），这里只定终点：场景 debt 为 0，组件边界违规为 0，`components/` 下 C、D 级为 0。

## 迁移顺序 {#migration}

每一步是一个能单独合并的 PR，前一步不合，后一步不开。同一行里的几步互不依赖，可以并行。

| 步 | 内容 | 依赖 |
|---|---|---|
| 1 | 本页；清掉导入契约里失效的冻结条目；`api/routes/users/` 和 `users_password.py` 的函数内导入上提；函数内导入的棘轮；前端分级脚本不再把注释里的 `vue-router` 算成读路由；几处组件不再自己取数或读路由 | 无 |
| 2a | 分层契约：按[五层](#backend-tiers)写进 `.importlinter`，现有越界的边冻结 | 1 |
| 2b | 「不许新增 `announce_stale` 调用点」「不许新增读进程内存运行态」两道守卫 | 1 |
| 2c | 前端装 vue-query，`useCachedResource` 换成薄壳；就地改数据的调用方改成 `setQueryData` | 1 |
| 3a | `agent` 拆出 `gateway`、`forge`、`preview` | 2a |
| 3b | `change_log`、ORM 监听、分配器（挂在归属锁下）、`after` / `hello` / `resync` 协议，GET 和写响应带 `X-Change-Seq`；先给房间连接 | 2b |
| 3c | `topicPanelCache` 换成 queryOptions；旧 `state` 帧改成按键失效 | 2c |
| 4a | `agent` 拆出 `devices`；等异常基类任务进 main 后，拆 `chat.py` 和 `runtime.py` | 3a |
| 4b | 用户级变更流；删掉侧栏 30 秒轮询；「运行中」改从库里算 | 3b |
| 4c | 热点实体（房间任务、采纳卡、话题列表、成员）推整行 upsert，前端就地合并 | 3b、3c |
| 5a | `leases` 表；会话工作租约从 JSON 列搬过去 | 1 |
| 5b | `work_items` 表；周期任务逐个迁出全局归属锁，每个一个 PR | 5a |
| 5c | 分配器和会话订阅改成租约角色；全局归属锁退役 | 3b、5b |
| 6 | 29 个 `announce_stale` 逐个换成 ORM 映射；守卫变成「面板读到的模型都有映射」 | 3b |
| 7 | 生成的 API 客户端，按资源替换 `api.ts` 和 `network/` | 2c |
| 8 | 跨包 `relationship()` 改外键列，按包还掉分层契约的冻结条目 | 2a |

## 完成标准 {#done}

每一条都是一个能跑出来的数或一道会红的检查。行数下降和测试通过本身不算架构变好：拆文件不改依赖方向，包级依赖图一条边都不会少。

| 线 | 算完的条件 | 怎么验 |
|---|---|---|
| 领域分层 | 分层契约和 C3 的冻结条目都是 0；领域包之间没有强连通分量 | `cd backend && uv run lint-imports`；看板上的强连通分量大小 |
| 函数内导入 | 基线文件里每个文件都是 0 | `uv run python scripts/check_deferred_imports.py` |
| 路由边界 | C2 的冻结条目是 0，且 C2 扩到 `repository` 后仍为 0 | `lint-imports` |
| 变更推送 | 后端没有 `announce_stale`；面板读到的每个模型在映射表里；断线重连后不需要整页刷新就能对齐 | 守卫测试；一条端到端测试：断网期间改数据，重连后列表一致 |
| 运行态 | 没有任何请求从进程内存回答「谁在跑」 | 守卫测试；起两个后端进程，任一个答「运行中」结果相同 |
| 后台工作 | `core/background.py` 的周期任务清单为空；`core/ownership.py` 删除 | 文件不存在；部署时两个副本同时跑，没有重复副作用 |
| 前端数据层 | 没有手写缓存和轮询；只有一套请求栈 | 守卫：`setInterval` 取数、`network/` 目录、`api.ts` 都不存在 |
| 组件分级 | `components/` 下 C、D 为 0；场景 debt 和组件边界违规为 0 | `pnpm --dir frontend run lint:scenes`、`lint:boundary`；`arch-metrics.py` |
| 文件大小 | 前后端都没有超上限的文件 | `.claude/scripts/check-file-sizes.py` 对全树 |

## 和其他工作的边界 {#boundaries}

- 路由怎么声明写权限、怎么守，由「写权限闸门」那条任务定；本页的 `api` 层只要求路由调领域的公开面。
- `chat.py`、`runtime.py` 的拆分等「合并异常基类」进 main 之后再动。
- 场景拆分里已经有人在做的（spaces、workspace 和杂项三批），不在本页的迁移步骤里重复。
