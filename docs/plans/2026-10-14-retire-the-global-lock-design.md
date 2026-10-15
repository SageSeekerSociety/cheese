# 全局锁退役：进程内状态清单与迁移路线

状态：设计定稿（2026-10-14）。这份文档只写盘点与迁移路线，不改任何行为代码。

一句话：后端今天只有一个进程、一把进程内的锁在兜着轮次准入；这份文档盘点所有进程内状态、给每一项定下跨进程的家，并给出可以分步合入的退役路线。

## 0. 这份文档回答什么

四件事：

1. 今天哪些状态活在进程里，谁在读；
2. 每一项该搬到哪里（库里、租约里、跨副本扇出）；
3. 一条能分步合入的迁移路线，每一步合进去之后什么仍然正确；
4. 停在第 1 步或第 2 步会坏什么。

按本仓库的规矩，架构改动在动代码之前要先有：仓库内调研、外部调研、反方意见、迁移路线。这四节分别是第 2 节、第 4 节、第 5 节、第 6 节。

**本任务不改任何行为代码。**

## 1. 为什么现在做

`#1510` 的诊断（`docs/topics/perf-awaiting-me-queue.md`）测出 `GET /awaiting-me` 一个请求吃掉约 35 ms 循环 CPU，20 个请求约 0.7 s，全部挤在一个事件循环上。它同时给出了两条路：削每请求 CPU，或者加工作进程。

加 `--workers 2` 今天不是慢，是坏。诊断第 5 节点了三处：

- 连接预算。每个 worker 有自己的池，`backend/app/core/config.py` 92–113 行那笔账按「两个后端进程 + 连接持有者」算出 2 × (35 + 1) + 10 + 10 = 92 / 97，dev 的服务器 2026-09-18 才提到 200 条，上线时的三份变四份。
- 进程内状态：`ChatService.live`（`backend/app/domain/agent/chat.py`，`turn/state/live.py` 自己写着 “lives and dies with the process”）。
- 其余单进程假设：`_gateway_lock`（对 `project.settings` 的读改写）和周期性任务。

座位准入是第四处，诊断没点到，这一条是本文件照代码推的（见 2.2）：轮次准入握在一把进程内的锁上，消息落到没有这把锁的那条进程就永远不会被放出去，于是谎报「投递失败」。

## 2. 今天有哪些状态活在进程里，谁在读

### 2.1 `ChatService.live` —— `LiveWork`（`backend/app/domain/agent/turn/state/live.py`）

模块开头自己划了界：「None of it is a source of truth: it lives and dies with the process, and the durable rows are written elsewhere.」表是公开的，handler 直接读写格子；方法只做纯键运算。

**房间层**

| 格子 | 形状 | 谁在读 |
| --- | --- | --- |
| `topic_locks` | `dict[topic, asyncio.Lock]` | `lock_for()`。不是任何人的轮次的那几件事：换房间环境、根话题上的记忆整理 |
| `seat_locks` | `dict[(topic, agent), asyncio.Lock]` | `seat_lock_for()`。同一座位的轮次排队，两个座位并行 |
| `room_session_agents` | `dict[topic, str]` | 这间房此刻是哪位队友的会话（最近一次 `AgentSessionInfo`） |
| `session_route` | `dict[topic, str]` | 这个活会话的模型流量走哪条路 |
| `session_model` | `dict[topic, str]` | 这个会话当初启动在哪个模型上 |
| `conversation_rooms` | `dict[conv, (room, inner, 是不是支线)]` | 这段对话挂在哪个房间下 |
| `replays` | `dict[topic, asyncio.Task]` | `recover_sessions` 补放没人听时会话说的话 |

**轮次层**

| 格子 | 形状 | 谁在读 |
| --- | --- | --- |
| `active_turn_ids` | `dict[topic, set[work_id]]` | `liveness.running_tasks`（`backend/app/domain/agent/liveness.py` 84–115 行）、`pending_messages`、`consuming_work_id`。一个话题可以同时有多个座位在跑，所以是**集合** |
| `hook_work` | `dict[(topic, work_id), HookWorkState]` | 事件订阅那条路读它来落库、组装现场块、发广播 |
| `retry_notes` / `waiting_notes` / `compact_notes` | `dict[turn_id, note_id]` | 每一串重试/等机器/压缩只留一行活的提示 |
| `dead_sessions` | `set` | `recover_sessions` 报过「这台机器上会话没了」的座位 |
| `unread_inputs` | `dict[conv, dict[input_id, ts]]` | 只管活不活；自己写着「Settlement never depends on this process cache」 |

