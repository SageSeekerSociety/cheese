---
title: 团队、项目与成员
kind: 参考
summary: 团队与个人团队、入团申请与邀请、项目的归属，以及「这个项目里有谁」那唯一一个答案。
covers:
  - backend/app/domain/team/
  - backend/app/domain/project/
  - backend/app/domain/membership/
  - backend/app/api/routes/members.py
---

# 团队、项目与成员 {#teams}

团队、个人团队、入团申请与邀请、项目挂在谁名下，以及「这个项目里有谁」——这个问题全平台只有一个答案。

> 讲：团队和项目怎么建、人怎么进来、名册由哪些来源合成。不讲：谁能看到什么、席位与授权怎么判定（见[席位与权限判定](/dev/seats)），房间里的成员和 @（见[一条消息怎么变成芝士的一轮](/dev/turn)），团队成员在外面还有哪些入口（见[集成](/dev/integrations)）。

## 团队与个人团队 {#team-model}

`domain/team/models.py` 里只有一张 `Team`，两种用法：

- **共享团队**：有 `handle`，在 URL 和 @ 里用它。handle 与用户名**同一个字母表和同一个命名空间**，所以一个 handle 只指一个人或一个团队，不会两个都是（`uq_team_handle_lower` 唯一索引；`ck_team_handle_iff_shared` 保证共享团队才有 handle）。
- **个人团队**：`personal_owner_user_id` 非空，一个用户一个（`uq_team_personal_owner`，带 `deleted_at IS NULL` 的部分索引；`create_personal_team` 的 `ON CONFLICT` 用的是同一个谓词，Postgres 才对得上）。它**不存 handle**（名字由所有者用户名给出），是单人的真团队——个人项目属于它，它也能像任何团队一样持有算力和设备，所以「为自己注册设备」就是「注册给个人团队」。**除了所有者谁也进不来**：`TeamRepository.add_member` 是所有入团路径（直接加、接受邀请、批准申请、按链接加入）最后落行的那一处，个人团队的规矩就守在那里（`refuse_anyone_but_the_owner`），发邀请时也提前拒掉，免得一条永远接受不了的邀请躺在对方的待定里。和别人一起做个人项目，走项目的外部成员；要长期一起用，另建共享团队。

界面上它不是团队：侧栏把它单独放在「团队」小标题之上，以所有者的昵称和头像出现。团队接口读它时 `name`、`avatarId` 给的就是所有者的昵称和头像（`_team_to_api_model`），库里存的名字不占重名检查（`exists_by_name` 排除个人团队）。团队题不能用它领：候选团队里没有它，`POST /tasks/{id}/participations/team` 拿它来领直接拒。

两个开关各自管一件事：`visibility`（`public` / `stealth`）决定**不进团队的人找不找得到它**——public 能搜到、能按 id 打开；stealth 两样都不行，只能通过 `join_token`（`/team-invites/<token>`，一直有效直到队长或管理员重置）进来。`join_approval` 决定**进来的时候等不等**，和项目上那个开关是同一个语义，默认开。

角色是 `TeamMemberRole` 的三个整数：`OWNER = 0` / `ADMIN = 1` / `MEMBER = 2`。

## 申请与邀请是同一条流水线 {#applications}

`TeamMembershipApplication` 一张表装两件事，靠 `type` 分开：`REQUEST`（用户申请）和 `INVITATION`（团队邀请）。状态集合也共用：`PENDING` / `APPROVED` / `REJECTED` / `ACCEPTED` / `DECLINED` / `CANCELED`——前三个是申请那条路的结局，后三个是邀请那条路的。共用一张表的好处是「待定的东西」只有一个读点；`initiator_id` 与 `user_id` 是两列，因为两个方向里谁是发起人不一样。

`TeamMembershipService.join` 的行为由 `join_approval` 一个开关决定：关着就直接落 `TeamUserRelation` 一行、返回 `member`；开着就写一条 `PENDING` 的 `REQUEST`，并给全部队长和管理员发一条 `TEAM_JOIN_REQUEST` 通知（带 requester / team / application 三个 id）。**问两次、或者已经在里面了都不是错误**，答案是他现在站在哪（`join_status` 返回 `member` / `pending` / `none`）。

接受邀请时角色从申请行的 `role` 字符串映射回整数（`OWNER` / `ADMIN` / `MEMBER`，认不出按 `MEMBER`），所以邀请人可以在发出时就定角色，用户文档里那张「队长/管理员/普通成员能做什么」的表说的就是这个。四种结局各发一条通知，而收件人不一样：申请和邀请是 `handed_to`（队长与管理员），接受与拒绝是 `outcome_for`（发起人）——同一次状态变化里「谁要动」和「谁想知道」是两个人。

团队锁定（`check_team_locking_status`）挡在**改成员资格**那几条路上：团队还在某个 `LOCK_ON_APPROVAL` 且未结束的赛题里获批，成员就加不进来也退不出去。这是从旧系统搬过来的规则，读的是 `TaskMembership`。

## 项目挂在团队下 {#projects}

`Project.team_id` 是外键、`ondelete="RESTRICT"`：**每个项目都属于一个团队**，而一个还有项目的团队删不掉。建项目时（`ProjectService.create`）没给 `team_id` 就用所有者的个人团队（`_resolve_personal_team_id`）；两者都没有就拒——「项目没有地方可以属于」不是一个可接受的状态。

