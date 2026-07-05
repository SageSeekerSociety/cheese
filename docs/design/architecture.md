# 知是 2.0 后端架构

本文是 `cheese-backend-py` 后端的**唯一架构总纲**,自包含,阅读它不需要其它文档。目标:

> **为产品设计（`知是2.0产品设计.md`）里的所有功能预先开辟好结构,让未来的改动是"往空目录里加文件",而不是"搬动/重构现有结构"。**

技术设计（`知是2.0技术设计.pdf`,细节多已过时但模块划分仍有参考价值）把系统分成:业务层 / Agent 编排器 / Agent 适配器 / 机器管理器。本文沿用这个划分,并把它落到三个代码平面。

---

## 1. 三个平面 + 横切层

服务端代码分三个平面,外加横切层。芝士引擎（`agent/`）与机器管理器（`compute/`）**都不是业务域**,各自独立于 `domain/`。

```
backend/app/
  domain/     Business Layer   纯业务,完全不感知 AI 怎么实现
  agent/      Agent Engine     业务与"AI 实现"之间的解耦层
  compute/    Machine Manager  执行环境(也是给人用的工作台)
  api/routes/ Web Layer        每资源一个 router,调用 domain 服务
  auth/       Authorization    权限引擎(每域注册)
  core/ db/ middleware/ common/  config / session / middleware / shared
```

三条解耦缝,都设计成"只增不改"的接口:

1. **业务层 ↔ 芝士引擎**:编排器**直接调用业务函数**(不套事件总线),并在这一刻按 agent actor 的真实权限鉴权。业务层不感知 AI。
2. **编排器 ↔ 适配器**:抽象 `Agent` 接口(`start/stop/send…`)。换 agent 框架只改适配器,工具/技能/业务代码不动。
3. **连接器 ↔ 后端**:冻结的公共协议(带版本、只增不改)。客户机产物永不进 Python 后端。

---

## 2. 聚合根:项目

**项目（Project）是整个系统的聚合根。** 它同时是:

- **根话题** —— 话题树的根;
- **一个 git repo** —— 代码与产出的载体;
- **执行环境的所有者** —— 机器/预览/额度都为项目而开;
- **AI 模式与审批策略的定义处** —— agent 能做什么的第一道天花板;
- **一个持权主体** —— 权限可以被"分享给项目"(见 §4)。

硬不变式(不留多态口子):

- **每个 agent 恰好属于一个项目**。没有项目的 agent 不存在。space/task 那一层**不**挂 agent。
- 项目 **1 : 多** 群聊,**1 : 多** agent。
- `block / thread(群聊) / session / memory / machine / agent` 全部挂在项目下,携带**强制** `project_id`,并 FK 向上收敛到项目。

一个项目内可以有**多个 agent(本体 / 分身)**,它们共享同一个 `project_id`;编排器在项目内协调分身。project : agent 是 1 : 多。

现有 `domain/project` 是这个聚合根的起点,将被扩成 2.0 形态(AI 模式、审批策略、根话题/repo 绑定、agent 归属),而**不是**另起炉灶。

---

## 3. 数据地基:万物皆块

2.0 的核心是"万物皆块"(产品 §5):一条消息、一段文档、一个决策、一个附件都是 `block`。

- **双树 + 引用**:`reply_to`(对话树) + `struct_parent`(文档树) + `refs`(跨块引用)。
- **append-only**:块只增不改,"编辑"是追加新块 + 引用旧块;软删只用于打墓碑。
- **群聊 / 话题** = 块升级后的形态 = 会话 = git 分支,可嵌套成树。它与现有 `domain/topics`(1.x 标签式 topic)**语义不同、命名空间隔离** —— 2.0 的话题模型**新建**(命名 `domain/thread`,避免与 `topics` 冲突)。
- **活文档**不是独立表,是块按 `struct_parent` 的投影(`domain/document` 提供 docs-in/out)。
- **记忆**是**项目块树**的投影,可随时从块树重建;主记忆源始终是块与活文档。

存储沿用现有约定(见 §11)。

---

## 4. 权限:把能力"分享给项目"

权限是本系统的安全支点。现有机制**复用,不绕过**;新增一层能力委托。

### 4.1 现有 RBAC(`app/auth/`,复述)

