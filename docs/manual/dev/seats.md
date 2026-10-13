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

成员自己的 Claude Code（#2991）也是一位 AI 队友，谁能叫它由它的主人决定。它是 Claude Code 加上主人的一份模型连接：仍是项目里的一个 `AgentInstance`（记忆、席位、名册都照旧），`own_agents` 那一行记它属于谁、用哪份连接：机器上的登录（`device_id`，连接即那台机器上的 `device_claude_login`），或主人账号里保存的 API key、自定义模型服务（`account_connection_id`，`account_model_connections`，密钥加密保存，`agent_instance/account_connections.py`），两者至多一个（`ck_own_agents_one_connection`，`agent_instance/own.py`）。账号里的连接有名字时，区分写这个名字。一个人在一个项目里每份连接可以有一位，名字是「<昵称>的 Claude Code（<区分>）」：订阅写档位，自定义模型服务写模型名，API key 写 `API`；一直带区分，同一主人两位区分相同时后加入的带序号（`own.label`、`own._name_owners_agents`）。机器重新报告登录时（`owner_login.remember`）照当前档位、模型和主人昵称重新命名。

- **怎么出现**：只由主人加入：`POST /projects/{id}/own-agents`（`connection_id` 是自己的一份在用连接，类型须被项目允许，`own.add`）；同一份连接再加入不建第二位，移出过的回来，记忆还在。主人用 `DELETE /projects/{id}/own-agents/{handle}` 移出：`is_active` 置否，退出名册，频道席位撤掉（`revoke_project_seats`）。读名册不建任何东西。主人第一次在频道里 `@` 它时，它入座这个频道（`agent/own_calls.seat_if_named`）。
- **谁能叫**：`own_agents.callers` 记着，`owner` 只有主人（缺省），`project` 项目里的人都能叫；AI 一律不能（`own.callable_by`、`own.may_call`）。只有主人能改，走 `PUT /projects/{id}/own-agents/{handle}/callers`。平台不看它背后接的是什么账号。叫不动的人 `@` 它不起轮次，消息旁落一行说明（`own_calls.refused`）；一条消息点到多位时同样跳过（`delivery/mention.record_mentions`）。任务只能交给它叫得动的那个负责人，换负责人时叫不动的清掉（`routes/topics_tasks.update_task`，`own.may_work_for`）；私聊和定时任务同样只给叫得动的人（`may_chat_with`、`routes/routines`）。
- **项目开关**：`settings.allow_own_agents`，缺省为关，只有值为 `true` 才算打开，管理者在 `PUT /projects/{id}/own-agents` 改。关掉后它们退出项目：不在名册上（`membership/roster.roster`，`/members`、给芝士的名册和 `@` 候选都从这里读），频道里的席位撤掉（`revoke_project_seats`），谁也叫不动，也加入不了。重新打开后，主人加入过的回到名册，第一次被 `@` 时重新入座。
- **允许的连接类型**：`settings.own_agent_kinds`，`subscription`（Claude 订阅）、`api_key`（Anthropic API key）、`model_service`（自定义模型服务）的子集，缺省全部允许，同样由管理者在 `PUT /projects/{id}/own-agents` 改。机器上的连接，类型由 `device_claude_login` 的 `auth_method` 和 `subscription_type` 判出（`device/login_kind.py`）；账号里的连接，类型就是它那一行的 `kind`，只能是 `api_key` 或 `model_service`，订阅不能保存在账号里。类型不被允许的连接加入不了；已加入的一位所用连接的类型后来不被允许时，叫它不起轮次，消息旁落一行说明是哪类连接不被允许（`own.why_not`），会话也不启动，说明同一句（`agent/owner_provider`）。连接被主人解除、退出登录或机器被删掉时同样：叫它不起轮次，会话不启动，说明连接已解除（`own.unusable`）。机器上的连接，会话只在存放这份连接的那台机器上启动，机器不在线时等它回来，不换到主人的其他机器；账号里的连接，会话在会话机上启动（`owner_provider.AccountChannel`），不等任何机器。
- **谁看得到什么**：`GET /projects/{id}/own-agents` 对项目里的人列出每一位的名字、主人、谁能叫和连接类型（订阅带档位，自定义模型服务带模型名，不含服务地址和密钥）；它在哪台机器上、机器在不在线（`machine`）只给主人本人，其他人（管理者也一样）拿到 `null`；连接是否保存在账号里（`connection.on_account`）大家都看得到。读者自己可加入的连接在 `my_connections`，账号里的连接 `on_account` 为真、`machine_name` 为 `null`。
- **在哪里看不到**：它不进给芝士的名册（`roster_rows`），不在队友管理列表和默认队友的候选里（`AgentInstanceService.list_team`）。项目允许时 `/members` 返回它，带 `owner_handle` 和 `callers`，前端只在叫得动它的人的 `@` 候选里列它。

它在哪台机器上跑、怎么计费，见[远程执行](https://github.com/SageSeekerSociety/cheese/blob/main/docs/remote-execution.md)里「A member's own Claude Code」一节。
