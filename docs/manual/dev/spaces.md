---
title: 空间与课程
kind: 参考
summary: 题目版就是一门课、成员与审批、教学单元、小测、课程模块开关和管理员看板。
covers:
  - backend/app/domain/space/
  - backend/app/domain/teaching/
  - backend/app/api/routes/spaces.py
---

# 空间与课程 {#spaces}

一块题目版就是一门课：成员、审批、按周排的教学单元、作业、小测、组队，以及老师在后台看见的那些数。

> 讲：这些行各是什么、谁看得见、发布这个动作改变了什么、哪些东西这一层不做。不讲：题目与提交本身（见[任务与工作目录](/dev/tasks)），机构协议与壳（见[准入与供给](/dev/admission)），项目那边的人与团队（见[团队、项目与成员](/dev/teams)）。

## 一块题目版就是一门课 {#space}

`domain/space/models.py` 的第一张表 `Space` 是这里的中心，而它今天的产品定义是**一门课（一次活动）**，不是「机构」。这一点值得单独写一段，因为它和仓库里另一份文档对不上：

`SpaceCategory` 的 docstring 明写着：``docs/spec.md`` 仍带着旧读法（Space = 机构，SpaceCategory = 一门课），那一读法已被「课程模板」这次重新定义退休——**两级各自上移了一格**，代码和注释正在统一到新读法上。所以读这一页时按代码来：`Space` = 题目版 = 一门课；`SpaceCategory` = 这门课里的一格（作业 / 实验 / 小测），每道题目（`Task`）挂在且只挂在一个分组下，新板子自动拿到的那一行「General」是这门课的第一格，不是一门课。

`Space` 上几个字段各有分工：

| 字段 | 说的是什么 |
|---|---|
| `review_status` / `review_reason` / `reviewed_by` / `reviewed_at` | 审批（见下） |
| `enable_rank` | 这门课开不开排名 |
| `visible_task_limit` | 对成员露出前几道题（`None` = 不限） |
| `course_modules` | 这门课开着哪几个模块（见下） |
| `announcements` / `task_templates` | 声明式数据，各自一组 |

**机构协议（#370）落在 `SpaceCategory` 上**，不在 `Space` 上：`resource_pack` / `conditions` / `default_role` 和壳的 `shell` 在分组上声明一次，下面每道题目继承，题目可以整键覆盖（`Task.protocol_override`）。理由是「一门课为『作业』整体说一次条件，不是每道题说一次」。它们只能通过 `app.domain.task.protocol.resolve`（壳走 `app.domain.shell.service`）读，**永远不要直接读列**。

## 谁能看见，谁能管理 {#membership}

「谁能看见这块题目版」这个问题，答案**只有成员资格一个来源，没有可见性档位**（模型注释里写得很直白：there is no visibility tier）。两件事要分开：

- `SpaceMember` 一行是「这块板子归我看」。成员资格本身就是可见性，所以移除一个成员**什么都不带走**——不带走他的题目、提交和项目。
- `SpaceAdminRelation` 一行（`OWNER = 0` / `ADMIN = 1`）是「我可以管理它」。创建者和管理员不写 `SpaceMember` 行也照样看得见。

`SpaceMember` 上有一条必须留意的索引：`uq_space_member_active` 是 `(space_id, user_id)` 在 `deleted_at IS NULL` 上的部分唯一索引。没有它，两个请求同时读到「还不是成员」就都会写，之后每个 `get_member` 都抛 `MultipleResultsFound`——一次文档里写着「重复点等于没点」的操作变成一个 500。部分索引的另一个用处：软删掉的行是「移除」的记录，**重新加入是复活它而不是加第二行**，那些行在索引之外，所以移除不会挡住随后的加入。另有一条非部分索引 `ix_space_member_space_user` 服务「连删除行一起按对读」的那个读法（那正是它存在的理由）。

`SpaceInviteCode` 是入板用的码：一块板子建出来时**就带着一个**，因为一块谁都进不去的板子没什么用。它和平台注册用的 `invite_code` **故意不是同一张表**——形状像，别的一点关系没有。码有 `max_uses` / `use_count` / `expires_at` / `created_by` / `note`（「十月这批同学」这种，制造者自己的话），吊销是软删，所以码指过的那一行之后仍然读得出来。`SpaceMember.invite_code_id` 记这个人是从哪个码进来的，值为 `NULL` 时**有两种情况而这一列分不出**：这列存在之前写下的行（迁移故意不回填——给老成员编一个码就是发明历史），以及所有者直接加的人（真相是「没有码」）。两者都读作「未知」，而「未知」和「没有码」不是同一句话。

## 审批 {#review}

`Space.review_status` 默认 `APPROVED`（为了既有数据），而**公开的创建路由把新建的置为 `PENDING`**（`api/routes/spaces.py`）。所以「新建空间默认就是课程、要等平台审核」这句用户文档说的是对的：默认值服务历史数据，创建那条路写死未审批。`SpaceReviewService`（`review_service.py`）管状态迁移，裁定人与时间写回 `Space.reviewed_by` / `reviewed_at`；`review_reason` 是驳回时给人看的那句话。

## 教学单元与作业 {#units}

课程的时间线是 `TeachingUnit`（`domain/teaching/models.py`）：`week`（两个单元可以同一周，一次讲课一次实验）、`title` / `summary`、`knowledge_point_ids`（指向知识库的行，不拷正文）、`material_ids`（这周发的课件）、`assignment_task_id`、`due_at`。