建项目不是只写一行：同一个事务里造出它的**根话题**（频道「综合」，`TopicKind.root`）并回填 `root_topic_id`（`projects` 与 `topics` 互相外键，所以这一个用 `use_alter` 事后 `ALTER` 加），芝士和它在「综合」里的席位也在同一个事务里出生。`intent` 是人填表时说的「想做什么」，原样存下，非空就写进项目总览（`projects.overview_document_id`，`ProjectService.seed_overview`，署名 `system`）。从赛题建的项目，项目总览里写的是「赛题要求」。

## 名册：一个答案 {#roster}

「这个项目里有谁」以前有两个答案——`ProjectMember`（只有人）和房间的 `topic_memberships`（人加席位），而**每条读名册的路走的都是前者**，于是 agent 在项目里列不出另一个 agent。现在只有一个读法：`membership/roster.py` 的 `roster()`。

它合成两个来源，不是两份名册：

- **人**这一半来自 `ProjectRepository.people`，三个来处由 `source` 说是哪一个：`owner`（`projects.owner_handle`，从不写成员行）、`team`（所属团队的成员，**读的时候实时算**，所以退出团队就离开了它的项目）、`external`（`project_members` 一行：接受了邀请的外部成员）。
- **队友**这一半来自这个项目的 agent 实例。已经有授权行的队友**合成一行**而不是两行——授权行给角色，实例给名字和启用与否（座位账号的昵称是建号时写死的常量，拿它当名字会让每个队友都叫「芝士」）。

三处容易写错的地方，代码里都写明了：

- **`agent` 这一列由 binding 答**（`IdentityService.agents_among`），不是「这一行是不是本项目的实例」，也不是「handle 长得像不像」。有授权行而实例建在别处的队友照样是 agent，房间名册对同一个 handle 答的也是 agent——同一个事实两份声明，差别只在某一个读者身上显出来（以前显在 agent 身上，人看不见）。
- **`ProjectMember` 是「不是通过团队来的那个人」的授权行**，团队成员从不写行。所以「在小队里」不等于「在这个项目里」这件事需要另一张表：`ProjectMemberExclusion`（`(project_id, user_handle)`）。「退出项目」按下去写的是这一行，小队里那一下一个字不动，而名册、`may_read_project`、`list_visible_to` 都认它。键带 `project_id` 而不是只有 handle：同一个人退出 A 项目，在 B 项目照常。所有者不在其内——他退不掉，而且一个退出过项目的人后来接手了它，照样是所有者。
- **`project_default` 只有实例答得出**。名册上「第一个带 AI 标的」不是这个答案：那是建得最早的那一位，而停用默认队友时默认会改判给另一位，于是两者必然不同——界面照前者写名字，答话的是后者。`project_default` 读的就是 `project.default_agent_instance_id`。

`roster_rows()` 是**同一次读**的另一种形状（`list[dict]`），给 @ 解析、提示词渲染、通知寻址这些还按 `m["handle"]` 传名册的路——它们是渲染这张表，不是再问一次「项目里有谁」。

## 邀请进项目 {#project-invitations}

项目侧的邀请是 `ProjectInvitation` + `InvitationStatus`（`pending` / `accepted` / `declined` / `revoked`——最后一个是**邀请的人反悔了**）。`membership/services.py` 开头写了一条纪律：这里的每一次写都对着调用者授权（`actor` 是必填参数，不是可选礼貌——将来的调用方忘了传会编译不过，而不是无声地越权写）。名册决定谁能进这个项目的房间（`authorize_topic_access`），所以它必须这样写。

一条专门的拒绝规则：`_reject_execution_identity` 挡的是**把人当人邀请进来**这条路（`InvitationService`），挡的判据是 `IdentityService.is_agent`（带 `agent_bindings` 行才算，不看 handle 长得像不像），**不挡直加名册**（`MemberService.add`）——那是平台给 agent 放座位的原语，也是接受邀请时真正落行的地方，所以 agent 照常出现在名册上、带 `agent` 标记，成员页的「AI 队友」那一栏读的就是它。解析不出用户的 handle 一律放行（脚本和测试夹具会加这种，它们不是 agent）。

## 招募 {#recruitment}

`TeamRecruitmentPost` 是团队招募帖（标题、正文、联系方式、`max_members`、`expires_at`），状态 `OPEN` / `CLOSED` / `EXPIRED`。`api/routes/recruitment.py` 上列表、编辑、删除都在，但**「招募广场」在界面上还是「即将推出」**（`frontend/src/views/teams/Explore.vue`），所以今天没有一个入口能发帖——后端实现了，产品面还没开，用户文档里那句「目前显示『即将推出』」说的是界面这一半。

## 边界与坑 {#traps}

- **团队成员不是项目成员行**。团队里加一个人，他在这个团队的所有项目里立刻出现，不需要谁去同步——反过来说，把一个人移出所有项目而留在团队里，只能靠 `ProjectMemberExclusion`。
- **个人团队也是团队**。它出现在团队相关的一切读法里，只是没有 handle、搜不到；按 handle 找团队的地方要自己记得它不存在。
- **名册是读出来的投影**，不是一张表。任何「把名册缓存一份」的想法都会遇上三个来源各自变化（换所有者、退团队、停用队友），而缓存没有任何一处在这些点上会被告知。
- **`revoked` 只属于邀请**。申请那条路没有「发起人反悔」这一格，用户自己取消走的是 `CANCELED`。
- **团队锁定挡的是成员资格，不是项目**。`LOCK_ON_APPROVAL` 的赛题里，团队获批之后人员就冻住了，此时加人、退人都会 `ForbiddenError`，而项目本身照常能用。
