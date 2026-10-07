---
title: 席位与权限判定
kind: 参考
summary: 谁能在哪里做什么。
covers:
  - backend/app/domain/authz/policy.py
  - backend/app/domain/agent_instance/own.py
  - backend/app/auth/
---

# 席位与权限判定 {#seats}

谁能在哪里做什么。

> 讲：判定规则和它在代码里的位置。不讲：界面上怎么邀请成员，见使用文档[团队](/teams#teams)。

## 人和 AI 队友用同一套判定 {#same}

`backend/app/domain/authz/policy.py` 的判定不区分参与者是人还是 AI 队友：

- 没有凭证、或名单为空，一律不授予。
- 共享话题：房间成员或项目成员可以访问。
- 私有话题：必须是这个房间的成员。
- 凭证的作用范围（例如会话令牌只属于某个项目和话题）在请求入口由 `ActorResolver` 先检查，再进入上面的判定。

## 管理项目 {#manage}

管理一个项目，包括外部成员和项目设置，要求是项目的所有者，或者项目所属团队的所有者或管理员。

## 空间 {#spaces}

空间有自己的角色：所有者（建空间的人）和管理员（课程里就是教师），成员是另一份名单。设置、撤销管理员和改角色都要求调用者自己是所有者（`backend/app/domain/space/services.py` 的 `add_admin`、`remove_admin`、`update_admin_role`）。所有权可以转交，转交后旧所有者降为管理员。「这人是不是这门课的教师」由 `backend/app/auth/space_access.py` 的 `is_space_admin` 回答。

## AI 队友 {#agents}

AI 队友以席位的身份参与房间，和人走同一套判定。平台管理类的操作要求平台管理员身份，AI 队友不在管理员名单里，所以做不了，见[平台管理员](/dev/admins)。

## 成员自己的 Claude Code {#own-agent}

成员自己的 Claude Code（#2991）也是一位 AI 队友，但只有它的主人能叫它。它仍是项目里的一个 `AgentInstance`（记忆、席位、名册都照旧），`own_agents` 那一行记它属于谁（`agent_instance/own.py`）。

- **怎么出现**：主人读项目名册（`GET /projects/{id}/members`）时，若项目允许、主人有一台登录过的电脑，就建出它（`ensure_for_member`），像 `ensure_identity` 那样读时补齐。主人第一次在频道里 `@` 它时，它入座这个频道（`agent/own_calls.seat_if_named`）。
- **谁能叫**：只有主人，AI 也不能（`own.may_call`）。别人的 `@` 不起轮次，消息旁落一行说明（`own_calls.refused`）；一条消息点到多位时同样跳过（`delivery/mention.record_mentions`）。任务只能交给负责人自己的，换负责人时清掉前任的（`routes/topics_tasks.update_task`，`own.may_work_for`）；私聊和定时任务同样只给主人（`may_chat_with`、`routes/routines`）。
- **项目开关**：`settings.allow_own_agents`，缺省为开，管理者在 `PUT /projects/{id}/own-agents` 改。关掉后谁也叫不动，也不再为新成员建出。
- **在哪里看不到**：它不进给芝士的名册（`roster_rows`），不在队友管理列表和默认队友的候选里（`AgentInstanceService.list_team`）。`/members` 仍返回它，带 `owner_handle`，前端只在主人的 `@` 候选里列它。

它在哪台机器上跑、怎么计费，见[远程执行](https://github.com/SageSeekerSociety/cheese/blob/main/docs/remote-execution.md)里「A member's own Claude Code」一节。