- 请求内 actor = `AuthUserInfo{user_id, system_roles, domain_roles}`。
- 引擎 = `permission_checker.check_permission(db, user, action, resource, resource_id, ctx)`:SUPER_ADMIN 短路 → 按资源映射到域 → 调该域 `role-provider` 从关系表取该用户对该资源的角色 → 角色层级展开 → 匹配 `PermissionConfig` → 评估 `PermissionRule`。
- 角色授予落在 DB 关系表(如 `TeamUserRelation.role`),定义落在代码(`app/auth/domains/*`)。
- 强制点 = 路由层 `require_permission(action, resource, id_param)`。

### 4.2 新增:能力委托给项目(可撤回、活委托)

产品语义:**一个权限的持有者(例如能看 X 的人)可以把这份能力"分享给项目";于是项目里的每个人、每个 agent、每个自动化都获得它。分享可撤回。**

落地:

- 新概念 `ProjectGrant{granted_by, capability=(action,resource,resource_id|scope), project_id, created_at, revoked_at}`。
- **活委托,不是快照拷贝**:一条 grant 仅当【授予者此刻仍持有该权限】时才有效。授予者自己失权 → 项目里继承来的这份**连带蒸发**;撤回 → 显式失效。所以鉴权时要对授予者做**实时再校验**,不能只查静态 grant 记录。
- 项目内主体(人 | agent | 自动化)对 `(action, X)` 的**有效权限** =
  - 自身本就拥有(现有 RBAC),**或**
  - 存在一条未撤回、覆盖 `(action, X)`、且授予者仍持有的 `ProjectGrant`。

### 4.3 agent 作为一等 actor

agent 不是自由 actor,是**某项目的 agent**。在 §4.2 之上再叠两道闸:

```
agent 能对 (action, X) 操作
  <=  §4.2 项目有效权限(继承自项目 grant / 或代表某人)
  AND 项目的 AI 模式 / 审批策略允许这一类动作
  AND (action, X) 落在该 agent 的工具白名单内
```

实现缝(现有 `app/connector` 已搭好脚手架,搬入 `agent/`):

- 扩 `ActorContext`:加 `kind("user"|"agent")`、**非空 `project_id`**、`on_behalf_of_user_id`、`tool 白名单`;全部带默认值,不破坏现有(frozen dataclass)。
- 新 JWT `type:"agent_session"`(短时、绑 `project_id`、带 scope);只在**连接器路由这一个信任点**解析成 actor。
- 写 `PermissionCheckerAuthorizer`:**复用** `permission_checker` 与各域 role-provider,按上式判定,拒绝抛 `PermissionDeniedError`。
- 授权发生在**编排器 → 业务/工具的单一 choke point**(`POST /connector/tools/call`)。actor 永远由服务端注入,绝不来自 agent 提交的 JSON。

---

## 5. 群聊与 agent 关注

- **agent 与群聊是多对多**:一个 agent 可在多个群,一个群可有多个 agent。
- 每个 **(agent, 群聊)** 维护:一个**关注水位** `cursor`(上次关注到哪) + 一个**关注策略**。
- **统一投递**:agent 被唤醒时,读 `[cursor, now]` 的全部积压,处理后推进 cursor。三种模式只在**何时唤醒**上不同,所以做成 `策略枚举 + 参数`,开闭可扩展:

  | 策略 | 唤醒条件 |
  |---|---|
  | `mention_only` | 被 @ 时 |
  | `idle_window(W)` | 距上次关注超过 W、且期间有**用户**发言时(去抖/批量) |
  | `all_user_messages` | 每条**用户**消息 |

  未来加 `keyword` / `on_decision_block` / `rate_limited(N)` 都是加一个策略,不改结构。

- **关键特性(防死循环)**:三种模式都只被**用户发言**触发,agent 自己的发言**不**触发别的 agent。多 agent 同群也不会互相刷屏。agent 之间要协作,只能靠**显式 @**(受控唤醒)。这是刻意保留的性质,不是巧合。

**待定的两个设计点**(见 §14):串行执行单元是 per-agent 还是 per-(agent,群);agent 能否 @ 另一个 agent。当前倾向:**per-agent 串行**(单一心智一次干一件事)+ **允许 agent @ agent**(协作的唯一受控通道)。