三条设计上的取舍写在模型说明里：

- **「发布了才有」是这模型存在的理由**。`published_at` 为 `NULL` 的单元对成员不存在：不进列表、不进详情、也不该进 agent 的上下文。这样「随课程推进才能得到更多知识」是默认行为,而不用管理员每周记得去改一份配置；想提前放出来就发布它。
- **作业不新造提交体系**。`assignment_task_id` 指向这块板子里的一道真 `Task`，提交、截止、重交、评审、打分全部沿用现成那一套。
- **知识点是 id 不是副本**。正文住在知识库里，也住在那里被编辑。

## 小测 {#quiz}

`quiz_models.py` 里四张表：`Quiz`（一次小测，**一个单元一次**）、`QuizQuestion`、`QuizAttempt`（一次作答）、`QuizAnswer`。

题型常量与那条界只在**一处**定义：`OBJECTIVE_KINDS` = 单选 / 多选 / 判断 / 填空，`is_objective(kind)` 是唯一判据（服务与路由不许各抄一遍）。

判分因此分成两半，界就在题型上：客观题**交卷那一刻在服务端一次判完**，成员立刻看到分；简答判不了，它落到管理员的复核队列里——`awarded_points` 为 `NULL` 就是「等着人判」，管理员判完这次作答才算判完（`QuizAttempt.graded_at` 从 `NULL` 变成时间）。

两条硬规则：

- **正确答案从不发给成员**。作答在截止前可以重交（改一改再交是正常的），只要能拿到答案键，重交就变成抄答案。所以成员的载荷里只有「你这题得了多少分」，没有「正确选项是哪个」。
- **小测的可见性来自单元的 `published_at`，不是它自己某一列**。小测挂在 `unit_id` 上，单元发布了它才存在。这是**复用那条查询的语义**，不是另立一份规则。

## 课程模块开关 {#modules}

`space/course_modules.py` 管 `Space.course_modules` 那一列：八个模块 `units` / `assignments` / `quiz` / `team` / `stuck` / `materials` / `progress` / `pool`。

它是**声明式数据，不是代码分支**：开关只决定界面露出哪几格（`frontend/src/lib/courseNav.ts` 与课程首页），**不决定能力**——关掉一个模块是把它从界面上收起来，地址仍然打得开、接口照样答话。所以那里没有「模块可不可用」，读侧永远只把它当过滤条件用，不写成 `if module_enabled:` 撒在各处。

**缺省是开着**：`{}` 与 `{"quiz": true}` 等价，存的只是偏离缺省的那些格子，所以以后加一个新模块时既有的课自动拿到它、不需要回填。`normalize` 只留认识的键、强制成 bool，而且**故意宽松**——它也在读路径上跑，抛异常会让一格里一个坏字节打挂每一个渲染它的页面；写路径才是有人在看着、可以被告知的那一侧（见 `PatchSpaceRequest`）。`is_on` 对不认识的键抛 `ValueError`，这一条不宽松。

## 管理员看板 {#analytics}

`space/` 下另外几个服务是老师与管理员那一侧：`analytics_service` / `analytics_view_service`（这个 1394 行，是最大的一块）、`learning_service`（学习维度：成员怎么与 AI 协作、卡在哪，issue #945）、`member_participating_service` / `member_publishing_service`（报名、发布）、`course_roster_service`、`rank_service`、`tags_service`。

`learning_service` 的模块说明值得整段读，因为它把**没有的东西**也写清楚了：`review_flag` 全仓没有这一列也没有这张表，所以 `reviewFlag` 那个队列只能报 `available: false`、把缺什么写进返回里，不拿别的信号冒充它；「再给一点提示」的点击同样没有落库；知识点今天也不存在，能用的是**课程分类**（`space_categories`，课程设计者自己划的格子——够用，但它不是知识点）。

还有一条边界：**「能打开课程页」不等于「能读这门课下每个成员项目的对话」**。每一个项目都得先过 `app.auth.project_access.may_read_project`，而「出题者能读自己那门课的产出」正是那份判据里已有的第四种主张。列表处用布尔版（一个列表不能为第 40 个项目抛 403 就整体失败），取原文处用抛错版。私聊（`Topic.is_private`）不进这一格：那是成员自己的对话，和导师对话视图无关。

## 边界与坑 {#traps}

- **`docs/spec.md` 的空间/分类两级定义是旧的**。代码这边 `Space` = 一门课、`SpaceCategory` = 课里的一个分组；协议与壳模块里还写着「项目集」的地方，指的都是 `SpaceCategory`。
- **`SpaceMember` 的 `NULL` invite_code 不等于「没有码」**，下游不要替它下结论。
- **`course_modules` 缺键是开，不是关**。用 `is_on` 读，别自己 `.get(key, False)`。
- **协议字段不许直接读列**。`resource_pack` / `conditions` / `default_role` 要经过 `task.protocol.resolve` 才拿到题目继承与整键覆盖后的结果。
- **`visible_task_limit` 不是权限**。它是对成员露出前几道的截断，admin 与创建者不受它影响；真正的授权在成员资格与 `SpaceAdminRelation` 上。
- **单元的 `published_at` 是唯一的发布开关**。别在小测或作业上再加一个——两个开关就会有两次不同步，而其中一次没人发现。
