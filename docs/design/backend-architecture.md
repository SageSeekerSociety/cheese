# Cheese 后端架构（三平面）

本文件描述 `cheese-backend-py` 后端的整体分层，以及"知是 2.0"产品设计里每个功能未来落在哪里。它是 [`cheese-agent-layer.md`](./cheese-agent-layer.md)（芝士接入层的自包含设计）的上层视图：接入层只是这里 `agent/` 平面里的一块。阅读本文不需要其他文档。

设计目标：**为产品设计（`知是2.0产品设计.md`）里的所有功能预先开辟好空间，让未来的改动是"往空目录里加文件"而不是"搬动/重构现有结构"。** 目录已作为空骨架建好（每个目录一行 README 说明归属）。

---

## 1. 三个平面 + 横切层

服务端代码分三个平面，外加若干横切层。芝士引擎（`agent/`）与机器管理器（`compute/`）**都不是业务域**，各自独立于 `domain/`。这对应技术设计里的划分：业务层 / Agent 编排器 / Agent 适配器 / 机器管理器。

```
backend/app/
  domain/     业务层 Business Layer —— 纯业务，完全不感知 AI 怎么实现
  agent/      芝士引擎 Agent Engine —— 业务与"AI 实现"之间的解耦层
  compute/    机器管理器 Machine Manager —— 执行环境(也服务人用的工作台)
  api/routes/ Web 层 —— 每资源一个 router，调用 domain 服务
  auth/       权限注册(每域)
  core/ db/ middleware/ common/   配置 / 会话 / 中间件 / 共享
```

三条解耦缝，都设计成"只增不改"的接口：

1. **业务层 ↔ 芝士引擎**：编排器**直接调用业务函数**（不套事件总线），并在这一刻按 agent actor 的真实权限鉴权。业务层不感知 AI。
2. **编排器 ↔ 适配器**：抽象 `Agent` 接口（`start/stop/send…`）。换 agent 框架只改适配器，工具/技能/业务代码不动。
3. **连接器 ↔ 后端**：冻结的公共协议（带版本、只增不改，见 `cheese-agent-layer.md` §11）。

---

## 2. 目录全图与产品功能映射

每项标注它服务的产品设计 § 和 MVP 阶段（P0–P6，见产品§13）。已存在的域标 [现有]，新建骨架标 [新]。

```
backend/app/
  domain/                       业务层
    block/          [新] §5   P0   万物皆块：双树(reply_to/struct_parent)+refs —— 数据地基
    document/       [新] §7   P2   活文档：块的文档树投影、docs-in/docs-out
    review/         [新] §4.4 P3   验收：reviewer 分配、accept、主分支保护、采纳=merge
    milestone/      [新] §7.2 P6   里程碑/日历：deadline 倒排、协议必做里程碑
    project/        [现有]§4  P0   项目=根话题=git repo、AI 模式、审批策略
    space/          [现有]§4  P5   机构：发布 Task Template
    task/           [现有]§4  P5   具体题目(+ 未来 task_template 协议)
    team/ user/     [现有]§4  P0   小队 / 用户(+ 未来 profile/portfolio §7.2)
    notification/   [现有]§8.5 P1  变更提醒 / 决策请求 / 分级
    topics/ ...     [现有]         1.x 标签式 topic 及其它 legacy 域(共存)
                                   注：2.0 的"话题"是命名空间隔离的新模型，见 §3 备注
  agent/                         芝士引擎(技术设计的"新增部分")
    interfaces.py   I2   P0   抽象 Agent 接口 + SessionHandle + AgentContext
    orchestration/  §9.1 P0   编排器：每话题串行队列、巡检、完成检测、汇报安全网、分身协调
    events/         I1   P0   (可选)EventEnvelope / Actor(User|Agent{本体|分身}) —— 编排器内部事件循环
    authorization/  §10  P0   编排器→业务/工具 边界的 per-actor 授权(安全支点)
    tools/          §9.1 P0   暴露给芝士的注解 Python 函数 → schema/CLI/权限投影；@tool 注册表
    adapters/       I2   P0   cheesed_codex/(经连接器) · naive_api/(无机器直连)
    connector/      -    P0   连接器服务端：控制通道、现场代理、接管仲裁、session store、工具 RPC
    memory/         §8.4 P0   记忆三层(个人/项目/技能) + OpenViking 投影
    skills/         §8.3 P1   技能(灵魂)：文档形态/对话风格/活动消化/巡检/验收/升级
    roles/          §8.2 P5   专家角色 = preset skills + role
  compute/                       机器管理器
    provisioning/   §9.1 P4   PVE LXC/VM 生命周期、Copy&Install cheesed、挂起/回收
    preview/        §7.1 P4   反向代理预览、code-server
    quota/          §9.1 P4   算力调度、项目额度、排队、三级用量可见性
    pool/           §9.1 P4   资源池：自有 PVE + 机构节点
  api/routes/                   Web 层：每资源一个 router(含现场/工具 RPC 端点)

connector/                      客户机组件(仓库根，Go；受冻结原则约束)
  cheesed/          私有 tmux 托管 agent、执行注入、空闲观测、终端中继、控制通道
  cheese/           schema 驱动的工具调用 CLI(与 cheesed 无共享代码)
```

