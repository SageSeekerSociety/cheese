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

一个人在项目里能做什么由 `backend/app/auth/project_roles.py` 回答：所有者（项目的 `owner_handle`，或所属团队的所有者）、管理员（所属团队的管理员，或项目设为管理员的人）、成员、导师，每个角色的权限默认值在 `backend/app/domain/project_permissions.py`，项目可以在「角色和权限」里改。路由只问 `project_roles.may(…, Permission.x)`。

## 管理团队 {#teams}

一个人在团队里坐哪个位置、这个位置能做什么，由 `backend/app/domain/team/permissions.py` 回答：所有者、管理员、成员三个位置，每个位置一组固定的权限（看成员、改设置和加入链接、管成员、改角色、管设备和模型连接、管团队文档、发招募帖、解散、转交）。只能移出位置比自己低的人，自己可以离开，所有者只能转交。

路由在声明里写它要哪项权限：`needs(TeamPermission.x, on="teamId")`（`backend/app/api/write_access.py`），由 `backend/app/auth/can.py` 在进入处理函数之前判定。看不到这个团队的人（不在隐身团队里）得到 404，看得到但权限不够得到 403。团队的服务只管团队自己的规则，例如个人团队不能解散，不再判断调用者是谁。

## 空间 {#spaces}

空间有自己的角色：所有者（建空间的人）和管理员（课程里就是教师），成员是另一份名单。设置、撤销管理员和改角色都要求调用者自己是所有者（`backend/app/domain/space/services.py` 的 `add_admin`、`remove_admin`、`update_admin_role`）。所有权可以转交，转交后旧所有者降为管理员。「这人是不是这门课的教师」由 `backend/app/auth/space_access.py` 的 `is_space_admin` 回答。

## AI 队友 {#agents}

AI 队友以席位的身份参与房间，和人走同一套判定。平台管理类的操作要求平台管理员身份，AI 队友不在管理员名单里，所以做不了，见[平台管理员](/dev/admins)。

## 团队模型连接和成员归属 {#own-agent}

模型连接归项目所属团队（#3717）。平台保存的 API key 和模型服务通过 `account_model_connections.team_id` 归属团队，设备登录通过 `claude_logins.device_id` 对应设备的 `team_id` 归属团队。团队成员可读，团队管理员可添加、重新检查、改名、解除连接，并修改 `allowed_models`。

`POST /projects/{id}/own-agents` 接收 `connection_id`、`model` 和 `device_id`。三项均从项目所属团队选择；`platform` 表示平台模型来源，平台模型的范围沿用团队方案。执行设备在沙箱中执行工具，不因创建队友而授予整台机器权限。同一连接可以实例化多位队友。添加、编辑、停用和设为默认沿用项目的 `configure_agents` 权限。

`own_agents` 保留模型进程的通道和成员归属：设备登录走 `OwnerChannel`，平台保存的连接走 `AccountChannel`。`owner_user_id` 用于提示词和名称，不决定连接可见性或谁可调用、配置队友。`own.naming.current_role` 在组装提示词时生成成员的私人助手约定；其他成员的任务不因消息作者不是这个成员而被平台拒绝。任务引用的消息仍必须属于本项目，私聊内容不会被引用到其他对话。私密频道是例外：点名只在主人也在这间频道的名册上时才让它入座（`agent/own_calls.seat_if_named`）——入座就能读到这里说的话，等于把频道外的人拉进来。

每份连接可用的模型由团队管理。被解除、退出登录或不再允许所选模型的连接不能开始新工作（`own.admission`、`agent/owner_provider`）。模型进程与执行设备分开，见[远程执行](https://github.com/SageSeekerSociety/cheese/blob/main/docs/remote-execution.md)。
