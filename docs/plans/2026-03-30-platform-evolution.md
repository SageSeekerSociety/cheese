# 平台演进计划

> **状态：未启动**（截至 2026-06-06，Phase 1–4 均未实施，代码中无 space_membership / progress_update）

> 基于 2026-03-18 架构讨论 + 2026-03-30 eTrip 校企合作部署实践

## 背景

知是平台（Cheese）目前以**赛题（Task）**为核心：老师在空间里发布赛题 → 学生/团队双向互选 → 在平台上完成解题和结题。

现在有两个新场景提出了不同的需求：

### 场景一：eTrip 校企合作（已部署，张超老师）

企业联系人在平台上发布合作需求（用现有赛题功能），学生团队报名参与。目前的痛点：

1. **内容保护** — 企业需求属于商业信息，不能对未注册用户公开 ✅ 已解决（require_auth）
2. **非教育邮箱注册** — 企业方没有 .edu.cn 邮箱 ✅ 已解决（invite code 解锁邮箱域名限制）
3. **过程管理缺失** — 团队接题后，老师/企业方无法跟踪进展，只能等最终提交。希望有**过程化记录**和**定期汇报**
4. **企业方视角** — 企业联系人想看到"我发布的需求目前各团队进展如何"，目前没有这个 Dashboard

### 场景二：明理书院创新项目（待支持）

书院已有一批大创/创新项目团队，需要一个平台来：

1. **团队入驻** — 已有团队带着已有项目入驻空间，不是老师发题学生接题
2. **进度留痕** — 团队定期发布进度更新（做了什么、遇到什么困难、下一步计划）
3. **AI 追踪** — 用 AI 实时汇总各团队进展，生成周报给老师，检测长期无更新的团队
4. **统一管理** — 老师在一个 Dashboard 看到空间下所有团队/项目的状态

### 两个场景的共同需求

| 需求 | 赛题模式 | 入驻模式 |
|------|---------|---------|
| 团队和空间的归属关系 | 隐式（通过报名赛题产生） | 显式（团队申请入驻） |
| 过程化记录 | 需要（解题过程中的进度） | 需要（项目推进的进度） |
| AI 摘要 | 对单个赛题的分析（已有） | 对项目进度的持续追踪（缺失） |
| 管理员总览 | 各赛题的参与/提交统计（已有） | 各项目的进展/健康度总览（缺失） |

**核心问题**：怎样在现有系统上**自然地**统一承载这两种模式，而不是每来一个需求就开发一套独立功能。

---

## 现状分析

### 已有的关键能力

| 模块 | 状态 | 关键发现 |
|------|------|----------|
| **Project** | ✅ 模型+服务已存在 | `project` 表有 `team_id`, `leader_id`, `parent_id`(层级), `external_task_id`(关联赛题), `github_repo`；`project_membership` 支持 MEMBER/ADMIN/OWNER |
| **Discussion** | ✅ 完整可用 | 多态设计 `model_type` ∈ {PROJECT, TEAM, TASK, KNOWLEDGE, QUESTION, ANSWER}，已支持 @mention、reactions、嵌套回复 |
| **Knowledge** | ✅ 完整可用 | 绑定 team，支持 labels、upvotes、全文搜索，可从 Discussion 转化（`source_type=FROM_DISCUSSION`） |
| **AI** | ✅ 基础可用 | AIConversation 有 `module_type`（可扩展）+ `context_id`；TaskAIAdvice 生成赛题分析；配额系统完整 |
| **Notification** | ✅ 完整可用 | 聚合通知、@mention、团队事件；支持 in-app + Redis 邮件队列 |
| **Space ↔ Team** | ⚠️ 无直接关系 | 仅通过 Task 间接关联，Space 没有成员概念 |

### 核心差距（需要新建的）

1. **Space 没有成员/入驻机制** — 无法表达"团队属于某空间"
2. **没有 Progress Stream** — 无结构化进度记录，Discussion 是自由形式的
3. **Space 没有治理模式配置** — 无法区分"发布式"和"入驻式"
4. **AI 没有进度追踪能力** — 只有一次性赛题分析，无持续摘要

