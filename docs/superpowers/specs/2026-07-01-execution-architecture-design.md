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