---

## 6. 芝士引擎(`agent/`)

平台**只**通过结构化工具调用感知 AI,**绝不解析自然语言**。

```
agent/
  interfaces.py   抽象 Agent 接口 + SessionHandle + AgentContext
  orchestration/  项目级编排:每群串行队列 / 去抖唤醒 / 完成检测 / 汇报安全网 / 分身协调
  authorization/  编排器->业务/工具边界的 per-actor 授权(安全支点,见 §4.3)
  tools/          @tool 注册表:注解 Python 函数 -> JSON-Schema / 权限投影
  adapters/       cheesed_codex/(经连接器,有机器) · naive_api/(无机器,直连 LLM)
  connector/      连接器服务端:控制通道 / 现场代理 / 接管仲裁 / session store / 工具 RPC
  memory/         记忆三层(个人/项目/技能),项目块树的投影
  skills/         技能(灵魂):文档形态/对话风格/巡检/验收/升级
  roles/          专家角色 = preset skills + role
```

`naive_api` 适配器复用现有 `domain/llm/LLMClient`(OpenAI 兼容,base-URL 可换成网关)与 `AiAdviceService` 配额。

---

## 7. 连接器(客户机组件,仓库根 `connector/`,Go)

- 传输 = **tmux(私有 socket) + webtty + xterm.js**。私有 socket 使托管会话对用户自己的 `tmux ls` 不可见。
- **瘦客户端原则**:`cheesed`/`cheese` 及任何客户机二进制**零业务逻辑**。driver 的发射决策、接管锁判断**上移后端**(`agent/orchestration`),客户机只留机制。这样业务更新无需重发客户端。
- **冻结协议**:带版本、只增不改。
- **已调试出的管道知识**(重建连接器时必须保留,届时连同代码写进 `docs/design/connector-protocol.md`):强制 `C.UTF-8` locale(否则多字节字形全渲染成 `_`);启动即注入 `4base64` SetEncoding 帧(否则输入乱码);resize 帧不受接管者门控地转发;前端 `open()` 前 await 字体加载 + 内嵌字体(否则空格塌缩、罕见符号缺字)。

---

## 8. 机器管理器(`compute/`)

```
compute/
  provisioning/  PVE LXC/VM 生命周期:clone 母版 -> 装 cheesed -> 接后端 -> 挂起/回收
  preview/       反向代理预览 / code-server
  quota/         算力调度 / 项目额度 / 排队 / 三级用量可见性
  pool/          资源池:自有 PVE + 机构节点
```

- **母版**:现有 119(LXC)/124(VM) 母版 root 密码不可得 → **不修改它们,自己造带 SSH key 的新母版**(LXC 经 `pct create --ssh-public-keys` 从标准 vztmpl 建后转模板;VM 经 cloud-init 镜像)。
- **IP 分配**:集群 `192.168.16.0/20`,已有机器在低位 → 新机从**高位倒序**分配。
- **AI-first 运维**:装 code-server、连接方式复杂的机器装 cheesed、修下线机器上的 cheesed —— 建模成"在临时机器上跑 Claude Code agent"的任务,而非脆弱的确定性脚本。
- `compute/` 独立于 `agent/`:机器管理器同时服务人(§7.1 工作台的预览/终端/文件面板)和 AI(agent 执行环境),不该只属于 agent 子系统。

---

## 9. 目录全图与功能映射

每项标注它服务的产品 § 与 MVP 阶段(P0–P6)。已存在的域标 [现有],新建标 [新]。