---

## 实施阶段

### Phase 1: Space 成员体系（基础设施）

**目标**：让 Space 能感知"谁在里面"，为入驻和进度流打基础。

#### 1.1 新建 `space_membership` 表

```python
# app/domain/space/models.py 新增

class SpaceMembershipType(str, Enum):
    TEAM = "TEAM"
    USER = "USER"

class SpaceMembershipJoinType(str, Enum):
    AUTO = "AUTO"          # 通过赛题匹配自动关联
    APPLIED = "APPLIED"    # 主动申请入驻
    INVITED = "INVITED"    # 管理员邀请

class SpaceMembershipRole(str, Enum):
    PARTICIPANT = "PARTICIPANT"  # 赛题参与者（自动）
    RESIDENT = "RESIDENT"        # 入驻团队/成员

class SpaceMembership(Base):
    __tablename__ = "space_membership"
    id          # BigInteger, PK
    space_id    # BigInteger, FK → space
    member_type # String — TEAM or USER
    member_id   # BigInteger — team.id or user.id
    join_type   # String — AUTO / APPLIED / INVITED
    role        # String — PARTICIPANT / RESIDENT
    status      # String — ACTIVE / PENDING / REMOVED
    created_at, updated_at, deleted_at
```

**Migration**: `add_space_membership_table.py`

#### 1.2 SpaceMembershipService

```
文件: app/domain/space/membership_service.py

方法:
- apply_to_join(space_id, member_type, member_id) → SpaceMembership  # 主动申请
- approve_membership(membership_id, actor_user_id)                    # 管理员审核
- reject_membership(membership_id, actor_user_id)
- auto_join(space_id, member_type, member_id)                        # 赛题匹配后自动调用
- remove_membership(membership_id, actor_user_id)
- list_members(space_id, member_type?, role?, status?)
- is_member(space_id, member_type, member_id) → bool
```

#### 1.3 自动关联：赛题参与 → Space 成员

在 `TaskMembershipService.create_membership()` 成功后，调用：
```python
await space_membership_service.auto_join(
    space_id=task.space_id,
    member_type="TEAM" if membership.is_team else "USER",
    member_id=membership.member_id,
)
```

#### 1.4 API 端点

```
GET    /spaces/{spaceId}/members          # 列出成员（管理员可见全部，普通用户只看 ACTIVE）
POST   /spaces/{spaceId}/members/apply    # 申请入驻
PATCH  /spaces/{spaceId}/members/{id}     # 审核/移除
```

#### 1.5 前端变更

- Space 详情页增加「成员/入驻团队」Tab
- 团队视角增加「申请入驻」按钮
- 管理员视角增加「入驻审核」列表

**估算改动量**：~400 行后端 + 1 个 migration + 前端 2-3 个组件

---

### Phase 2: Progress Stream（进度流）

**目标**：让团队能在 Project 下发布结构化的进度更新，形成时间线。

#### 2.1 新建 `progress_update` 和 `milestone` 表

```python
# app/domain/progress/models.py

class Milestone(Base):
    __tablename__ = "milestone"
    id           # BigInteger, PK
    project_id   # BigInteger, FK → project
    name         # String(255)
    description  # Text
    due_date     # DateTime (nullable)
    status       # String — PENDING / IN_PROGRESS / COMPLETED / OVERDUE
    completed_at # DateTime (nullable)
    created_at, updated_at, deleted_at

class ProgressUpdate(Base):
    __tablename__ = "progress_update"
    id            # BigInteger, PK
    project_id    # BigInteger, FK → project
    author_id     # Integer, FK → user
    milestone_id  # BigInteger, FK → milestone (nullable)
    title         # String(255)
    content       # JSONB — 复用 Discussion 的 Tiptap 格式
    update_type   # String — PROGRESS / BLOCKER / ACHIEVEMENT / NOTE
    attachments   # JSONB — [{attachmentId, name}]
    created_at, updated_at, deleted_at
```

