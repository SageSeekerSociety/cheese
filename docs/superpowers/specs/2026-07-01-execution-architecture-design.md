# 执行架构:控制面 / 计算面解耦 + 执行档案 + 后台化订阅

状态：蓝图，待 andyl 审 + agent review。目标：一次性把"AI 这轮活在哪跑、用谁的模型/算力、谁来看"这套架构定成 future-proof 的形态，今天的单机功能作为默认实现跑在正确接缝上，未来加 cheesed/远端节点/GPU/pub-sub 全是"填实现"，不需要重构。

对齐 spec §9.1（算力调度=平台代码、资源池=自有+自带节点、按环境匹配/按额度并发/超额排队、三级可见）。

## 0. 一句话架构

> **后端（控制面，永远在线，不跑容器）** 把每一轮 AI 工作当作一个**作业（TurnJob）**，按项目的**执行档案（ExecutionProfile）**解析出"用什么模型/鉴权 + 派到哪个算力"，交给**计算面（cheesed 节点，跑容器）**执行；作业把**持久产物写进 DB（block = 事件日志）**、把**瞬时增量（delta）发到 broker**；前端只是**订阅者**，断开/重连/多端都不影响作业。

三个面，职责单一、互不杀：

```
┌─ 控制面 backend ───────────┐   派作业    ┌─ 计算面 cheesed(节点) ──────┐
│ API / DB / 话题树 / 调度    │ ─────────▶ │ ComputeProvider 实现        │
│ TurnRunner(编排,不执行)     │ ◀───────── │ 跑 claude+沙箱(容器/LXC)    │
│ Broker(发布/订阅)           │  流+产物    │ 拥有机器/Docker/GPU         │
└────────────┬───────────────┘            └────────────────────────────┘
             │ 订阅(WS/SSE)
        前端/多端(只看，断了不影响作业)
```

## 1. 不变量（架构红线，review 按这些挑）

1. **控制面不执行不可信代码**：backend 永远不跑 agent 的 Bash/容器；跑飞也 kill 不到它。
2. **作业不依赖观看者**：TurnJob 的产物落库与否，与有没有人/哪个连接在看**完全无关**。连接只订阅。
3. **持久与瞬时分离**：持久状态 = DB 里的 block（事件日志，可回放）；瞬时 = delta/状态帧（broker，丢了无所谓，重连回放 block 即可）。
4. **算力可插拔**：执行落在 `ComputeProvider` 接口后；今天单机 Docker = `LocalDockerProvider`，明天 cheesed/远端/GPU = 新实现，调度器与上层零改动。
5. **模型/鉴权按项目可配**：`ExecutionProfile` 决定模型+鉴权+算力+额度；默认指向"我们的 pool"；项目可选自带（per-seat 合规）。
6. **平台只认结构化**：感知 AI 仍只靠 cheese CLI 工具调用 + 我们的 token；不解析自然语言（CLAUDE.md 红线）。

## 2. ExecutionProfile（执行档案，按项目）

一份 profile 决定一轮怎么跑：
```
ExecutionProfile:
  name: str                 # "default" | "claude-opus" | ...
  tier: "default"|"testing"|"byo"   # testing=仅 dogfooding；byo=项目自带
  model: str                # glm-5.2 / claude-opus-4-8 ...
  auth:  AuthRef            # 我们的池凭据 / 项目自带 API key / 项目自带订阅
  compute: ComputeRef       # provider 名 + env_spec(镜像/GPU/...)
  quota: QuotaRef           # 并发/用量上限
```
- **解析**：`resolve_profile(project) -> ExecutionProfile`。项目没配 → **默认档（我们的 AI pool）**。
- **注册表**：可用 profile 在配置里登记（名字→模型/鉴权来源/tier）。`claude-opus` 标 `tier=testing`，**只允许 dogfooding 用**（个人座位=合规；产品多用户路径默认走 pool / API key）。
- **鉴权来源**：default=我们的网关凭据（env）；byo=项目存的凭据（加密，不进明文 DB）。今天先实现 default + testing(env 提供 Anthropic 凭据)，byo 留接口。
- **落点**：今天 model+auth 是全局 env（`AGENT_MODEL`/`ANTHROPIC_*`）。改为：每轮按项目解析 → 传给 AgentService（model + env 覆盖）。这样"按项目换模型"零侵入。