`HookWorkState` 里两半性质完全不同：

- **事实**：`project_id` / `topic_id` / `work_id` / `reply_to` / `roster` / `topic_refs` / `continuation_id` / `route` / `acting_agent` / `agent_pool` / `user_text` / `started_at` / `model` —— 进程死了就再也答不上来；
- **手上正在用的**：`steps`（工具调用落在哪个现场块）、`known_commits`（一个 `asyncio.Task`）、三个 `last_*_at` 提醒时刻、`self_started` —— 注释已经写明重启丢掉的只是几截输出和几个红点。

### 2.2 座位准入 `seat_admission`（`backend/app/domain/agent/seat_admission.py`）

一个 `ContextVar` 加一把 `asyncio.Lock` 的上下文管理器，文档只有一句：「Serialize prompt preparation and live offers on the same recipient seat.」

它按 `(topic, agent)` 取 `live.seat_locks`，那格子的注释写着「Prompt construction is serialized per (topic, agent) seat」，所以是**进程内**的：第二条进程对同一个座位拿到的是另一把锁，两边同时放行同一个座位的消息。后果是消息落到没有锁的那条进程就再也不会被放出去、谎报投递失败。这一条是照这段代码推出来的，`docs/topics/perf-awaiting-me-queue.md` 没写过。

### 2.3 `_gateway_lock`（`backend/app/domain/agent/gateway_usage.py`）

一把 `asyncio.Lock`，管项目的虚拟网关 key 的读改写（铸 key、按项目额度调 L2 预算）。读写两半已经分开了：

- **读路径不拿锁**：key 已经铸好、预算也对，就直接返回。注释记着代价——2026-09-23 在 dev 上量过，同一个项目的 20 个并发准入仍然扇出成 5.8 s 的尾巴，因为那把锁曾经横跨整段方法体，包括两次网关 HTTP 调用；
- **写路径拿锁 + `repo.lock_settings(project)`**：库里那把行锁已经在了，`ProjectGatewayKey.read(for_update=True)` 的注释写着「Another backend process can charge during a rollout.」

所以这一项**目标态已经有一半**，缺的只是把外层那把 `asyncio.Lock` 换掉。

### 2.4 `HostMemory`（`backend/app/domain/agent/admission.py`）

会话宿主机还装不装得下新会话，读 `/proc/meminfo` 的 `MemAvailable`，结果在进程内缓存 2 秒（`_lock` + `_read_at` + `_available`）。N 个进程就是 N 份各自为政的判断，合起来可以一起越过宿主机的上限——这道闸就是为了不让宿主机被拖垮。

### 2.5 周期任务（`backend/app/domain/agent/periodic.py`）

注册在 `PeriodicJobs` 上，都是本进程的 asyncio 任务：

| 任务 | 间隔 |
| --- | --- |
| forge 凭证缓存清理 | 3600 s |
| 孤儿轮次清扫 | `orphan_sweep_interval_s` |
| 已关闭任务里的活停下 | `closed_task_stop_interval_s` |
| 排队消息清扫 | `queued_message_sweep_interval_s` |
| 沉默轮次提醒 | `chat_progress_check_interval_s` |
| 清单过期提醒 | `chat_progress_check_interval_s` |
| 记忆整理（dream） | `memory_dream_sweep_interval_s` |

`get_runner()` 是**在每个 tick 时**才解析的，注释说明是「a missed wake-up must resume on whichever runner the application currently owns」。N 个进程就是 N 份清扫、N 份提醒、N 份 dream。

### 2.6 `pending_messages._runner`（`backend/app/domain/agent/pending_messages.py`）

模块级单例，`bind_runner` / `current_runner`。`nudge_messages` 只在 `runner.owns_sessions and runner.accepting_turns` 时才动手，否则记一行 `pending_messages nudge_dropped` 就丢下。消息落到的进程和持有会话的进程不是同一条时，这条消息只能等周期清扫。

### 2.7 现场帧扇出 —— `InProcessBroker`（`backend/app/domain/agent/realtime/broker.py`）

类文档第一句：「Fan-out pub/sub for one process」。`get_broker()` 是 `@lru_cache` 的进程级单例，状态全是内存的：`_subs` / `_buffer` / `_active` / `_active_since` / `_last_activity_at` / `_ended` / `busy_changed`。`live_frames.publish_live` 把现场帧发到「the room's channel and nowhere else」。

