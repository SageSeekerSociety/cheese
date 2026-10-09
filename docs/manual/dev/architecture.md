---
title: 目标架构
kind: 概念
summary: 后端和前端要长成什么样：领域包分几层、按什么方向依赖，变更怎么推到浏览器，工作租约和后台任务怎么领取回收，前端数据怎么取怎么合并；每一项给出选过的方案和取舍、迁移顺序和能被机器验证的完成标准。
covers:
  - backend/app/
  - backend/.importlinter
  - backend/scripts/check_deferred_imports.py
  - backend/app/domain/agent/runtime.py
  - backend/app/domain/machine/session_work.py
  - backend/app/domain/machine/lease_claim.py
  - backend/app/core/ownership.py
  - backend/app/core/background.py
  - backend/app/api/routes/notifications_live.py
  - frontend/src/
  - .claude/rules/architecture.md
---

# 目标架构 {#architecture}

这一页定后端和前端的终点：领域包分几层、按什么方向依赖，变更怎么到浏览器，工作租约和后台任务怎么领取和回收，前端数据怎么取和合并。每一项都按最长远的方案定，实施拆成多步，每步一个能单独合并的 PR。

> 讲：目标形状、为什么选它不选别的、先做哪步后做哪步、做到什么程度算完。不讲：每个数今天怎么量（见[架构指标](/dev/arch-metrics#metrics)），三道闸门各自的规则（仓库里的 `.claude/rules/architecture.md`），某一个领域内部的业务（各域自己那一页）。

## 一屏看完 {#summary}

六条线，每条都有一个终点和一道只许变好的检查。「今天」一列是 2026-10-07 在 `main@3d9039c7` 上量的。

| 线 | 今天 | 终点 | 看住它的检查 |
|---|---|---|---|
| [领域分层](#backend) | 72 个领域包有 395 条跨包边，40 个包在同一个环里，双向依赖 46 对 | 七层，跨层只向下；同层无环；「让高层去做事」只走事件 | `.importlinter` 的 C1–C3，加一条分层契约 |
| [函数内导入](#deferred-imports) | 850 条，没有一条写明原因 | 没写原因的为 0 | `backend/scripts/check_deferred_imports.py` |
| [变更推送](#realtime) | 写入后手写通知（`announce_stale`、`tell_project`），只说哪样变了，前端重读；漏写没有检查 | 写入钩子按一张映射表在提交后推，没有手写的通知 | 「不许新增 `announce_stale`」守卫，之后是「面板读到的模型都有变更映射」 |
| [工作租约与后台任务](#work) | 工作租约是一个 JSON 列，按各进程自己的时钟判过期；一把全局锁下跑 29 个周期任务 | 租约是一张表，四条带条件的 SQL 领取回收；周期任务按任务各自租；全局锁最后退役 | 租约写入只经一个模块；周期任务逐个迁出的清单 |
| [前端数据层](#frontend-data) | 四份手写缓存、两套请求栈 | 一个查询缓存，推送说哪样变了就重读哪样；请求类型由后端声明生成 | 缓存、轮询、请求栈各一道「只许减少」的守卫 |
| [前端组件分级](#frontend-components) | D 级 98 个（其中 5 个只因注释里写了 `vue-router` 被误判，第 1 步已修正），场景 debt 77 个，组件边界违规 24 处 | `components/` 下没有 C、D；debt 和边界违规为 0 | `lint:scenes`、`lint:boundary` |

## 后端：七层，只向下依赖 {#backend}

后端的终点是七层领域，一个包只能依赖比它低的层；同一层里的依赖不能成环；低层要让高层去做一件事，只能发事件。

今天的问题在依赖方向，不在目录。`app/domain` 下 72 个包有 395 条跨包边，40 个包落在同一个强连通分量里（2026-08 量的时候是 42 个包里的 26 个）。把只经 `models` 的 69 条边全部去掉，环里还剩 34 个包，所以环不是 ORM 关系一家造成的，业务调用本身在双向走：`agent` 依赖 31 个包，`project` 被 25 个包依赖，它们又互相依赖。[领域包解环](https://github.com/SageSeekerSociety/cheese/blob/main/docs/topics/领域包解环.md)量过，把 repository 调用改成 service 调用，包级的边一条不少。解环只能去掉依赖本身。

### 分层 {#backend-tiers}

层的归属按实测的依赖定，不按包名猜。下表按 2026-10-07 的依赖图排：

| 层 | 是什么 | 包 |
|---|---|---|
| 1 底座 | 没有业务判断、被所有人用的记录和工具 | `common`、`conversation`、`idempotency`、`run_record`、`attachment`、`avatars`、`textfile`、`service_keys`、`backend_log`、`frontend_log`、`tag`、`legal` |
| 2 身份与归属 | 谁、属于哪个团队和项目、在哪个话题里、能做什么 | `identity`、`user`、`team`、`project`、`topic`、`membership`、`topic_membership`、`authz`、`policy`、`invite`、`passkey`、`oauth`、`agent_type`、`agent_credential`、`ratchet` |
| 3 平台服务 | 被业务同步调用的能力：代码托管、模型单次调用、额度与计量、通知收件箱、投递账本 | `forge`（新）、`repository`、`integration`、`webhook`、`fetch`、`remote_mcp`、`gateway_chat`、`usage`、`notification`、`delivery_ledger`（新） |
| 4 协作内容 | 房间里的东西：消息、线程、任务、采纳、文档、记忆、反馈、题目 | `notices`（新）、`block`、`thread`、`pin`、`mentions`、`room_task`、`task`、`review`、`living_doc`、`documents`、`library`、`memory`、`feedback`、`space`、`groups`、`questions`、`answers`、`comments`、`discussion`、`knowledge`、`materials`、`project_skill`、`search`、`docs_site` |
| 5 智能体执行 | 一轮怎么跑、在哪台机器上跑 | `agent` 拆出的各包（见[下文](#agent-split)）、`agent_session`、`agent_instance`、`machine`、`device`、`local_fs`、`shell`、`site`、`preview`、`assistant`、`delivery` |
| 6 编排 | 到点或有事时决定唤起谁 | `routine`、`task_quiet`、`admin` |
| 7 报表 | 只读汇总，没有任何包依赖它 | `platform_stats`、`feature_stats`、`dashboard`、`project_progress` |

分层之前先搬五组模块。它们今天住在高层的包里，却被低层大量引用；不搬的话，分层契约第一天就冻进一大批「其实只是放错地方」的边：

| 模块 | 搬到 | 为什么 |
|---|---|---|
| `platform_stats/windows.py` | `common` | 一个时间窗口工具，被 `user`、`usage`、`feedback`、`memory` 引用 |
| `feature_stats/pricing.py` | `usage` | 计价是计量的一部分 |
| `agent/announce.py`、`agent/platform_notices.py` | `notices`（第 4 层） | 往房间时间线写一条平台消息，属于协作内容；`project`、`policy`、`review`、`webhook` 都在用 |
| `agent/github_app.py`、`agent/forgejo_tokens.py`、`agent/forge_cli.py` | `forge`（第 3 层） | `review`、`project` 要同步拿令牌，必须在它们下面 |
| `delivery/addressing.py`、`delivery/ledger.py` | `delivery_ledger`（第 3 层） | 投递账本被 `membership`、`team`、`policy` 引用；把消息交给智能体的那一半留在第 5 层的 `delivery` |

按这张表、做完这次搬家，向上的包级边约 70 条（2026-10-07 在依赖图上模拟），全部冻进分层契约，之后逐条还。大头是三类：`topic` 归档时直接清理机器和会话（第 2 层调第 5 层）；`project` 直接读 `review`、`task`、`library`（第 2 层调第 4 层）；`block`、`review`、`room_task` 直接调 `agent` 的 `chat` 和 `runtime` 去唤起一轮（第 4 层调第 5 层，12 条）。

**哪些改成事件，哪些保留同步调用。** 只有「让高层去做一件事」改成事件：话题已归档、消息已写入、采纳卡已通过、任务已创建，由第 5、6 层订阅后决定唤起谁、清理什么。需要当场拿到结果的调用（拿 GitHub 令牌、查额度、写一条平台消息）保留同步，办法是把被调用方放到调用方下面，也就是上面那张搬家表做的事。

### 为什么选这个方案 {#backend-options}

| 方案 | 做法 | 结论 |
|---|---|---|
| A. 只收紧现有三条契约 | 目录不动，继续把冻结条目一条条还掉 | 不选。C3 只说「兄弟包无环」，不说谁在上谁在下；一条边还掉，常常换个方向又长出来 |
| **B. 按依赖方向分层，层写在契约里** | 包名和目录不动；`.importlinter` 加一条 `layers` 契约，按上表列出各层，同层兄弟用 `:` 连接（允许互相依赖，但仍受 C3 无环约束） | **选它**。方向成了机器能判的规则；包解耦后换层只改契约里的一行，不搬目录、不断 `git blame` |
| C. 按层建目录（`domain/kernel/user/`…） | 把包移进层目录 | 不选。层的归属会随解耦变化，每变一次就是一次全仓改 import |
| D. 拆成多个服务 | 按层或按域拆进程 | 不选。进程内的耦合解不开，拆进程只会把函数调用换成网络调用；按可用性已经拆了四个进程（见[后端结构](/dev/backend-app#processes)） |

`layers` 契约会检查间接链：一个没分层的包会被当成通道，让冻结名单暴涨或漏判。所以分层契约上线时，`app/domain` 下每个包都必须在表里，新增的包不在表里就红。

### 一个领域包的样子 {#backend-package}

每个领域包对外只露一面，内部分读写。目标形状：

```
app/domain/<包>/
  __init__.py     对外的面：别的包和路由只从这里 import（服务函数、DTO、事件类型）
  models.py       ORM。只有本包、alembic 和本包的测试能 import
  repository.py   本包私有的持久化
  commands.py     写：开事务、校验、写行；同一事务里记变更和事件
  queries.py      读：返回 DTO，不把 ORM 对象交出包外
  events.py       本包发布的事件
  handlers.py     本包对别的包事件的订阅
  schemas.py      DTO
```

三条规矩，各要一条新契约：

1. 路由只调领域的公开面。C2 今天只禁路由碰别包的 `models`，终点是连 `repository` 一起禁。
2. 跨包的 ORM `relationship()` 改成只留外键列；跨包的联表查询放进拥有主表的那个包的 `queries.py`，或者第 7 层。用 import-linter 的 `protected` 契约把每个包的 `models` 限给本包。
3. 事件在写入方的同一个事务里落表，订阅方在提交之后处理，处理器必须幂等。Sentry 的跨域 outbox 和 Saleor 的「事务里的事件推迟到提交后」是同一个做法。

### `agent` 包拆成什么 {#agent-split}

`agent` 一个包 66,454 行，包着模型网关、设备链路、预览隧道、代码托管凭据和整个轮次运行时，所以它依赖 31 个包、又被 20 个包依赖。按职责拆：

| 新包 | 从 `agent/` 搬走的 | 层 |
|---|---|---|
| `forge`、`notices` | 见上面的搬家表 | 3、4 |
| `chat`（新，不并进 `conversation`） | `chat.py` 的消息收发、`mentions`、`turn_inputs`、`turn_speakers`、`prompt`、`pending_messages` | 5 |
| `turns` | `runtime.py` 的 `AgentWorkRunner`、`recovery`、`liveness`、`turn_*`、`turn_adoption` | 5 |
| `live` | `agent/realtime/` 的 broker、`live_frames`、`live_notices`、`room_events` | 5 |
| `device`（并入现有包） | `device_hub*`、`device_link`、`device_provider`、`device_storage`、`machine_*`、`executor_transport` | 5 |
| `harness` | `agent/harness/` | 5 |
| `gateway` | `gateway*` | 5 |
| `preview`（并入现有包） | `preview_hub`、`preview_owner`、`preview_tunnel` | 5 |

`chat.py` 不并进 `conversation`：`conversation` 在第 1 层，一个依赖都没有、被 20 个包依赖，把 `chat.py` 的依赖带进去会当场造出新环。

拆的顺序按依赖：先拆 `forge` 和 `notices`（只需要搬家），再拆 `preview`、`device`；`gateway` 今天还引用 `agent` 内部的 `service`、`queries`、`room.sessions`、`profiles`、`supply`（`gateway_usage.py:42`），要先断开这几条才能独立。`chat.py` 和 `runtime.py` 最后拆，并且等「合并异常基类」那条任务进 main，否则两边同时大改同一批文件。

### 函数内导入 {#deferred-imports}

函数体里的 `import` 只有带着原因才合法：同一行或上一行写 `# deferred-import: <原因>`。它把依赖藏到运行时，读代码的人和多数工具都看不见；仓库里有的是为了躲环，有的只是拆文件时原样搬过来的（`api/routes/users/` 的 77 条就是，上提后没有一条成环）。

- 合法的原因：打破一个还没还的环（写明环的另一端）、可选依赖、启动开销、测试替身要在调用时取模块属性。
- 没写原因的按文件冻结在 `backend/deferred-import-baseline.json`：旧文件只许减少，新文件必须为 0。检查是 `backend/scripts/check_deferred_imports.py`，看板 `arch-metrics.py` 用的是同一个计数函数。
- 为躲环而写的那一类，随分层契约的冻结条目一起还：环解开，它就能上提。
- 没有直接用 ruff 的 `PLC0415`：ruff 没有基线，接入就要在几百处各加一个不说原因的 `noqa`。基线降到 0 之后换成 `PLC0415`，`noqa` 后面写原因。

## 变更推送：写入时登记，提交后推失效帧 {#realtime}

终点是：每次写入经 SQLAlchemy 的钩子自动登记「哪样东西在哪里变了」，提交之后按一张映射表推给订阅了的连接；回滚的不推。帧里不带数据，前端收到就重读变了的那一份。断线期间的变化不补发：重连就是重新订阅，订上时把在看的几样整份读一遍，消息按每段对话的序号补读。

今天在写入之后手工调 `announce_stale(room_id, 资源名)`（`backend/app/domain/agent/staleness.py:18`，21 处），发一帧 `{"type":"state","resource":...}`，前端重读那一类；`topics` 的调用点另外带上变的那一行的 `id`，前端就只重读那一行。项目框架（频道清单、未读、提醒档位）的变化推给订了这个项目的页面（`agent/project_feed.py`）：一条消息存进库、房间那一行变了、一轮开始或结束都会推，只说哪样在哪个房间变了；只有自己人看得见的房间只告诉里面的人。缺口在漏调：一处写完忘了通知，那一份就不刷新，没有检查会发现。

三块现成的样板，用的是终点的做法：`review/live.py` 在 `after_flush` 里记下变了的采纳卡、`after_commit` 之后推；`room_task/live.py` 对任务这一行、以及任务里问出或答掉的题做同样的事，推给任务所在的频道和任务自己那段对话；`agent/project_feed.py` 用同样两个钩子，一条消息不论从哪条路径存进库都会推给项目。

### 写入钩子和映射表 {#write-hooks}

- **谁记**：`after_flush` 看这次写了哪些行（新增、改动、删除），按映射表把「推给谁、哪样变了」记在会话上；`after_commit` 推出去，`after_rollback` 扔掉。只看写了什么，不看是哪个接口写的，新加的写入路径不用记得通知。
- **映射表**：一张表一行，写了哪张表的行 → 推给谁（房间、项目、只给某个人）→ 帧里的 `resource`。面板读一样新东西，就在表里加一行。
- **批量写**：`update(Model)`、`insert(...).on_conflict_do_update` 这类语句不进 `after_flush` 的行列表，今天约 180 处（`review/live.py` 就漏了 `poll_claim.py` 里的两处 `update(AcceptCard)`）。`do_orm_execute` 看语句写的是哪张表，映射表里有的照样登记；再加一道守卫：对映射过的表做 Core 写，要么经过钩子，要么显式登记。
- **帧不带数据**：频道清单那一行带着读的人自己的任务，未读按人算，`TopicOut` 里的 `can_manage`、`joined`、`awaits_me` 也按调用者算（`topic/schemas.py:60`），一帧发给所有人带不了这些。帧只说哪样变了，每个页面按自己的身份读，读的是变了的那一份（频道清单只读那一行）。
- **不用表上的触发器加 `LISTEN` / `NOTIFY`**：触发器连原始 SQL 都抓得到，但映射写成 SQL，每改一次是一个迁移；「只告诉房间里的人」还得回到后端判断，一样东西分在两处。

### 帧和协议 {#realtime-frames}

房间连接和项目订阅用同一种帧：

```jsonc
// 哪样在哪里变了；id 是那一样里的一行时，只重读这一行
{"type": "state", "resource": "topics", "id": "<topic_id>"}
// 订阅生效：那一刻房间的样子，和最后一条显示出来的消息的号
{"type": "subscribed", "topic": "…", "newest": 1834, "room": {…}}
```

- **订上时对齐**：房间连接的 `subscribed` 帧带着房间快照（`api/room_snapshot.py`）和 `newest`；项目订阅每次连上把四样各读一次。重连就是重新订阅，断线期间漏掉的帧不用补。
- **消息按号补读**：`blocks.seq` 是每段对话自己的号，数据库插入时给：先取这段对话的事务级 advisory lock，再取索引上的最大号加一，所以号的先后就是提交的先后。前端手里最大的号小于 `newest` 就读 `GET /topics/{id}/blocks?stored_after=`。已读位置也是这个号。
- **项目订阅**：页面在房间连接上订阅 `project:<id>`，收到频道清单的哪一行、未读、私聊未读、提醒档位变了；只有自己人看得见的房间只告诉里面的人。
- **运行中**：频道列表的「运行中」由 `runtime.AgentWorkRunner.running_topic_ids()`读本进程内存。终点是轮次状态在库里，「运行中」是一个查询，它的变化也经写入钩子推出。
- **流式帧不走写入钩子**：一轮正在输出的文字量大、只对在看的人有用，不落库。今天 `InProcessBroker` 只在本进程扇出（`agent/realtime/broker.py`）；轮次可以在任意副本跑之后，它需要一条跨副本的扇出（Redis pub/sub，或 `NOTIFY` 带轮次号），这是[全局锁退役](#ownership-retire)的前提之一。

| 方案 | 结论 |
|---|---|
| **A. 失效帧，前端重读变了的那一份；消息每段对话一个序号** | **选它**。帧不随查看者变，权限只在读的时候判；重连即重新订阅，不要补发队列。代价是每次变更多一次读，读的是变了的那一行时很小 |
| B. 变更日志：全局序号，和查看者无关的字段推整行，断线按游标补发 | 不选。多一张表、一个给号的分配器、一套 `hello` / `resync` 协议；按查看者算的字段仍要另读。我们的读已经按行，省下的那一次读不值这些 |
| C. 逻辑复制 / CDC（读 WAL 推变更） | 不选。按行做权限过滤要在复制流之外重写一遍，运维面多一个常驻组件 |

## 工作租约与后台任务：表里的行，按条件领取 {#work}

终点是：要独占的东西都是表里的一行，带持有者、令牌（fencing token）和到期时间；任何一个后端副本都用一条带条件的 SQL 领取、续期、交还，到期时间一律按数据库的 `now()` 算，死掉的持有者因为不再续期而自然放手。全局锁最后退役。

### 会话工作租约今天的样子 {#lease-today}

`agent_sessions.work_lease`（`agent_session/models.py:126`）一个 JSON 列里叠着两件事：

- **分配**：这个会话的工作在哪台机器、这一代工作区是哪个（`device_id`、`resource_id`、`room_resource_id`、`generation`、`home`、`workspace`、`url`…）。它被 SQL 按 JSON 路径读 11 处（例如 `session_work.py:253` 按 `work_lease["device_id"]` 查），会话结束后的清理也要读它。
- **安装认领**：谁正在装执行器（`claim`、`claim_until`）。认领和状态正交：一个可用的租约被复查时带着认领、状态仍是可用（`session_work.py:1213`）。

写入只有 10 处、在 5 个文件里。领取在锁住话题行和会话行之后、在 Python 里比较 JSON（`session_work.py:1008`）；续约是每次安装一个 asyncio 任务，每 10 秒开会话、锁行、改 JSON（`lease_claim.py:67`）；没有回收，读的人自己比 `claim_until`。时间来自**各后端进程自己的时钟**（`lease_claim.py:46`），滚动发布时两个进程读同一行，时钟差直接变成 TTL 误差。三个常数（30 秒心跳超时、10 秒续一次、660 秒总时限）和 Temporal 的心跳超时、start-to-close 一一对得上，参数没问题，问题在存储形状。

仓库里已经有目标形状的先例：`review/services/poll_claim.py` 用 `poll_claim` / `poll_claimed_until` 两列一条 `UPDATE ... WHERE poll_claimed_until IS NULL OR < now()` 领取；`notification/outbox.py`、`routine/service.py`、`usage/compute.py` 等用 `FOR UPDATE SKIP LOCKED`，`routine/service.py:393` 的注释明说两个进程同时扫。

### 四个轴，各有一个权威来源 {#lease-axes}

| 轴 | 权威来源 | 状态 |
|---|---|---|
| 业务进度 | `tasks.status`（`room_task/models.py:25`） | `open` / `closed`，不变 |
| 会话 | `agent_sessions`：一行表示「这个智能体在这个对话里用这个 harness」 | 不加状态列 |
| 工作租约 | 新表 `session_work_leases`，一个会话一行 | `preparing` / `environment_pending` / `ready`，加上正交的认领列 |
| 机器 | 设备在线由连接推导（`device_hub.is_online`）；云主机看 `CloudHost` 和 `MachineStatus`（`machine/models.py:39`） | 不存副本，继续从基础设施推导 |

执行状态和机器状态分开存、分开推进。OpenHands 把两者绑在一起时出过「沙箱停了，会话还卡在运行中」（software-agent-sdk#4893）。

### 租约表和四条语句 {#lease-table}

```sql
CREATE TYPE work_lease_status AS ENUM ('preparing', 'environment_pending', 'ready');
CREATE TABLE session_work_leases (
  session_id       uuid PRIMARY KEY REFERENCES agent_sessions(id) ON DELETE CASCADE,
  generation       uuid NOT NULL,            -- 分配：拆成列，能建索引、能做条件
  kind             text NOT NULL DEFAULT 'device',
  device_id        text NOT NULL,
  resource_id      uuid NOT NULL,
  room_resource_id text NOT NULL,
  status           work_lease_status NOT NULL,
  claim            uuid,                     -- 认领：fencing token；全为 NULL 表示没人在装
  claim_holder     text,                     -- 哪个后端进程，便于排障和退出时一次放掉
  claim_until      timestamptz,              -- 心跳截止，按数据库 now() 算
  claim_deadline   timestamptz,              -- 领取时刻 + 660 秒，续约不能越过
  claim_progress   jsonb,                    -- 安装做到哪一步，下次领取从断点接着做
  detail           jsonb NOT NULL DEFAULT '{}',  -- home、workspace、url、mcp_servers 等展示和调用用的
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CHECK ((claim IS NULL) = (claim_until IS NULL) AND (claim IS NULL) = (claim_deadline IS NULL)),
  CHECK (claim_until IS NULL OR claim_until <= claim_deadline),
  CHECK (status <> 'environment_pending' OR claim IS NULL)
);
CREATE INDEX ON session_work_leases (device_id) WHERE status = 'ready';
CREATE INDEX ON session_work_leases (claim_until) WHERE claim IS NOT NULL;
```

没有租约时删掉这一行，不设「已释放」状态，和今天 `work_lease = None` 的语义一致。状态转移在 Python 里用一张转移表、一个入口函数管住，数据库只用 CHECK 管不变量。

- **领取**：`INSERT ... ON CONFLICT (session_id) DO UPDATE ... WHERE l.generation = EXCLUDED.generation AND (l.claim IS NULL OR l.claim_until <= now())`，同一条语句里完成状态迁移（复查时保持 `ready`，换机器时回到 `preparing`）。没有返回行就是没领到。写法是 Hatchet `AcquireOrExtendLeases` 的「插入或在过期时接管」加 multica 的「领取时一并迁移状态」。
- **续约**：`UPDATE ... SET claim_until = LEAST(now() + 30 秒, claim_deadline) WHERE session_id = :sid AND claim = :claim`。不加 `claim_until > now()`：认领过期但还没被别人接管时续回来，避免第二次安装。
- **完成**：`UPDATE ... SET status = :final, claim = NULL ... WHERE session_id = :sid AND claim = :claim`，0 行就是「安装期间分配变了」。
- **回收**：`UPDATE ... SET claim = NULL ... WHERE session_id IN (SELECT ... WHERE claim IS NOT NULL AND claim_until <= now() LIMIT 500 FOR UPDATE SKIP LOCKED)`。正确性不依赖它，读的人只看 `claim_until <= now()`；它只让展示和「等它挪走」的轮询早点看到结果，所以每个副本都可以跑。进程正常退出时按 `claim_holder` 一次放掉自己的认领。

房间级的一致性继续靠执行前锁住话题行（`lock_for_execution`）。代码注释说「工作机器那一半是房间的」（`agent_session/models.py:64`），但今天每个会话各有一份，表也按一个会话一行建。

并发安装多起来以后，可以按 procrastinate 的做法加一张进程心跳表（每个进程一个心跳，替代每个认领一个续约任务）；今天并发量不需要。

### 周期任务：每个任务每一拍一个租约 {#periodic-leases}

`periodic_job_runs`（`core/job_runs.py`）有 `name`、`last_run_at`、`run_by`、`run_until`，每个任务、每一拍一个租约：`INSERT ... ON CONFLICT (name) DO UPDATE ... WHERE last_run_at <= now() - 间隔 AND (run_until IS NULL OR run_until <= now()) RETURNING name`，有返回行才跑这一拍，`run_by` 记主机和进程、`run_until` 记到下一拍。租约读不到时照跑这一拍并记 ERROR，宁可重跑也不让调度全停。任务默认还是「只在持锁进程跑」，第 3e 步逐个迁出；迁出后每个进程都调度它，每一拍只有一个进程真跑。Hatchet 的维护任务也是按种类各自租，不靠一个全局 leader。

29 个周期任务按能不能迁分四类：

| 类 | 任务 | 动作 |
|---|---|---|
| A. 已经能并发跑 | routines、timed deliveries、通知邮件和推送、通知摘要、PR 轮询两项、云计算计量、云预热池 | 第一批迁出。routines 和 timed deliveries 会起轮次，先确认不持锁的进程能起轮次 |
| B. 只做保留期清理或幂等同步 | 托管凭据缓存清理、运行记录过期、文档问答保留、棘轮快照采集、托管事件订阅对账 | 第二批迁出，每个先用两个进程跑一遍集成测试 |
| C. 读请求路径写进本进程内存的东西 | backend error flush：`backend_log.py` 的 `intake` 在每个进程的请求路径上写入，由持锁进程的周期任务刷出，只刷满 300 秒（`DEDUP_WINDOW_S`）的窗口。部署是单实例蓝绿双槽，后起的进程拿到锁后会刷自己攒的窗口，不会丢。出局的进程原本会丢最后不足 300 秒的窗口：`main.py` 的 `hand_over()` 先停掉周期任务、再放锁 | 第 2e 步已做：`hand_over()` 停周期任务之前强制刷一次，不论 300 秒到没到；进程退出前再刷一次，兜住交接期间新开的窗口。和是否持锁无关，是迁出锁的第一步 |
| D. 靠本进程内存判断「谁在跑」 | 孤儿轮次清扫、排队消息清扫、进度提醒、启动恢复、托管事件监听 | 留在锁里，等[全局锁退役](#ownership-retire)那一步 |
| 待逐个读代码 | 记忆整理、任务截止、安静任务提醒、超时投递告警、托管孤儿账号清理、云主机池、云沙箱生命周期、订阅用量导入 | 能用领取列改造的归 A，否则归 D |

### 全局锁退役 {#ownership-retire}

全局锁（`backend/app/core/ownership.py`，一条会话级 advisory lock）今天保护的 D 类，都在用本进程内存回答「谁在跑」：只有持锁进程起轮次（`main.py:173` 的 `hold_turns`），会话订阅和托管事件监听只在它身上（`main.py:272`）。退役的条件：

1. 轮次状态进库：一轮在跑，库里有一行带持有者和心跳，「运行中」和孤儿判断都查它（multica 用 runtime 级心跳判 running 任务死活）。
2. 每个会话一张订阅租约，谁拿着谁监听、谁起这个会话的轮次；托管事件监听一张租约。
3. 闸门按闸门行上的认领判断有没有人在跑。
4. 流式帧跨副本扇出（见[帧和协议](#realtime-frames)）。

四件都做完，D 类迁出，`core/ownership.py` 删除。River 证明「维护任务选主」本身可以接受；我们最后不选主，是因为 D 类的每一项都能落到行上的租约，而选主会让发版交接期间这些工作全停。

| 方案 | 结论 |
|---|---|
| A. 全局锁覆盖一切（今天） | 不选作终点。「谁在做」变成「哪个进程活着」，做到一半的工作没有痕迹，发版交接期间全停 |
| **B. Postgres 里按行租约：会话租约表、周期任务租约、轮次租约** | **选它**。不加基础设施；领取和业务写入在同一个事务里；状态能直接查。multica、Hatchet、procrastinate 都是这个形状 |
| C. Celery 加 Redis（dify 的做法） | 不选。排工作和业务写入分在两个系统里，要么丢要么重复；定时器 beat 只能起一个实例，单点还在 |

## 前端：一个查询缓存，按推送重读 {#frontend}

前端的终点是：服务端数据只经一个查询缓存（`@tanstack/vue-query`）取和存，推送说哪样变了就重读缓存里那一份；请求只走一套传输层，类型由后端声明的 OpenAPI 生成；页面读路由、调组合函数，组件只吃 props 和事件。

### 目录 {#frontend-tree}

```
frontend/src/
  api/          传输层 http.ts（鉴权、重试、条件 GET、刷新令牌）＋按资源的薄封装
  api/schema.gen.ts   由 /api/openapi.json 生成的类型，不手改
  query/        QueryClient、查询键表、推送帧的应用（changes.ts）、各资源的 queryOptions
  features/<域>/ 该域的组合函数（包 useQuery / useMutation）、乐观更新、域内客户端状态
  views/        路由容器：读路由、调 features、渲染同目录的 <页面>View.vue
  components/   只吃 props 和事件的组件（含各 *View.vue）
  layouts/ router/
  lib/          纯函数，不 import 上面任何一层
  stores/       只放客户端状态：外壳、草稿、偏好
```

今天和终点的差：`src/api.ts`（2,779 行、270 个导出）加 `src/api/`（36 个文件，`http.ts` 是它的传输层）是 fetch 一套；`src/network/`（axios，14 个资源）是另一套；`services/` 里还有账户和推送；`stores/feedback.ts`（1,708 行）把服务端状态和客户端状态混在一起。

### 数据层 {#frontend-data}

工作区（项目外框、侧栏、总览、频道和任务页、右侧面板、任务清单、文档、成员）和应用外壳的服务端数据只经一个 QueryClient：`src/query/client.ts` 里的单例，`src/query/keys.ts` 一张键表（按 `['project', id, …]`、`['room', id, …]` 分层），各资源的 `queryOptions` 在 `src/query/` 下按领域放。读的地方把这一个实例显式传给 `useQuery`，不靠组件树注入：store 和不在组件里调用的组合函数也这样读。

- **一个 QueryClient**：换了人登录或退出时，每一份退回「没读过」，看着它的地方还连着，新的人的令牌到手后屏幕上那几份重读（`services/account.ts`）。项目清单另存一份在 sessionStorage（`query/persist.ts`），冷打开时左边栏当场就有。
- **不可变更新**：本地写一律 `patchQuery(key, old => 新值)`：先作废正在路上的那次读，再改缓存。「改之前发出去的读把刚改的盖回去」靠它挡住，不再各处手写版本号。
- **推送进缓存**：`state` 帧经一个入口（`query/changes.ts`）按键让对应几份再读一次；正在读的那次回来之后再读一次，一串帧只多读一次（`refreshQueries`）。缓存里没有的（采纳卡、提案卡、文档）由页面自己接。
- **进房间不再逐块读**：房间连接上的 `subscribed` 帧带着订阅生效那一刻房间的样子（`room`：名册、任务、置顶、支线、提议卡；任务还有它自己、相关、审阅意见），每一块和它自己那个接口回的一样（后端 `api/room_snapshot.py`）。打开房间的导航出发时记下「快照要来」，这几格的读先等它（`query/snapshot.ts`）；之后的帧都比快照新。采纳卡和预览不在里面：读它们要等外部服务。
- **侧栏不读整个项目的任务**：频道清单的每一行带着挂在它下面的任务（`my_tasks`：和我有关的几件，加进行中的总数），任务变了重读那一行。
- **KeepAlive 里的页面停下观察**：保活页面的查询把 `enabled` 绑到页面在不在屏幕上（`composables/usePageActive.ts`）。
- **项目推送代替轮询**：项目外框在房间连接上订阅这个项目（`query/projectFeed.ts`），频道清单的哪一行、未读、私聊未读、提醒档位哪一样变了就重读哪一样；订上和每次重连时四样各读一次。频道清单那一行重读回来后按最后有动静的时刻挪到该在的位置。任务概览的清单由任务连接上的 `todo` 帧直接放进缓存。
- **聊天时间线不迁**：双向翻页、跳到中间、实时追加、发送中的消息、按 id 合并，`useInfiniteQuery` 给不了；时间线留在 `useTimeline` / `useChatPaging`，只有每个房间最新那一段窗口在缓存里（`query/blocks.ts`），打开房间、预取、未读增加时的后台预取共用它。
- **还没迁的**：反馈中心（`stores/feedback.ts`）、管理后台、空间、资料库。

| 方案 | 结论 |
|---|---|
| A. 继续手写，把各页的缓存合成一份 | 不选。合出来是又一份，按前缀失效、结构共享、只重取在看的都要再写一遍 |
| **B. 采用 @tanstack/vue-query** | **选它**。它只管服务端状态，和 pinia 不冲突；结构共享让「整块重取回来只变一行」时其余子树保持同一个引用 |
| C. Linear 式本地对象池加 IndexedDB | 不选。全量模型注册和离线存储太重；只借三样：整行 delta、快照带序号、写响应带序号 |

### 请求类型 {#frontend-api}

终点是前端的请求和响应类型由后端生成，字段对不上在类型检查里就红。今天做不到：`/api/openapi.json` 里 740 个接口只有 1 个的成功响应有类型，路由里 699 个函数返回 `-> dict`，还有一个重复的 operationId。所以分两步：

1. **后端声明响应模型**：路由加 `response_model`（或返回类型注解），按资源一组一组补；加一道守卫，新路由必须声明，存量按文件冻结。
2. **生成类型、合并请求栈**：用 openapi-typescript 生成 `src/api/schema.gen.ts`，`src/api/` 下的薄封装按资源换成用生成的类型；`network/` 的拦截器（鉴权、刷新令牌、错误上报）并进 `http.ts` 后删掉 `network/`；`api.ts` 按资源搬空。

### 组件分级 {#frontend-components}

`components/` 下的终点是全部 A 级：只吃 props 和事件。读路由、取数、读业务 store 的只能是 `views/` 下的容器页和 `features/` 下的组合函数。判据和闸门已经有了（[场景清单](/dev/scenes#standalone)、`lint:boundary`），这里只定终点：场景 debt 为 0，组件边界违规为 0，`components/` 下 C、D 级为 0。

## 迁移顺序 {#migration}

每一步是一个能单独合并的 PR，标了依赖的要等依赖合进 main。没有依赖关系的步骤可以并行。

| 步 | 内容 | 依赖 |
|---|---|---|
| 1 | 本页；清掉导入契约里失效的冻结条目；`api/routes/users/` 和 `users_password.py` 的函数内导入上提；函数内导入的棘轮；前端分级不再把注释里的 `vue-router` 算成读路由；几处组件不再自己取数或读路由 | 无 |
| 2a | 搬家：`windows`、`pricing`、`notices`、`forge`、`delivery_ledger` 五组模块 | 1 |
| 2b | 「不许新增 `announce_stale`」「不许新增读进程内存运行态」「新路由必须声明响应模型」三道守卫 | 1 |
| 2c | 已合并：工作区和应用外壳的服务端数据只经一个 QueryClient，手写缓存删掉；就地改数据改成 `patchQuery`；KeepAlive 页面的 `enabled`。反馈中心、管理后台、空间、资料库另作一步 | 1 |
| 2d | 租约写入收口：10 处写入收进一个 `lease_store`，带状态转移表；`claim_until` 改用数据库 `now()`；守卫：写 `work_lease` 只许经过它 | 1 |
| 2e | 交接（`hand_over()`）停周期任务前强制刷一次 backend error flush；`periodic_job_runs` 加 `run_by`、`run_until`，`PeriodicRunner` 加「只在持锁进程跑」的开关 | 1 |
| 3a | 分层契约：每个包都分好层，越界的边冻结 | 2a |
| 3b | 写入钩子：`after_flush` 和 `do_orm_execute` 登记、`after_commit` 推，一张映射表，Core 写守卫；29 个 `announce_stale` 和项目推送的手写调用全部换成映射，守卫变成「面板读到的模型都有映射」 | 2b |
| 3c | 已合并：`state` 帧经 `query/changes.ts` 按键失效 | 2c |
| 3d | 建 `session_work_leases`，从 JSON 回填，双写，对账任务跑一周 | 2d |
| 3e | A 类周期任务逐个迁出全局锁，每个一个 PR；再是 B 类 | 2e |
| 4a | `agent` 拆出 `preview`、`device`；断开 `gateway` 对 `agent` 内部的引用后拆出 `gateway` | 3a |
| 4b | 轮次状态进库：「运行中」和孤儿判断改成查询；`running_topic_ids()` 退场 | 2b |
| 4c | 已合并：项目推送取代两处 30 秒轮询（房间连接上订阅 `project:<id>`，推失效帧）；消息每段对话一个序号（`blocks.seq`），断线按号补读，已读位置也是这个号 | 2c |
| 4e | 租约切写：领取、续约、完成、回收改用四条 SQL，JSON 只作镜像 | 3d |
| 5a | 异常基类已合并；`room/turn.py` 接一轮的运行，`live_work.py` 持有现场状态；`session_turn_events.py` 声明由调用方等待的会话轮次事件接口，API 构造时显式注入同一运行器，`chat.py` 不再导入 `app.api.deps`。其余 `chat.py` / `runtime.py` 职责继续分段拆出 | 4a、4b |
| 5b | 租约切读：11 处 JSON 路径查询改成联表，停写 JSON，最后删列 | 4e，且 4e 已发版 |
| 6 | 全局锁退役：会话订阅租约、托管事件监听租约、闸门认领、流式帧跨副本扇出，D 类迁出，删 `core/ownership.py` | 4b、5b、3e |
| 7 | 路由补响应模型，按资源分批 | 2b |
| 8 | 生成类型，合并请求栈，删 `network/`、搬空 `api.ts` | 7、2c |
| 9 | 跨包 `relationship()` 改外键列，「让高层去做事」的调用改成事件，逐条还分层契约的冻结条目 | 3a |

## 完成标准 {#done}

每一条都是一个能跑出来的数或一道会红的检查。行数下降和测试通过本身不算架构变好：拆文件不改依赖方向，包级依赖图一条边都不会少。

| 线 | 算完的条件 | 怎么验 |
|---|---|---|
| 领域分层 | 分层契约和 C3 的冻结条目都是 0；领域包之间没有强连通分量 | `cd backend && uv run lint-imports`；看板上的强连通分量大小 |
| 函数内导入 | 基线文件里每个文件都是 0，换成 ruff `PLC0415` | `uv run python scripts/check_deferred_imports.py` |
| 路由边界 | C2 扩到 `repository` 后冻结条目为 0；每个路由都声明响应模型 | `lint-imports`；响应模型守卫 |
| 变更推送 | 后端没有手写的推送调用（`announce_stale`、`tell_project`）；面板读到的每个模型在映射表里；断线重连后不用整页刷新就能对齐 | 守卫测试；端到端测试：断网期间改数据，重连后列表一致 |
| 运行态 | 没有任何请求从进程内存回答「谁在跑」 | 守卫测试；起两个后端进程，任一个答「运行中」结果相同 |
| 工作租约 | `agent_sessions` 上没有 `work_lease` 列；租约的时间判断全在 SQL 里 | 列不存在；守卫：Python 里不出现和 `claim_until` 比较的 `datetime.now` |
| 后台任务 | `core/ownership.py` 删除；两个副本同时跑，没有重复副作用 | 文件不存在；双进程集成测试 |
| 前端数据层 | 没有手写缓存和取数轮询；只有一套传输层；请求类型来自生成文件 | 守卫：`network/`、`api.ts` 不存在，取数用的 `setInterval` 为 0 |
| 组件分级 | `components/` 下 C、D 为 0；场景 debt 和组件边界违规为 0 | `pnpm --dir frontend run lint:scenes`、`lint:boundary`；`arch-metrics.py` |
| 文件大小 | 前后端都没有超上限的文件 | `.claude/scripts/check-file-sizes.py` 对全树 |

## 和其他工作的边界 {#boundaries}

- 路由怎么声明写权限、怎么守，由「写权限闸门」那条任务定；本页的 `api` 层只要求路由调领域的公开面、声明响应模型。
- `chat.py`、`runtime.py` 的拆分不改轮次顺序：自主开轮在原位等待持久化，输出先等待发布再记活性，收尾先等待关账及发布再清运行器记号。事件接收端是同进程的窄协议，不是 detached pubsub 或异步总线；没有默认空接收端，也不让 `get_work_runner()` 反向构造 `ChatService`。
- 场景拆分里已经有人在做的三批（spaces、workspace、杂项，#2827、#2819、#2911），不在本页的迁移步骤里重复。
