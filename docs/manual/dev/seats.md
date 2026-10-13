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

成员自己的 Claude Code（#2991）也是一位 AI 队友，谁能叫它由它的主人决定。它仍是项目里的一个 `AgentInstance`（记忆、席位、名册都照旧），`own_agents` 那一行记它属于谁（`agent_instance/own.py`）。

- **怎么出现**：主人读项目名册（`GET /projects/{id}/members`）时，若项目允许、主人有一台登录过的电脑，就建出它（`ensure_for_member`），像 `ensure_identity` 那样读时补齐。主人第一次在频道里 `@` 它时，它入座这个频道（`agent/own_calls.seat_if_named`）。
- **谁能叫**：`own_agents.callers` 记着，`owner` 只有主人（缺省），`project` 项目里的人都能叫；AI 一律不能（`own.callable_by`、`own.may_call`）。只有主人能改，走 `PUT /projects/{id}/own-agents/{handle}/callers`。平台不看它背后接的是什么账号。叫不动的人 `@` 它不起轮次，消息旁落一行说明（`own_calls.refused`）；一条消息点到多位时同样跳过（`delivery/mention.record_mentions`）。任务只能交给它叫得动的那个负责人，换负责人时叫不动的清掉（`routes/topics_tasks.update_task`，`own.may_work_for`）；私聊和定时任务同样只给叫得动的人（`may_chat_with`、`routes/routines`）。
- **项目开关**：`settings.allow_own_agents`，缺省为关，只有值为 `true` 才算打开，管理者在 `PUT /projects/{id}/own-agents` 改。关掉后它们退出项目：不在名册上（`membership/roster.roster`，`/members`、给芝士的名册和 `@` 候选都从这里读），频道里的席位撤掉（`revoke_project_seats`），谁也叫不动，也不再为新成员建出。重新打开后，主人读名册时它回来，第一次被 `@` 时重新入座。
- **允许的连接类型**：`settings.own_agent_kinds`，`subscription`（Claude 订阅）、`api_key`（Anthropic API key）、`model_service`（自定义模型服务）的子集，缺省全部允许，同样由管理者在 `PUT /projects/{id}/own-agents` 改。类型由 `device_claude_login` 的 `auth_method` 和 `subscription_type` 判出（`device/login_kind.py`）。主人登录过的机器里没有一台的类型被允许时，叫它不起轮次，消息旁落一行说明是哪类连接不被允许（`own.why_not`）；会话只在类型被允许的机器上启动，一台都没有时不启动，说明同一句（`agent/owner_provider`）。
- **谁看得到什么**：`GET /projects/{id}/own-agents` 对项目里的人列出每一位的名字、主人、谁能叫和连接类型（订阅带档位，自定义模型服务带模型名，不含服务地址和密钥）；它在哪几台机器上、机器在不在线（`machines`）只给主人本人，其他人（管理者也一样）拿到 `null`。
- **在哪里看不到**：它不进给芝士的名册（`roster_rows`），不在队友管理列表和默认队友的候选里（`AgentInstanceService.list_team`）。项目允许时 `/members` 返回它，带 `owner_handle` 和 `callers`，前端只在叫得动它的人的 `@` 候选里列它。

它在哪台机器上跑、怎么计费，见[远程执行](https://github.com/SageSeekerSociety/cheese/blob/main/docs/remote-execution.md)里「A member's own Claude Code」一节。