现场帧本来就是丢得起的：`live_frames.py` 自己写着「it is not a block, nothing stores it」，错过的客户端「has lost nothing」，下一帧或成品块会带上全文。

### 2.8 已经跨进程的 —— 别动

这几样已经在目标态了，退役路线里不重做：

- **`admission.Pool` / `admission.Hold` / `Slot` / `adopt`**（同在 `admission.py`）：「Both live in Valkey, so they hold across backend processes and outlive none of them: a slot is leased, renewed while its holder works, and lapses `LEASE_S` after a backend that died stopped renewing it.」`adopt()` 的用途就是「another process is taking the turn over」。
- **`turn_inputs.py`**：输入归属全在库里，`transition` 走 `SELECT ... FOR UPDATE`，并发的首次认领在主键上分出输赢。没有进程内状态。
- **`liveness.running_tasks` / `moving_topics`**：已经是双来源——本进程的 `active_turn_ids`，加上库里 `AgentTurn` 那行开着的兜底。`moving_topics` 的文档明确写着「一个进程按不住另一个进程正在跑的那一轮」。
- **`delivery/receipts.py`（输入结算）**：明确禁止从进程内状态推断完成——「Never infer completion from acceptance, an echo, another work's marker, or missing process-local state.」注册表、回执、终止全在库里。
- **`turn_inputs.py` 与 `delivery/receipts.py` 加起来说明一件事**：库里的那半已经进程无关了。退役锁要补的是**活的那半**（准入、轮次上下文、帧扇出、定时器），不是重做持久化。

## 3. 每一项该搬到哪里

| 现在在哪 | 目标家 | 理由 |
| --- | --- | --- |
| `live.seat_locks` / `seat_admission` 的 `asyncio.Lock` | Valkey 里的**每座位一把租约**，直接复用 `admission.Hold` | 租约、续租、`adopt` 都现成；进程死了租约自己过期 |
| `live.topic_locks` | 同上，每房间一把 | 同上 |
| `active_turn_ids` | **库里的一行开着的 `AgentTurn`**，内存只做写穿的缓存 | 兜底已经存在，只是现在反过来当次要的 |
| `hook_work` 的「事实」半 | **库里的一张轮次状态表**（或并进 `AgentTurn`） | 轮次跨进程、跨部署都得活着 |
| `hook_work` 的「手上正在用的」半 | **留在进程内**，允许死 | 它自己就说重启丢掉的不是记录 |
| `retry_notes` / `waiting_notes` / `compact_notes` | 提示行的**持久身份**做去重键 | 一串重试只该有一行，不管谁在看 |
| `_gateway_lock` | **库行锁** `SELECT … FOR UPDATE` on `projects` | 写路径已经在拿它了 |
| `HostMemory` 读数 | **全机一份、2 秒 TTL 的共享读数**（Valkey） | 闸门是给整台宿主机的，不该由 N 个进程各判一次 |
| 周期任务 | **每个任务名一把租约**，谁拿到谁跑 | 这把租约就是 `core/job_runs.py` 的 `JobRuns.claim`：一个任务名一行（`run_by`/`run_until`），`PeriodicRunner._take_tick` 每拍去领，拿到才跑、过期换人。迁移顺序 2e 已经建好，不要再套一层 `admission.Hold`（30 秒续租的粒度配不上 3600 秒的任务，白留三层）。验证口径就是 `test_two_processes_claiming_at_once_only_one_gets_the_tick` 与 `test_a_job_scheduled_on_every_process_runs_once_a_tick` |
| `pending_messages._runner` 单例 | 按租约认领，或把 nudge 广播出去 | 现在落错进程就只能等清扫 |
| `InProcessBroker` 现场帧 | **本进程扇出 + Valkey pub/sub 跨进程** | 现场帧本来就丢得起，at-most-once 正好 |
| `session_route` / `session_model` / `conversation_rooms` / `room_session_agents` | 从库里**按需重算**（`AgentSession`、`threads.room_id`、`tasks.room_id`） | 这几样本来就有持久副本 |
| `dead_sessions` / `unread_inputs` | 库里重算 | 一个只管活、一个明确不影响结算 |

## 4. 仓库外的做法调研

**现场帧扇出：Redis/Valkey pub/sub vs 流 vs Postgres `NOTIFY`**