**设计决策**：
- `ProgressUpdate` 不复用 `Discussion`，因为语义不同：Discussion 是**讨论**，ProgressUpdate 是**记录**
- 但 ProgressUpdate 下可以挂 Discussion（`model_type=PROGRESS_UPDATE`），支持对进度的评论
- 内容格式复用 Tiptap JSON，前端编辑器可复用

#### 2.2 ProgressService

```
文件: app/domain/progress/services.py

方法:
- create_milestone(project_id, name, description, due_date, actor_user_id)
- update_milestone(milestone_id, ..., actor_user_id)
- complete_milestone(milestone_id, actor_user_id)
- list_milestones(project_id, status?)

- create_update(project_id, title, content, update_type, milestone_id?, attachments?, actor_user_id)
- list_updates(project_id, milestone_id?, update_type?, limit, offset)  # 时间线
- get_update(update_id)
- delete_update(update_id, actor_user_id)
```

#### 2.3 API 端点

```
# Milestone
POST   /projects/{projectId}/milestones
GET    /projects/{projectId}/milestones
PATCH  /projects/{projectId}/milestones/{milestoneId}

# Progress Updates
POST   /projects/{projectId}/updates
GET    /projects/{projectId}/updates          # 时间线，支持按 milestone/type 过滤
GET    /projects/{projectId}/updates/{updateId}
DELETE /projects/{projectId}/updates/{updateId}
```

#### 2.4 Notification 集成

- 进度更新发布时 → 通知空间管理员（老师）
- 里程碑逾期时 → 通知项目成员 + 空间管理员

新增 NotificationType：`PROGRESS_UPDATE`, `MILESTONE_OVERDUE`

#### 2.5 Discussion 扩展

在 `DiscussableModelType` 新增 `PROGRESS_UPDATE`，使进度更新可被评论。

#### 2.6 前端变更

- Project 详情页增加「进度」Tab：时间线视图 + 里程碑甘特图
- 进度发布表单（Tiptap 编辑器 + 附件 + 关联里程碑）
- 管理员 Dashboard 侧边栏：空间下所有项目的最新进度摘要

**估算改动量**：~600 行后端 + 2 个 migration + 前端 3-4 个组件

---

### Phase 3: Space 治理模式

**目标**：让不同空间可以配置不同运作方式。

#### 3.1 Space 表新增配置字段

```python
# Migration: alter space table
op.add_column("space", sa.Column("governance_mode", sa.String(50),
    server_default="TASK_DRIVEN", nullable=False))
op.add_column("space", sa.Column("allow_team_apply", sa.Boolean(),
    server_default=sa.text("false"), nullable=False))
op.add_column("space", sa.Column("allow_self_create_project", sa.Boolean(),
    server_default=sa.text("false"), nullable=False))
op.add_column("space", sa.Column("require_membership_approval", sa.Boolean(),
    server_default=sa.text("true"), nullable=False))
```

**治理模式**：
| 模式 | governance_mode | 场景 |
|------|----------------|------|
| 赛题驱动 | `TASK_DRIVEN` | 信息学院科研早培（现有模式） |
| 入驻驱动 | `RESIDENT_DRIVEN` | 明理书院创新项目 |
| 混合 | `HYBRID` | 两者兼有 |

#### 3.2 前端变更

- Space 编辑页增加「运作模式」配置区
- 入驻驱动模式下：首页展示入驻团队列表 + 项目进度，弱化赛题入口
- 赛题驱动模式下：保持现有 UI 不变

#### 3.3 入驻式项目创建流程

```
团队申请入驻 Space（Phase 1）
  → 管理员审核通过
  → 团队在 Space 下创建 Project（self_create_project=true）
  → Project 自动关联 Space（通过 SpaceMembership）
  → 团队发布进度（Phase 2）
```

需要扩展 Project 模型，增加 `space_id` 字段：
```python
# Migration: add space_id to project
op.add_column("project", sa.Column("space_id", sa.BigInteger(), nullable=True))
```

这样 Project 就可以既通过 `external_task_id` 关联赛题（发布式），又通过 `space_id` 直接关联空间（入驻式）。

**估算改动量**：~200 行后端 + 1 个 migration + 前端配置组件