## 3. ComputeProvider（算力抽象 = cheesed 的契约）

```
ComputeProvider (Protocol):
  async def ensure(env_spec) -> SandboxHandle      # 起/复用该话题的沙箱
  async def exec(handle, argv, *, network) -> Stream
  def cli_path(handle) -> str                      # claude 进容器的 shim
  async def teardown(handle)
  def available() -> bool
  caps: {gpu: bool, ...}                            # 调度匹配用
```
- **EnvSpec**：镜像/依赖/**GPU**/端口/内存·CPU·pids 限额（spec §9.1 的"环境章节"）。
- 实现：
  - `LocalDockerProvider`（**今天的默认**）：把现有 `_sandbox_kwargs`/`claude-sbx` shim/容器命名·限额·复用，原样收进来。
  - `RemoteCheesedProvider`（**留接口，后续**）：后端经 cheesed 节点 agent 的 RPC 起容器；NAT 友好（节点出站长连，后端不主动连进去）；凭 per-turn 短时 token。
  - `SelfHostedProvider` / `CompetitionProvider` / `GpuProvider`：远端的特例（注册节点池 + caps）。
- **调度器**：`pick_provider(env_spec, project_quota)` → 匹配 caps（要 GPU 就找 gpu 节点）→ 项目额度限并发 → 超额**排队**。今天只有 Local 一个，直接返回它；多 provider 时才有真调度。
- **工作区位置**：`/work`(jj workspace) 跟着 compute 走。**"所有产出都是 git"是远端 compute 的前提**——节点 clone 干活、push/merge 回来。今天 Local 直接挂宿主机盘；远端走 git 同步。

## 4. TurnRunner + Broker（后台化 + 订阅）

把 converse 从"WS 连接的协程里"挪到"后台作业"：
```
TurnRunner:
  async def submit(topic_id, msg) -> job_id        # 起后台 task，按 topic 串行（锁在这）
  # 作业内部：resolve_profile → ensure compute → 跑 converse →
  #   持久帧(user_block/assistant_block/event_block/decision...) 写 DB
  #   瞬时帧(delta/state/todo) publish 到 broker channel(topic)
Broker (Protocol):
  async def publish(channel, frame)
  async def subscribe(channel) -> AsyncIterator[frame]
```
- **默认实现**：`InProcessBroker`（asyncio fan-out，单机够用）。**留接口** → 多实例时换 `ValkeyBroker`(pub/sub) 或 PG `LISTEN/NOTIFY`，上层零改动。
- **WS 路由变薄**：只做两件事——把用户消息 `submit` 给 runner；`subscribe(topic)` 把帧转给前端。**连接断开 = 退订**，作业照跑（满足不变量 2）。
- **重连/多端**：连上先 `GET /blocks` 回放持久产物，再订阅 broker 接实时增量。多端同订一个 channel。
- **去重**：runner 按 topic 串行（一个话题同时只一轮）；submit 时若已有在跑，排队或拒（语义二选一，默认排队）。
- **崩溃恢复**：作业是后台 task，进程重启会丢"在途的瞬时流"，但**持久产物已落库**；重启后未完成的轮可由调度补跑或标记中断（cheese 已提交的改动都在）。

## 5. 安全 / 信任

- **per-turn 短时 token**：cheese 回调后端的 token 按"项目+话题+本轮"最小授权、短时效（今天是进程级 `SANDBOX_TOKEN`，收紧成每轮签发）。
- **不可信节点**（self-hosted/赛题）：cheese API 面收窄到白名单写路径（已有 `_CHEESE_WRITE_PATHS` 闸门）；节点只能出站；产物经 git 回流而非直接写后端盘。
- **凭据**：byo 鉴权凭据加密存、不进明文 DB / 不进日志。

## 6. 今天建什么 vs 留接口（都不是半成品：接口完整、默认实现可跑、测试覆盖）

