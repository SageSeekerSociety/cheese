---
title: 空间与题目
kind: 参考
summary: 题目版、成员与审批、机构协议落在哪一层，以及管理员看板。
covers:
  - backend/app/domain/space/
  - backend/app/api/routes/spaces.py
  - backend/app/api/routes/space_announcements.py
---

# 空间与题目 {#spaces}

一块题目版装着一批题目：成员、审批、题目分组，以及老师在后台看见的那些数。

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
| `task_templates` | 声明式数据，一组发题模板 |

**机构协议（#370）落在 `SpaceCategory` 上**，不在 `Space` 上：`resource_pack` / `conditions` / `default_role` 和壳的 `shell` 在分组上声明一次，下面每道题目继承，题目可以整键覆盖（`Task.protocol_override`）。理由是「一门课为『作业』整体说一次条件，不是每道题说一次」。它们只能通过 `app.domain.task.protocol.resolve`（壳走 `app.domain.shell.service`）读，**永远不要直接读列**。

## 谁能看见，谁能管理 {#membership}

「谁能看见这块题目版」这个问题，答案**只有成员资格一个来源，没有可见性档位**（模型注释里写得很直白：there is no visibility tier）。两件事要分开：

- `SpaceMember` 一行是「这块板子归我看」。成员资格本身就是可见性，所以移除一个成员**什么都不带走**——不带走他的题目、提交和项目。
- `SpaceAdminRelation` 一行（`OWNER = 0` / `ADMIN = 1`）是「我可以管理它」。创建者和管理员不写 `SpaceMember` 行也照样看得见。

`SpaceMember` 上有一条必须留意的索引：`uq_space_member_active` 是 `(space_id, user_id)` 在 `deleted_at IS NULL` 上的部分唯一索引。没有它，两个请求同时读到「还不是成员」就都会写，之后每个 `get_member` 都抛 `MultipleResultsFound`——一次文档里写着「重复点等于没点」的操作变成一个 500。部分索引的另一个用处：软删掉的行是「移除」的记录，**重新加入是复活它而不是加第二行**，那些行在索引之外，所以移除不会挡住随后的加入。另有一条非部分索引 `ix_space_member_space_user` 服务「连删除行一起按对读」的那个读法（那正是它存在的理由）。

`SpaceInviteCode` 是入板用的码：一块板子建出来时**就带着一个**，因为一块谁都进不去的板子没什么用。它和平台注册用的 `invite_code` **故意不是同一张表**——形状像，别的一点关系没有。码有 `max_uses` / `use_count` / `expires_at` / `created_by` / `note`（「十月这批同学」这种，制造者自己的话），吊销是软删，所以码指过的那一行之后仍然读得出来。`SpaceMember.invite_code_id` 记这个人是从哪个码进来的，值为 `NULL` 时**有两种情况而这一列分不出**：这列存在之前写下的行（迁移故意不回填——给老成员编一个码就是发明历史），以及所有者直接加的人（真相是「没有码」）。两者都读作「未知」，而「未知」和「没有码」不是同一句话。

## 公告 {#announcements}

公告一条一行，存在 `SpaceAnnouncement`（`space_announcement` 表）：作者、标题、富文本正文、`pinned`、可空的 `expires_at`。路由在 `api/routes/space_announcements.py`，逻辑在 `domain/space/announcement_service.py`。

- **读是成员，写是管理员。** 列表和空间本身同一道门（`_ensure_space_visible`），发布、修改、删除走 `is_space_admin`。每条有自己的地址，改一条只写那一条。
- **到没到期由服务端答。** 列表回 `current`（置顶在前，再按新到旧）和 `expired` 两组；题目列表顶上那一栏只读 `current` 里置顶的。`updated_at` 只在标题、正文、到期日变化时挪动，置顶不算「已编辑」。
- **发布时通知一次。** 收件人是发布那一刻空间里除作者以外的每个人（成员加管理员），经投递账本发出，类型 `SPACE_ANNOUNCEMENT`，只进站内（见[通知与待办](/dev/notifications#ledger)）。修改不再通知，只把已发出的标题和摘要改成新的（`ledger.amend`）；删除连同它发出的通知一起撤回（`ledger.retract`）。

## 审批 {#review}

`Space.review_status` 默认 `APPROVED`（为了既有数据），而**公开的创建路由把新建的置为 `PENDING`**（`api/routes/spaces.py`）。所以新建的空间要等平台审核：默认值服务历史数据，创建那条路写死未审批。`SpaceReviewService`（`review_service.py`）管状态迁移，裁定人与时间写回 `Space.reviewed_by` / `reviewed_at`；`review_reason` 是驳回时给人看的那句话。

## 管理员看板 {#analytics}

`space/` 下另外几个服务是老师与管理员那一侧：`analytics_service` / `analytics_view_service`（这个 1394 行，是最大的一块）、`learning_service`（学习维度：成员怎么与 AI 协作、卡在哪，issue #945）、`member_participating_service` / `member_publishing_service`（报名、发布）、`rank_service`、`tags_service`。

`learning_service` 的模块说明值得整段读，因为它把**没有的东西**也写清楚了：`review_flag` 全仓没有这一列也没有这张表，所以 `reviewFlag` 那个队列只能报 `available: false`、把缺什么写进返回里，不拿别的信号冒充它；「再给一点提示」的点击同样没有落库；知识点今天也不存在，能用的是**课程分类**（`space_categories`，课程设计者自己划的格子——够用，但它不是知识点）。

还有一条边界：**「能打开课程页」不等于「能读这门课下每个成员项目的对话」**。每一个项目都得先过 `app.auth.project_access.may_read_project`，而「出题者能读自己那门课的产出」正是那份判据里已有的第四种主张。列表处用布尔版（一个列表不能为第 40 个项目抛 403 就整体失败），取原文处用抛错版。私聊（`Topic.is_private`）不进这一格：那是成员自己的对话，和导师对话视图无关。

## 边界与坑 {#traps}

- **`docs/spec.md` 的空间/分类两级定义是旧的**。代码这边 `Space` = 一门课、`SpaceCategory` = 课里的一个分组；协议与壳模块里还写着「项目集」的地方，指的都是 `SpaceCategory`。
- **`SpaceMember` 的 `NULL` invite_code 不等于「没有码」**，下游不要替它下结论。
- **协议字段不许直接读列**。`resource_pack` / `conditions` / `default_role` 要经过 `task.protocol.resolve` 才拿到题目继承与整键覆盖后的结果。
- **`visible_task_limit` 不是权限**。它是对成员露出前几道的截断，admin 与创建者不受它影响；真正的授权在成员资格与 `SpaceAdminRelation` 上。
