---
title: 席位与权限判定
kind: 参考
summary: 谁能在哪里做什么。
covers:
  - backend/app/domain/authz/policy.py
  - backend/app/domain/agent_instance/own/
  - backend/app/auth/
---

# 席位与权限判定 {#seats}

谁能在哪里做什么。

> 讲：判定规则和它在代码里的位置。不讲：界面上怎么邀请成员，见使用文档[团队](/teams#teams)。

## 人和 AI 队友用同一套判定 {#same}

`backend/app/domain/authz/policy.py` 的判定不区分参与者是人还是 AI 队友：

- 没有凭证、或名单为空，一律不授予。
- 共享频道：房间成员或项目成员可以访问。
- 私有频道：必须是这个房间的成员。
- 凭证的作用范围（例如会话令牌只属于某个项目和频道）在请求入口由 `ActorResolver` 先检查，再进入上面的判定。

## 管理项目 {#manage}

一个人在项目里能做什么由 `backend/app/auth/project_roles.py` 回答：所有者（项目的 `owner_handle`，或所属团队的所有者）、管理员（所属团队的管理员，或项目设为管理员的人）、成员，以及项目自己的角色（`project_role_defs`，导师是带 `template = mentor` 标记的一种）。内置两个角色的权限默认值在 `backend/app/domain/project_permissions.py`，项目可以在「角色和权限」里改。路由只问 `project_roles.may(…, Permission.x)`。

## 管理团队 {#teams}

一个人在团队里坐哪个位置、这个位置能做什么，由 `backend/app/domain/team/permissions.py` 回答：所有者、管理员、成员三个位置，每个位置一组固定的权限（看成员、改设置和加入链接、管成员、改角色、管设备和模型连接、管团队文档、发招募帖、解散、转交）。只能移出位置比自己低的人，自己可以离开，所有者只能转交。

看不到这个团队的人（不在隐身团队里）得到 404，看得到但权限不够得到 403。团队的服务只管团队自己的规则，例如个人团队不能解散，不再判断调用者是谁。

## 空间 {#spaces}

空间有自己的位置：所有者（建空间的人）、管理员（课程里就是教师）和成员。每个位置能做什么由 `backend/app/domain/space/permissions.py` 回答：成员看得到空间和里面的内容；管理员还能改设置和分类、管成员和邀请码、发公告、管资料、看数据分析；设置和撤销管理员、删除空间、被驳回后重新提交审核只有所有者能做。所有权可以转交，转交后旧所有者降为管理员。

不在空间里的人得到 404，在空间里但位置不够得到 403。「这人是不是这门课的教师」就是空间的 `teach` 权限（所有者和管理员有），`runs()` 回答「是不是所有者或管理员」。

## 题目 {#problems}

题目没有自己的位置，能做什么看这个人和题目的关系，由 `backend/app/auth/task_roles.py` 回答（它同时用到题目、所在空间和报名的团队，所以和 `project_roles` 一样放在 `app/auth`）：

- `view`：看题和它的附属内容。没审核通过的题只有教它的人看得到；其余的要在板上看得见（在空间里、在题目的可见范围里、没超出每位出题人的上限）。
- `take_up`：领题，在板上看得见就能领，审核状态由路由另说。
- `teach`：改题、删题、批改、管报名，出题人和教这个空间的人都有。
- `approve`：审核通过或退回，只有教这个空间的人有，出题人不能审自己的题。
- `download`：下载题目文件，教它的人和报名已通过的人有。

报名（`ClaimPolicy`，同一个文件）是另一类对象：看提交和批改是教这道题的人和报名者本人，交作业是报名者本人或报名团队的成员，撤回报名是个人报名者本人或教这道题的人。看不到题的人一律 404，包括还没审核通过的题。

## 问答 {#questions}

问答里看、答、关注、投票、评论，登录的人都能做。改问题本身是提问人的事：改、删、设悬赏、采纳答案或撤销采纳、撤回邀请（`backend/app/domain/questions/permissions.py`）；改、删回答是回答作者的事（`backend/app/domain/answers/permissions.py`）。已删除或不存在的问题、回答得到 404。

## 路由怎么声明权限 {#declaring}

每类对象一个策略（`backend/app/core/policy.py` 的 `Policy`，按身份分权限的用 `SeatPolicy`）：它说这类对象的权限有哪些、路径里哪个参数是它的编号、怎么读出来、这个人能不能做某件事、对他存不存在。`backend/app/auth/can.py` 的 `POLICIES` 按权限的类型找到策略。

路由把要的权限写在参数上，参数拿到的就是读出来、判过权限的对象：

```python
async def patch_team(team: Annotated[TeamView, Can(TeamPermission.edit_settings)], ...):
```

用不到对象的路由写在 `dependencies=[Can(...)]` 里。`Can` 也是写接口的写权限声明（`backend/app/api/write_access.py`）。判定顺序：没登录 401；有权限放行；对象不存在或对他不可见 404；看得到但不能做 403。

只看身份判不出来、要看具体对象的规则（只能移出比自己低的人），处理函数用 `can.refuse_unless(条件, 权限, 编号)`；手里已经有对象时（读出来的一份报名）用 `can.require(会话, 用户, 权限, 对象)`。

## 问答 {#questions}

问答里看、答、关注、投票、评论，登录的人都能做。改问题本身是提问人的事：改、删、设悬赏、采纳答案或撤销采纳、撤回邀请（`backend/app/domain/questions/permissions.py`）；改、删回答是回答作者的事（`backend/app/domain/answers/permissions.py`）。路由声明 `needs(QuestionPermission.manage, on="question_id")` 或 `needs(AnswerPermission.manage, on="answer_id")`，已删除或不存在的问题、回答得到 404。

团队、空间、题目、问答四类对象都由 `backend/app/auth/can.py` 判定，旧的「角色 × 动作 × 资源」表（`app/auth/domains`）已经删除。

## AI 队友 {#agents}

AI 队友以席位的身份参与房间，和人走同一套判定。有几项权限只有 AI 队友有（`project_permissions.AGENT_ONLY`：给自己别的线程留便条、签频道的 webhook 凭据、记机器状态），任何人的角色都没有，也授予不了，角色表里不列。平台管理类的操作要求平台管理员身份，AI 队友不在管理员名单里，所以做不了，见[平台管理员](/dev/admins)。

## 团队模型连接和成员归属 {#own-agent}

模型连接归项目所属团队（#3717）。平台保存的 API key 和模型服务通过 `account_model_connections.team_id` 归属团队，设备登录通过 `claude_logins.device_id` 对应设备的 `team_id` 归属团队。团队成员可读，团队管理员可添加、重新检查、改名、解除连接，并修改 `allowed_models`。

`POST /projects/{id}/own-agents` 接收 `connection_id`、`model` 和 `device_id`。三项均从项目所属团队选择；`platform` 表示平台模型来源，平台模型的范围沿用团队方案。执行设备在沙箱中执行工具，不因创建队友而授予整台机器权限。同一连接可以实例化多位队友。添加、编辑、停用和设为默认沿用项目的 `configure_agents` 权限。

`own_agents` 保留模型进程的通道和成员归属：设备登录走 `OwnerChannel`，平台保存的连接走 `AccountChannel`。`owner_user_id` 用于提示词和名称，不决定连接可见性或谁可调用、配置队友。`own.naming.current_role` 在组装提示词时生成成员的私人助手约定；其他成员的任务不因消息作者不是这个成员而被平台拒绝。任务引用的消息仍必须属于本项目，私聊内容不会被引用到其他对话。私密频道是例外：点名只在主人也在这间频道的名册上时才让它入座（`agent/own_calls.seat_if_named`）——入座就能读到这里说的话，等于把频道外的人拉进来。

每份连接可用的模型由团队管理。被解除、退出登录或不再允许所选模型的连接不能开始新工作（`own.admission`、`agent/owner_provider`）。模型进程与执行设备分开，见[远程执行](https://github.com/SageSeekerSociety/cheese/blob/main/docs/remote-execution.md)。