- Valkey pub/sub 的投递语义是 **at-most-once**：发出即不再重发，订阅者断线或处理失败就永久丢失（Redis 文档 *Delivery semantics*）。它同时明确说，要更强保证就用 Streams——Streams 会持久化、支持 at-least-once。
- 现场帧自己就写着「nothing stores it」「has lost nothing」。所以**丢得起这件事是既定语义，不是妥协**：用 pub/sub 正好，用 Streams 反而是白付持久化的钱去保证一件不需要保证的事。
- Postgres `NOTIFY` 也能跨进程，但：payload 默认小于 8000 字节；通知只在事务之间投递，事务里的 `NOTIFY` 要等提交；没在听的会话收不到，队列满会让 `NOTIFY` 的提交失败。对「每秒若干帧、帧体几十 KB、听的人可以不在」的现场帧不合适；对「轻量的变更多播」合适。
- 代价说清楚：pub/sub 的订阅端在集群模式下是全局传播的，要压扇出量得用 sharded pub/sub（`SSUBSCRIBE` / `SPUBLISH`）。我们今天是单 Valkey，用不上。

**租约与「谁在跑」**

- 进程内 `asyncio.Lock` 的本质问题是**它不承认进程边界**：两个进程各有一把，各以为自己是唯一的。
- 换成 Postgres 的 advisory lock 有个坑：advisory lock 挂在**连接**上，而我们的连接来自池。一条请求拿锁、另一条连接检查不到，正是最容易埋的那类雷。所以租约放 Valkey（带 TTL、可续租、可 `adopt`）比放连接锁更稳——`admission.py` 已经把这套写完了。
- 「这一轮还在不在跑」的正解是**库里一行开着的、带最近活动时刻的记录**，而不是任何一份内存里的集合。`liveness.py` 已经是这个形状，只是兜底还没转正。

## 5. 反方意见

**反方一：别退役锁，用削 CPU 达标。** #1510 第 6 节本来就列了三条路，第 1 条（削每请求 CPU）和第 3 条（缓存这一夜清单）都能冲 1 秒，完全不动进程模型。而且第 1 条已经削过一轮（p50 1418 → 881 ms），还有余量可挖。退役锁要动 6 处状态、5 个步骤，风险和工期都不小；如果 1 秒的目标只差一倍，削 CPU 是更便宜的那条。

**为什么仍然建议退役**：削 CPU 是每次代码改动都要重新守的堤坝，加 worker 是一次性买到的余量；而且 #1510 的三处单进程假设**迟早**要处理——哪怕今天不加 worker，滚动部署就会踩到 `hook_work` 随进程死、现场帧跨不过去这两条。退役锁解决的不只是吞吐，是「一条轮次横跨两条进程」这件事本身。

**反方二：用粘性路由代替，一间房固定落在一条进程上。** 能减小消息落错进程的概率。

**为什么否掉**：减小不等于消除。重连、部署、客户端开第二条连接都会打穿粘性，而打穿的那一次正好是 2.2 里那个静默的「投递失败」。粘性把故障从「看得见」变成「偶尔才出现」，更难查。

**反方三：全搬库里，别引 Valkey。** 少一个依赖。

**为什么否掉**：准入和租约这一层 Valkey 已经写完了，带续租、带 `adopt`、带 Lua 原子性；重建在 Postgres advisory lock 上会踩「锁挂在连接上」的坑，还丢掉 `adopt()`。库里该放的是**事实**（谁在跑、这轮的事实），不是**临界区**。

**反方四：先别动，等 #1510 的削 CPU 出结果。** 如果削完就达标，这些步骤可以无限期推后。

**回应**：可以，但第 2 步（「在跑」变成持久事实）和第 5 步（周期任务单实例化）不依赖加 worker，**现在就该做**——它们修的是滚动部署下的正确性，不是吞吐。

## 6. 迁移路线

每一步都是独立可合入的一次改动，合进去之后系统仍然是对的；每一步都不加 `--workers`。

### Step 1 — 座位与房间准入改成共享租约

- `seat_admission` 的签名不动（还是那个 `asynccontextmanager`），里面那把 `asyncio.Lock` 换成 Valkey 的 `admission.Hold`，key 按 `(topic, agent)`。
- `live.seat_locks` / `live.topic_locks` 改成同一把租约的别名，调用点一格不动。
- Valkey 不可用时**退化回进程内锁**，行为回到今天。