| 模块 | 今天 | 之后(填实现，不重构) |
|---|---|---|
| ExecutionProfile | ✅ 注册表 + 按项目解析 + default/testing + API + 测试 | byo 凭据加密存 |
| ComputeProvider | ✅ 接口 + `LocalDockerProvider`(收编现状) + 测试 | Remote/SelfHosted/Gpu/Competition |
| 调度 pick_provider | ✅ 接口(单 provider 直返) | 多节点 caps 匹配 + 排队 |
| TurnRunner + Broker | ✅ 接口 + 后台 runner + `InProcessBroker` + WS 改订阅 + 测试 | Valkey/PG broker；崩溃补跑 |
| cheesed 节点 agent | ⬜ 接口已定(RemoteCheesedProvider 契约) | 独立守护进程实现 |

> 原则：**接口是 future-proof 的核心**。default 实现今天就跑通+测试，远端实现是"按已定接口填空"，不动上层——这是"一次性搞顺、不留重构债"的含义。

## 7. 迁移顺序（每步可上线、可回滚）

1. ExecutionProfile（按项目模型/鉴权）——不动执行路径，先把"用谁的模型"参数化。
2. ComputeProvider 抽象——把现有沙箱收进 LocalDockerProvider，行为不变。
3. TurnRunner + InProcessBroker——把 converse 挪后台、WS 改订阅，行为不变（断开不再杀活已经在 #4 验证）。
4. （后续）RemoteCheesedProvider + ValkeyBroker + 调度排队 + GPU。

## 8. review 该挑的点（给审查 agent 的清单）