```
backend/app/
  domain/
    project/        [现有] §4  P0  聚合根:根话题/repo/AI 模式/审批策略/agent 归属/持权主体
    block/          [新]   §5  P0  万物皆块:双树(reply_to/struct_parent)+refs -- 数据地基
    thread/         [新]   §6  P1  2.0 话题/群聊:会话=git 分支,可嵌套,命名空间隔离于 topics
    document/       [新]   §7  P2  活文档:块的文档树投影,docs-in/out
    review/         [新]   §4.4 P3 验收:reviewer 分配 / accept / 主分支保护 / 采纳=merge
    milestone/      [新]   §7.2 P6 里程碑/日历:deadline 倒排、协议必做里程碑
    notification/   [现有] §8.5 P1 变更提醒 / 决策请求 / 分级(agent 用新增 type)
    space/ task/    [现有] §4  P5  机构 / 具体题目(+ 未来 task_template 协议)
    team/ user/     [现有] §4  P0  小队 / 用户(+ 未来 profile/portfolio §7.2)
    topics/ ...     [现有]        1.x 标签式 topic 及其它 legacy 域(共存)
  agent/            见 §6
  compute/          见 §8
  api/routes/       每资源一个 router(含现场/工具 RPC 端点)
  auth/             见 §4.1(agent 授权在 agent/authorization,复用此引擎)

connector/          客户机组件(Go;受冻结原则约束,见 §7)
  cheesed/          私有 tmux 托管 agent、执行注入、空闲观测、终端中继、控制通道
  cheese/           schema 驱动的工具调用 CLI(与 cheesed 无共享代码)
```

---

## 10. 一次请求怎么流动

- **人 → AI**:用户在群聊发消息 → `api/routes` → `domain` 记 block → 通知 `agent/orchestration` → 按 (agent,群) 关注策略决定唤醒 → 经 `agent/adapters` 注入 agent 会话(有机器走 `agent/connector` → cheesed → tmux)。
- **AI → 平台**:agent 调 `cheese` CLI → cheesed 本地 HTTP → 后端 `agent/connector` 工具 RPC → `agent/tools` 查表 → `agent/authorization` 按 actor 鉴权(§4.3) → 调 `domain` 业务函数 → 结果回注。
- **现场终端**:浏览器 xterm.js ↔ 后端 `agent/connector` 代理 ↔ cheesed webtty ↔ tmux。逻辑只读 = 后端仲裁(不转发非接管者输入);cheesed 全程透明可读写。

---

## 11. 存储与迁移约定(沿用现有)

- PostgreSQL + SQLAlchemy 2.0 async + Alembic。
- 每域一个包 `app/domain/<name>/{models,repositories,services}.py`(+ 可选 `types.py`);路由在 `app/api/routes/<name>.py`。
- **无共享 mixin**:每个模型自带 `id`(Sequence 主键)、`created_at/updated_at`(`timezone=True`,repo 里用 `datetime.now(UTC)` 赋值)、`deleted_at`(软删,查询恒 `.is_(None)`)。
- 新域必须在 `app/db/base.py` 加一行 import,Alembic autogenerate 才看得见。
- repo 只 `flush()` 不 `commit()`(请求级 `get_db()` 统一提交);service 抛 `app.core.errors`,不抛 `HTTPException`。

---

## 12. 红线(不可越)

1. 客户机二进制**零业务逻辑**;业务在后端。
2. 平台**不解析自然语言**,只认结构化工具调用。
3. 授权走**单一 choke point**,**复用** `permission_checker`;项目委托是**活委托**(授予者失权连带失效)。
4. actor **永不**来自 agent 提交的数据;由服务端在信任点注入。
5. **agent 硬归属于项目**(无 space/task 挂 agent 的口子)。
6. 关注只被**用户发言**触发(防 agent 互刷死循环)。
7. 块 **append-only**。
8. 连接器协议**冻结**、只增不改;不修改现有 PVE 母版。

---

## 13. MVP 阶段

- **P0**:项目聚合根扩展、block 地基、agent 一等 actor + 授权、连接器搬入 `agent/` 并挂进 `main`。
- **P1**:thread(群聊)+ 关注策略、notification 扩展、memory 接口。
- **P2**:document(活文档)。
- **P3**:review(验收=merge)。
- **P4**:compute(PVE provisioning + AI-first 运维 + preview)。
- **P5**:space/task 模板协议、roles。
- **P6**:milestone/日历。

---

## 14. 开放问题(待用户拍板)

1. **串行执行单元**:per-agent(单一心智串行,倾向)还是 per-(agent,群)可并行?影响 `orchestration` 队列粒度。
2. **agent @ agent**:是否允许 agent 显式 @ 唤醒另一个 agent?(倾向允许,作为受控协作通道。)
3. **VM 母版路径**:provisioning 先打通 LXC(API 友好),VM 的 cloud-init 母版作为第二步。