**合进去之后仍然正确**：两条进程再也拿不到同一个座位的准入；消息落到没持有座位的那条进程会等租约，而不是被放出去谎报投递失败。单进程行为不变，只多一次 Valkey 往返。

**租约和 `asyncio.Lock` 不是一回事，三点要说清**：

1. **可重入。** `seat_admission` 现在靠 `ContextVar` 认「这把锁是不是当前任务已经拿着的」，换成租约后要保留这条——同一个任务嵌套取同一个座位的准入不能自锁。
2. **不公平。** `admission.Hold` 只是 `SET NX PX`，谁先抢到谁上，没有先来后到；要排队得用 `admission.Pool`（它有 FCFS 队列和 `queued()`）。座位准入今天也没有公平性，所以行为不退步，但要写进文档，别让人以为换了之后变公平了。
3. **持有者死了要等 `LEASE_S`（30 s）才放。** `asyncio.Lock` 是任务一取消就放。差别只出现在崩溃路径，正常路径由 `Slot.release()` 主动放。

**已开 PR #3786**（本步只此一件，待验收）。代价实测：一次无人等的准入发三条命令、三个来回——`SET NX PX` 拿下、`admission.enter` 紧接着给刚写上的 TTL 续的 `EVAL`、放手时的 `EVAL`；回环上对真 Redis 8.6.3，两次跑分别 p50 0.73 / 0.74 ms、p95 0.80 / 1.37 ms，一个裸来回（PING）p50 0.19 ms。等不到座位的那一方每 `POLL_S` 一个来回，不是 0（实测：等在 3.2 s 的持有者后面，3.02 s 里问了 4 次座位，共 7 条命令）。复现：`backend/scripts/measure_seat_lease.py --redis-url <服务器>`。**dev 那台的数拿不到**：做这一步的工作机连不到 dev（域名解析得出内网地址，连过去超时，Valkey 端口也不通），换 dev 的地址跑同一条命令才有那个数。三条命令里 `SET` 和紧随的续租可以合成一个来回（量到 p50 0.35 ms），少发那条属于 `admission.enter`，项目准入也走它，没在本步改。

**代价**：每次准入多一次往返。要量，不接受「应该不慢」。

**怎么验证**：两条进程同时对同一个座位投同一条消息，只应开出一轮；把其中一条进程 `kill -9`，30 s 内座位应重新可用。

### Step 2 — 「这一轮在跑」变成持久事实

- `AgentTurn` 那行开着的记录升为**权威**；`active_turn_ids` 降级为写穿的缓存，读不到就回库。
- `mark_turn_active` / `mark_turn_inactive` 保持原签名，内部顺带写库。
- `consuming_work_id` 要能认出跑在别的进程上的那一轮（`pending_messages` 里那句 `busy` 查询升上来共用）。

**合进去之后仍然正确**：侧栏的「运行中」、消息的放行，在 N 条进程下都是对的；一条消息不再因为持有轮次的是另一条进程而被扣住。

**怎么验证**：在 A 进程开场一轮，在 B 进程投递同房间的消息，它应进 A 那一轮而不是新开一轮；`GET /awaiting-me` 的「运行中」应认出 A 的轮次。

### Step 3 — `hook_work` 的事实半搬进库里

- 拆 `HookWorkState`：事实半落一行轮次状态（并进 `AgentTurn` 或新表），手上那半留在进程内。
- 事件路径按 `(topic, work_id)` 回库取，取不到就走今天的降级。
- `steps` / `known_commits` / 三个 `last_*_at` 不迁——它们本来就说好了重启可以丢。

**合进去之后仍然正确**：轮次事件可以被任何一条进程处理；部署到一半的轮次不再丢掉持久化上下文。

**怎么验证**：一轮跑到一半重启后端，事件继续落库、归到同一轮上；`steps` 和提醒时刻允许丢，要确认丢的确实只有它们。

### Step 4 — 现场帧跨副本扇出

- `InProcessBroker` 保留，作为本进程订阅者的扇出层；发布侧多一层 Valkey pub/sub 转发到别的进程，别的进程再喂给自己的订阅者。
- 语义**保持 at-most-once**，不换 Streams。`replay=True` 的补帧缓冲可以先留在本地（跨进程补帧是第 4 步的可选项，不是必须）。

**合进去之后仍然正确**：连在 B 进程上的页面看得到 A 进程写到一半的字。错过一帧的损失和今天一样——下一次帧或成品块带全文。