---

### Phase 4: AI 进度追踪

**目标**：AI 定期摘要项目进度，生成管理员报告。

#### 4.1 新建 `project_ai_summary` 表

```python
# app/domain/progress/models.py 新增

class ProjectAISummary(Base):
    __tablename__ = "project_ai_summary"
    id             # BigInteger, PK
    project_id     # BigInteger, FK → project
    summary_type   # String — WEEKLY / ON_DEMAND / MILESTONE_REPORT
    content        # JSONB — {overallStatus, keyProgress, blockers, nextSteps, healthScore}
    period_start   # DateTime
    period_end     # DateTime
    updates_count  # Integer — 本期内的进度更新数量
    raw_response   # Text — 原始 LLM 输出
    created_at
```

#### 4.2 ProjectAISummaryService

```
方法:
- generate_summary(project_id, summary_type, period_start?, period_end?)
  → 收集 period 内的 ProgressUpdate + Discussion
  → 调用 LLM 生成结构化摘要
  → 存储到 project_ai_summary

- generate_space_report(space_id)
  → 聚合空间下所有项目的最新摘要
  → 生成面向管理员的总览报告

- check_anomalies(space_id)
  → 检测：长时间无更新、里程碑逾期、进度停滞
  → 触发 DEADLINE_REMIND 通知
```

#### 4.3 定时任务

复用现有的后台任务机制（`@app.on_event("startup")` 或 APScheduler）：
- 每周一 9:00 → 为所有活跃项目生成周报
- 每天 10:00 → 检测异常（无更新 > 7天、里程碑逾期）

#### 4.4 API 端点

```
GET    /projects/{projectId}/ai-summaries              # 项目摘要历史
POST   /projects/{projectId}/ai-summaries/generate     # 手动触发
GET    /spaces/{spaceId}/ai-report                     # 空间级管理员报告
```

#### 4.5 前端变更

- Project 详情页增加「AI 摘要」Tab
- 管理员 Dashboard 增加「AI 周报」视图：每个项目一张卡片，显示健康度、关键进展、风险点
- 异常检测 → 红色徽标提醒

**估算改动量**：~500 行后端 + 1 个 migration + 前端 2-3 个组件

---

## 依赖关系与推荐顺序

```
Phase 1 (Space 成员)
    │
    ├──→ Phase 2 (Progress Stream)    ← 需要 Space 成员体系判断"谁能发进度"
    │        │
    │        └──→ Phase 4 (AI 追踪)   ← 需要 Progress Stream 作为数据源
    │
    └──→ Phase 3 (治理模式)           ← 需要 Space 成员体系支撑入驻流程
```

**建议**：Phase 1 → Phase 2 → Phase 3 → Phase 4

Phase 1 和 Phase 3 可以并行开发（Phase 3 的 DB migration 不依赖 Phase 1 的表），但 Phase 3 的入驻流程运行时依赖 Phase 1 的 SpaceMembership。

---

## 不改动的部分

- **Task 不改名为 Project** — 实际上 Task 和 Project 已经是两个独立实体，且 `project.external_task_id` 已建立关联。不需要强行合并。
- **Discussion 系统** — 直接复用，只需在 `DiscussableModelType` 加 `PROGRESS_UPDATE`
- **Knowledge 系统** — 直接复用，进度中的沉淀知识可转为 Knowledge
- **现有赛题流程** — 完全不动，Phase 1 的 auto_join 是增量行为
- **Team 系统** — 不改动，入驻的主体就是现有的 Team

---

## 数据模型变更汇总

| Phase | 新表 | 改表 | 新 Enum |
|-------|------|------|---------|
| 1 | `space_membership` | - | SpaceMembershipType, JoinType, Role |
| 2 | `milestone`, `progress_update` | - | ProgressUpdateType; DiscussableModelType += PROGRESS_UPDATE; NotificationType += 2 |
| 3 | - | `space` +3 列, `project` +1 列 | GovernanceMode |
| 4 | `project_ai_summary` | - | SummaryType |

**总计**：4 个新表 + 2 个表加列 + 4 个 migration