- 接口是否真能容纳远端/GPU/cheesed 而不漏抽象（NAT、工作区 git 同步、caps 匹配、token 信任）？
- 持久/瞬时切分是否干净（重连回放是否真无缝、有没有"只在内存里的状态"）？
- TurnRunner 串行/去重/崩溃恢复语义有没有洞？
- ExecutionProfile 的 byo/testing 合规边界是否清楚、凭据是否安全？
- 与现有红线（平台只认结构化、cheese 写路径闸门、jj/git 采纳）是否冲突？
- 默认实现是否真"行为不变"、有回滚路径？
```

---

# v2 修订（吸收对抗审查的 12 条发现）

审查证明 v1 蓝图有真洞，尤其"明天=填实现、不重构"对其中三项**不成立**。以下逐条修订，作为权威版本（与上文冲突处以本节为准）。

## R1（BLOCKER）单写者不是 broker 问题，是 lease 问题 —— 锁/队列/broker 必须一起下进程
v1 把"多实例"只挂在 broker 上，错了。`asyncio.Lock` 是进程内的；两个后端副本各自有一把锁 → 同一话题 split-brain（两个 `docker exec` 抢同一 `cheesex-sbx-<topic>` 容器 + 同一 `/work` + 同一 `~/.claude` session，`claude --resume` 互踩 → session 损坏、产物重复）。
**定稿：** 单写者用**持久 lease**（PG advisory lock / `topic_turn` 行 `FOR UPDATE` / Valkey `SET NX PX`），在 `TurnRunner.submit` 获取、由执行节点持有、带续租 + 到期自动释放（崩溃自愈）。**lease + 持久队列 + 跨进程 broker 是一个耦合变更，不是三个独立"换实现"。** §6 表 TurnRunner 行的"之后"补：`distributed turn lease + 持久队列`。`_topic_locks` dict 同时改为带淘汰（今天的小泄漏，也是"从没按非进程内设计"的信号）。

## R2（BLOCKER）ComputeProvider 的接缝画在了 SDK 之下，必须上移成"turn 执行器"
真执行路径是 `AgentService.stream_reply` 在**后端进程**里构造 `ClaudeSDKClient` 并把 `cli_path` 当**本地子进程**拉起。`exec(argv)`/`cli_path` 根本不在 turn 路径上。要做远端节点，返回不同 `cli_path` 没用——整个 `ClaudeSDKClient` + 子进程 + `AgentEvent` 翻译都得搬到节点、把事件流经 RPC 回传。
**定稿：`ComputeProvider` 只回答「在哪台机器上」——起机器、备好工作区、事后快照。
「上面跑的是什么」是另一个接缝**：长在那儿的会话是 `AgentRuntime`
（ensure / send / read / interrupt / close），每轮起一个进程的是 `TurnStream`
（`run_turn`）。`cli_path`/`exec` 降级为后者的内部细节。

一个接口同时管这两件事，就等于「换 harness」和「换机器」必须是同一个开关——而那
正是 `AgentType.harness` 存在却没人读的原因。

## R3（SERIOUS）重连不无缝：整轮产物在 tx2 收尾前只活在瞬时流里
现状：流式中只发 `delta/tool/state/todo`；**所有持久 block（现场事件、assistant、行动卡、usage、`set_session_id`）都在流结束后的 tx2 才落**。`InProcessBroker` 无 backlog，订阅者只收订阅之后的帧。→ 手机中途打开/掉线重连，`GET /blocks` 只看到用户块，turn 看着像卡住直到最后一坨蹦出来；missed 的 delta 永久丢失。
**定稿：** (i) **增量落库**——assistant 块先建、delta/event 边流边 append；(ii) 给每轮一个 **`turn_id` + 单调 sequence**，帧与块都带，broker 配每轮 replay buffer；重连按"blocks + 自 cursor 起的瞬时 backlog"补齐。今天 block/frame **都没有 turn_id/sequence**，连瞬时流与最终块的排序/去重都未定义——本次必须把 cursor 定义进协议。

## R4（SERIOUS）崩溃恢复不安全：cheese 副作用各自独立提交，tx2 整体丢 → 补跑双重执行
`cheese doc/decision/notify/split/milestone` 是**流式中各自提交的独立 REST**；assistant/现场/usage/session_id 在 tx2。崩在中间 → 决策/通知/子话题留下了、session_id 丢了、没有叙述块；"补跑"会再发一遍 → 重复决策/双通知/重复子话题。**无任何幂等键。**
**定稿：** (i) 每个 cheese 写带 **per-turn 幂等键(`turn_id`)**，闸门/处理器去重；(ii) `session_id` 在**首个 AssistantMessage 即落**，不等 tx2；(iii) 恢复语义写死：turn 由 `turn_id` 标识，重放幂等，"补跑"=续同一 `turn_id` 而非新轮；默认补跑 vs 中断二选一并写明部分副作用如何对账。

## R5（SERIOUS）cheese token 全局无作用域 + 闸门漏 /doc /split；信任边界当前是空话
两点：(1) `SANDBOX_TOKEN` 是单一全局值，端点的 project/topic 取自 **URL 而非 token**——A 项目容器能拿同一 token 写 B 项目。(2) `_CHEESE_WRITE_PATHS` **没盖 `PUT /doc` 和 `POST /split`**。
**重要纠正（实现细节，审查未及）：** `/doc`、`/split` 是**双写**（前端人工存文档/拆话题也走它，且前端无 token），当前单机可信 MVP 里**整个浏览器 API 本就无鉴权**，所以这俩开放是与现状一致、**非新增可利用漏洞**；naive 加闸门会**直接 break 人工存文档/拆话题**。
**定稿：** 信任边界与"浏览器无鉴权"一起做——(i) **用户鉴权**上线后，浏览器写带用户身份；(ii) cheese 写改 **per-turn token 绑定 {project,topic,turn_id}**，闸门校验 URL 的 project/topic 与 token 声明**一致**（不只 `compare_digest` 全局值）；(iii) 闸门改**默认拒绝**（前缀白名单，新端点默认关），双写路径按"来源"区分（用户 token vs turn token）。

## R6（SERIOUS）模型凭据明文进沙箱，agent 有 Bash + bypassPermissions 能直接 `env` 偷
`ANTHROPIC_AUTH_TOKEN` 经 `docker exec -e` 进容器，agent `env|grep ANTHROPIC` 就能读。默认池=所有租户共享一把网关 token 可被任意 agent 读；testing Opus=真 Anthropic key 暴露给被沙箱的模型代码；远端/赛题节点=**节点运营方直接收割**你的凭据。
**定稿：** 池凭据**永不进入不可信环境**。模型调用经**后端侧网关**：按 {project,turn} 签发短时、限频的临时 token 并计量；容器只拿这个一次性 token（或后端直接代理模型 API，节点永不持凭据）。`RemoteCheesed`/`Competition` 必须如此。

## R7（SERIOUS）合规：testing/BYO 无强制；per-project ≠ per-seat
v1 只有标签没有强制。**已在本轮实现**：`resolve_profile` 对 `tier=testing` 硬性要求 owner ∈ dogfood 白名单，否则回落默认 + 可审计（见 `profiles.py`、`test_profiles.py`）。
**补定稿：** BYO 合规在**座位**层定义——订阅凭据只能背书归属于**该个人**的轮；**多用户/共享项目的 BYO 必须是 API key（商用条款），不能是个人订阅**。§2 写明此区分。凭据加密要写清方案（信封加密/KMS、主密钥位置、轮换）——"加密存"但密钥贴着密文=自欺。

## R8（SERIOUS）无每轮墙钟超时/取消 → 卡死的轮永久占锁，话题死锁
`stream_reply` 无超时；`_stream_with_retry` 只在产出前重试；WS 路由已改"drain 到完成、断开不取消"。卡在流中（网关 stall / 容器内死循环）→ 永不完成、tx2 永不跑、话题锁永久持有 → 该话题后续全部阻塞，无 kill 开关。
**定稿：** runner 强制**每轮墙钟预算 + 每 Bash 预算**；超时取消 SDK client、`docker kill` exec、释放 lease、写一条"interrupted"块。R1 的 lease 到期保证死节点不永久占话题。

## R9（SERIOUS）Provider 契约缺回调通道/工作区同步/结构化 caps/容量/回收
v1 的 `ensure/exec/cli_path/teardown/available/caps` 撑不起远端：cheese 硬编码 `host.docker.internal`（NAT 后不可达，且无处取回调 URL+scoped token）；`ws.snapshot_worktree` 在**本地**跑（远端话题路径不存在，采纳/diff/预览全废）；`{gpu:bool}` 表达不了 A100×2；`available()->bool` 给不出实时负载/空位（排队/"看排队情况"无数据源）；`teardown` **无人调用**（容器 `sleep infinity` 永驻、无 idle 挂起/归档回收/孤儿 GC/重连对账 → 每个碰过的话题=永久泄漏一个容器）。
**定稿：** 契约补：每轮 `callback_endpoint + scoped_token` 签发；显式工作区生命周期 `materialize/checkpoint/fetch_refs/get_diff/teardown`（采纳/diff 与位置无关）；结构化 `caps`（gpu 型号/数量/显存、arch、镜像）+ 实时 `capacity()`；**对账循环 + 孤儿 GC + idle 挂起/归档回收**。在这些方法存在前，"所有产出都是 git→远端前提"只是断言、无机制。

## R10–R12（MINOR）
- **R10**：WS 必须**先订阅再 submit**（或 broker 从 `turn_id` 创建即缓冲），否则 submit 与 subscribe 之间的首帧/快轮整轮丢——并入 R3 cursor。
- **R11**：`InProcessBroker` 跨不了进程，"WS 降为订阅者"今天**只单副本成立**。诚实结论：单机默认在这些接缝上成立，但**多实例需要一组耦合的新不变量**（单写 lease + 持久队列 + 跨进程 broker + cursor 回放 + 远端 SDK 搬迁 + 工作区同步），不是各自独立"换实现"。§6/§7 表已据此修正措辞。
- **R12**：`pending` 历史窗口要**按 `turn_id` 限定**，否则排队的第二轮会重复处理第一轮的用户消息；并写明用户块在 submit 时还是 turn 时落库。

## 修订后的"一次性"判断（诚实）
审查证明：**整套分布式执行面不可能一轮实装到无懈可击**——R1/R2 要改契约、R3/R4 要 turn_id+幂等贯穿、R5/R6 要重做信任与凭据。硬塞一轮反而造出它要避免的洞。
**所以正确的"搞顺"是：架构（本文档 v2）已自洽且经对抗审查；地基里能独立交付的先落地（ExecutionProfile + 合规护栏 ✅ 已做、已测）；其余按 §7 顺序、每步带 turn_id/lease/cursor 的正确语义增量实装、每步可上线可回滚。** future-proof 体现在**契约对了**（v2 的 run_turn/lease/cursor/scoped-token），远端/GPU/cheesed 是按已定契约填空。

---

# v3 修订（AI 与 compute = 两个对称的 Pool）

andyl 定调：**AI 和 compute 各自抽成一种"池"，同一套形状**。把 §2（ExecutionProfile）和 §3（ComputeProvider）统一到一个通用 `ResourcePool` 抽象，两边是它的两个实例。这让"默认我们的、可选 BYO/外部、按项目配、配额、计费、三级可见"这套东西**只写一次**，AI 和 compute 复用；将来第三种可池化资源（如存储/密钥）也照搬。

## 通用抽象：ResourcePool[Provider]
```
ResourcePool[P]:                      # P = AIProvider | ComputeProvider
  providers: dict[name, P]            # default(我们的) + byo/external/self-hosted/competition
  default_name: str
  select(project, owner) -> P         # 解析 + 合规护栏（未配/无凭据/越权 → 回落 default）
  scheduler: 配额/并发/排队（按项目额度）
  meter(usage) -> 计费                 # 我们的池收钱；BYO 计到项目自己的账
  visibility: 话题 / 项目 / 机构 三级看板