**怎么验证**：两个 worker，页面连 B，轮次跑在 A，现场帧应出现在页面上；断开重连后由下一帧／成品块补齐，不出现空指针或卡住。

### Step 5 — 周期任务单实例化 + 网关 key 改行锁

- 周期任务单实例化：这把租约已经有了，就是 `core/job_runs.py` 的 `JobRuns.claim`（一个任务名一行，`PeriodicRunner._take_tick` 每拍去领，拿到才跑、过期换人）。本步只把口径写清楚，不新增机制；两个进程同时跑一个周期只起一次的验证，就是 `test_two_processes_claiming_at_once_only_one_gets_the_tick` 与 `test_a_job_scheduled_on_every_process_runs_once_a_tick`。把周期任务搬出归属锁是迁移顺序 3e，一条任务一个 PR，不在本步。
- `_gateway_lock` 去掉，写路径整段握着 `repo.lock_settings(project)`；**读路径保持不拿锁**（那 5.8 s 尾巴的教训）。
- `HostMemory` 的读数挂到共享短 TTL 上。
- `pending_messages._runner` 的 nudge 改成广播或按租约认领。（要做它得先有 Step 1–4 的地盘：广播是第 4 步的跨进程通道，按租约认领要有人接得住轮次。不在本步。）

**合进去之后仍然正确**：清扫、提醒、dream 全机一次；两个进程不能各铸一把项目 key。

**怎么验证**：同一个项目连开两轮，`project.settings` 里的 `llm_gateway_key` 应还是同一把；两个 worker 同时跑一个周期，日志里每样任务只该起一次（上面两个测试即是）。

## 7. 停在第 1 步 / 第 2 步会坏什么

**先说公道话：如果只跑一个进程，Step 1 和 Step 2 合进去之后什么都不坏。** 多的只是一次 Valkey 往返和几条库查询。这两步的意义是把「必须只有一个进程」这条隐含约束拆掉。

**停在第 1 步（只做准入租约）**：

- 拿不到：消息不再被谎报投递失败，同一个座位不会被两条进程同时放行。这是 2.2 里最疼的那条，已经修好。
- 还会坏：
  - `active_turn_ids` 仍是每进程一份。消息落错进程时会**等**（正确，因为租约说有人占着），但只能等周期清扫来接，最多拖 `queued_message_sweep_interval_s`。
  - 部署到一半的轮次仍然丢 `hook_work`——今天就丢，不是新坏的，但也没修。
  - 连在 B 进程上的页面仍然看不全 A 进程的现场帧。跑一个进程时无所谓，跑两个就看得见了。
- **结论：够不够取决于要不要加 worker。** 只要还跑一个进程，第 1 步是纯加固；要加 worker，第 1 步是必要不充分。

**停在第 2 步（准入 + 「在跑」都是持久事实）**：

- 拿不到：准入和排班都对了，侧栏的「运行中」跨进程也对了。**`--workers 2` 可以跑起来接请求**——这是这条路线的分水岭。
- 还会坏：
  - `hook_work` 仍随进程死。一条轮次的事件（进度、清单、工具步、提示）落在不是它开场的那条进程上时，要么归错人，要么落不下去。滚动部署时尤其明显。
  - 现场帧跨不过去：连在 B 上的页面看不全 A 的打字中。用户看到的是「卡住了」，实际是帧没送到。
  - 周期任务跑 N 份：重复提醒是噪声；重复 dream 是白烧钱；`sweep_orphans` 有可能去收另一条进程正在跑的轮次——第 2 步给了它看得到的事实，第 5 步才保证它不乱动手。
  - `_gateway_lock` 仍可能让两条进程各铸一把项目 key。
- **结论：可以开 worker，但只适合开 2 个、只适合短平快的请求路径。** 滚动部署和「一条轮次横跨两条进程」这两件事在第 3 步之前始终是坏的。

## 8. 不在这份文档范围内的

照任务范围：

- 真的去加 `--workers`（那是这条路线走完之后的事）；
- #1510 的 20 并发读路径 CPU（归 #1510）；
- 这一夜清单的缓存／增量（会改「一屏上该有谁」的语义）；
- 任何触碰正式部署链的改动（只走 dev）。

## 9. 未决

- Step 1 那次 Valkey 往返的实际代价，要在 dev 上量，不接受估算。
- Step 3 的轮次状态表是并进 `AgentTurn` 还是新表，等真做那一步时按查询形状定。
- Step 4 的跨进程补帧要不要做，取决于第 4 步实测掉帧的表现。