**为什么 `compute/` 独立而非塞进 `agent/`**：机器管理器同时服务人（§7.1 工作台的预览/终端/文件面板）和 AI（agent 执行环境），不该只属于 agent 子系统。连接器的终端能力是 `compute/preview` 的一个消费者，不是它的拥有者。

**为什么 `agent/connector` 与仓库根 `connector/` 分处两地**：它们是同一座桥的两端——`agent/connector`（Python，平台侧控制面）与 `connector/`（Go，客户机侧，独立部署产物）。放两个代码库位置是刻意的：客户机产物不进 Python 后端。

---

## 3. 数据模型的落点

2.0 的核心是"万物皆块"（产品§5）：一条消息、一段文档、一个决策、一个附件都是 `block`，用两棵树组织——`reply_to`(对话树) 与 `struct_parent`(文档树)，加 `refs`(引用)。

- **`domain/block`** 是地基：`block` 表 + 双树 + refs。
- **活文档**不是独立表，是块按 `struct_parent` 的投影（`domain/document` 提供 docs-in/out 服务）。
- **话题**（产品§6）= 块升级后的形态 = 会话 = git 分支，可嵌套成树。它与现有 `domain/topics`（1.x 的标签式 topic）**语义不同、命名空间隔离**——2.0 的话题模型将新建（名称待定，如 `domain/thread` 或 `domain/topic_v2`，避免与现有 `topics` 冲突），本骨架先把地基（block）建好。
- **记忆**（`agent/memory`）是块树的投影，可随时从块树重建；主记忆源始终是块与活文档。

存储沿用现有约定：PostgreSQL + SQLAlchemy 2.0 async + Alembic 迁移。每个域内部沿用 `models.py / repositories.py / services.py` 的分层。

---

## 4. 一次请求怎么流动（两个方向）

**人 → AI（编排）**：用户在话题里发消息 → `api/routes` → `domain` 业务函数记录 block → 通知 `agent/orchestration` 编排器 → 编排器把输入注入该话题的 agent 会话（经 `agent/adapters` → 有机器走 `agent/connector` → 客户机 cheesed → tmux）。

**AI → 平台（工具调用）**：agent 调用 `cheese` CLI → 客户机 cheesed 本地 HTTP → 转发到后端 `agent/connector` 的工具 RPC 端点 → `agent/tools` 查表 → `agent/authorization` 按 agent actor 鉴权 → 调 `domain` 业务函数 → 结果回注 agent。平台**只**通过结构化工具调用感知 AI，绝不解析自然语言（产品§9.1）。

**现场（终端）**：浏览器 xterm.js ↔ 后端 `agent/connector` 代理 ↔ cheesed webtty ↔ tmux。只读=后端仲裁(不转发非接管者输入)，cheesed 全程透明可读写。

---

## 5. 迁移与现状

- 当前 `backend/app/connector/`（接入层的首个实现）将按本图搬入 `agent/`：传输/代理/仲裁 → `agent/connector`；@tool 注册表 → `agent/tools`；抽象 Agent 接口 → `agent/interfaces` + `agent/adapters/cheesed_codex`。纯机械迁移(移目录+改 import)，不改逻辑。
- 客户机上的 `connector/cheesed`、`connector/cheese`（Go）位置正确，保留。按"零业务逻辑"原则，`cheesed` 内的 driver 发射决策与接管锁判断将上移后端（`agent/orchestration`），cheesed 只留机制。
- 骨架目录已建好但多数为空；按 MVP 阶段(P0→P6)与产品优先级往里加文件即可，不需要重排结构。