```
不变量（两池共享）：①未配→默认我们的池；②default 永远可用、绝不回落到死档；③tier=testing/byo 有合规护栏；④用量计量 + 三级可见。

## 实例一：AIPool（= 现在的 ProfileRegistry，已建）
- `P = AIProvider`：model + provider 鉴权（base_url/token）+ tier。
- `select` = `ProfileRegistry.resolve`（**已实现**，含 dogfood 合规护栏）。
- default = 知是网关池(GLM)；`claude-opus`=testing；byo=项目自带 key（per-seat 合规：订阅仅个人、多用户项目须 API key）。
- 计费：默认池按 token 计到知是；byo 计到项目。
> 即：`profiles.py` 就是 AIPool 的首个落地，只是名字叫 ProfileRegistry——v3 起对齐命名/语义为 AIPool。

## 实例二：ComputePool（待建，按 v2 的 run_turn 契约）
- `P = ComputeProvider`：`run_turn(...)->事件流` + 工作区生命周期 + caps + capacity（见 v2 R2/R9）。
- `select`/`scheduler` = `pick_provider`：按 env_spec 的 caps（GPU 等）匹配 + 项目额度限并发 + 超额排队。
- default = `LocalDockerProvider`(收编现状)；之后 Remote/SelfHosted/Gpu/Competition。
- 计费：默认池按 compute-time/GPU-hours 计到知是（**这是知是作为算力平台的主营收入**）；BYO/赛题算力计到对方/不计。

## ExecutionProfile = 两池各选一 + 配额
```
ExecutionProfile(project):
  ai      = AIPool.select(project)        # 用谁的模型/鉴权
  compute = ComputePool.select(project)   # 派到哪种算力（含 GPU/赛题）
  quota   = {ai_tokens, compute_time/gpu_hours, 并发}
```
两轴**正交**：可以"我们的模型 + 赛题的 GPU"，也可以"项目自带 Claude key + 我们的池"。项目级配置就是各池里各选一个 + 配额；默认两边都指我们的池。

## 对落地的影响（顺序不变，命名对齐）
1. 把 `ProfileRegistry` 视作 `AIPool`（已建，含合规护栏 + API + 测试）。
2. 建 `ComputePool` + `LocalDockerProvider`（v2 的 run_turn 接缝）——与 AIPool **对称**：providers 注册表 + select + 默认实现 + 测试。
3. 两池接进 `ExecutionProfile`（项目各选一）+ 计量计费 + 三级看板。
4. 远端 compute provider / 多 AI provider 按已定接口填空。

---

# v4 修订（算力：归属=团队 context / 选择=会话级 / 默认=项目 sticky / session_id 冻结锁）

v3 把 compute 选择挂在**项目层**（`ComputePool.select(project)`）。经与 andyl 过 UI/IA 后细化：算力的**归属**在团队(context)、**选择**在会话(topic)、**默认**靠项目 sticky 记忆；并把"话题实例化后钉住、绝不漂"这条**数据正确性红线**写死（原始 PR bug 的定稿修复）。与上文冲突处以本节为准。

> 前提：产品形态 = **一套代码的 N 个隔离部署**（每机构一个 + 消费版），deployment = 机构/租户。v3 "三级可见"的**机构级 = 部署本身**，不再是 in-app 多空间。归属层（团队/工作区一等公民、项目归属收敛到团队）由 **Space refactor**（独立 issue #47）承载；本节算力模型踩在它上面，但**下面的 §affinity 冻结不依赖它，可独立落地**。

## §归属：算力属于团队(context)，deployment 隔离机构

- **Device = 一个 daemon 实例**（enroll 一次 = 一个 config/WS/在线态 = 一个 `device_id`；**一台机器可跑多个**：多用户/多项目各自 daemon）。归属一个**团队/工作区**——个人 = 单人工作区（真实的 solo context，非虚）。
- **可见性 = 同 context**：一个项目能选的算力 = **它所属团队的设备 + 平台**。无跨团队借用；想共享给别的团队 = 改设备归属（显式动作）。deployment 已隔离机构，故无跨租户算力。
- 这取代了早先的 `device_project` N:N「登记」争论：归属在团队、项目可见性由团队派生。

## §选择：团队池 → 项目 sticky → 会话选择（三级，各管各的）

| 层 | 管什么 | 载体 |
|---|---|---|
| **团队** | 算力池（self-hosted 设备 / 虚拟 GPU 节点 / 平台默认）+ 团队默认 | 团队页（成员+算力） |
| **项目** | **记住上次用的算力**（sticky，初始=团队默认），作新会话起点 | `Project.sticky_compute`（隐式记忆，**无独立配置页**） |
| **会话(topic)** | 建时默认沿用项目 sticky，**发第一条消息前可切**；切了同时更新项目 sticky | `Topic.compute`（冻结的 target） |

即：**团队给池 → 项目记住上次 → 新会话默认沿用、可改、发消息即锁**。零配置页，却不用每次手选。

## §affinity：实例化冻结（数据正确性红线，独立可落地）

- **分界线 = 这个话题有没有 `agent_sessions` 行**（首轮捕获）。没有 = 未实例化，算力可切；有 = 已落地，锁定。
- 首轮把选择**物化**成具体 compute target 写回 `Topic.compute`，此后只读。
- **自托管设备离线 → 该会话排队 / 报"算力离线"，绝不漂到别处**：工作树 + `~/.claude` session 都在那台机器，漂移 = 静默丢历史 + resume 损坏。这条是原始 bug（"话题实例化后会漂到别的在线设备"）的定稿修复，**不依赖归属/IA 重构，可先落地**。
- 平台/虚拟节点无漂移问题（provider 内部保证逻辑节点稳定，虚拟化 reuse 对上层透明）。

## §compute target 多态（承接 v2 R2/R9 + v3 ComputePool）

- 一个 target 解析到一个 provider：`LocalDocker`(平台默认) / `SelfHosted`(设备) / `GpuProvider`(**虚拟节点**如 H100:8——平台 GPU 池 provision + **虚拟化 reuse**)。
- **平台不是特例，是"默认那个 provider"**：解析 + 冻结对所有 target **统一**；平台/设备只在最后 provider 派发时分叉。
- 每个 provider **自管 backing 与 reuse**：GPU 池的虚拟化/复用是 `GpuProvider` 内部实现，**不进** device↔项目 的关系模型（澄清早先"团队共享物理 GPU 靠 N:N"的误判——那是平台侧虚拟化）。

## §对 v2/v3 落点的修正

- v3 `ComputePool.select(project)` → 细化为 `select(team-context)` 得池、`resolve(topic)` 得该会话冻结的 target。`Topic` 增 `compute` 字段；`Project` 增 `sticky_compute`；冻结分界线是「这个话题有没有 `agent_sessions` 行」。
- v2 R1 `topic_turn` lease / R2 `run_turn` 契约不变；**affinity 冻结与 lease 正交**（lease 管"同话题串行"，affinity 管"钉在哪台"）。
- UI 落点（实现细节，非本 spec）：会话算力选择器落在**新建话题流程 / 草稿话题 composer 那条**（`# 本话题 · @芝士` 旁），锁定态显示 🔒。
