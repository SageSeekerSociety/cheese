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

## 空间 {#spaces}

空间有自己的角色：所有者（建空间的人）和管理员（课程里就是教师），成员是另一份名单。设置、撤销管理员和改角色都要求调用者自己是所有者（`backend/app/domain/space/services.py` 的 `add_admin`、`remove_admin`、`update_admin_role`）。所有权可以转交，转交后旧所有者降为管理员。「这人是不是这门课的教师」由 `backend/app/auth/space_access.py` 的 `is_space_admin` 回答。

## AI 队友 {#agents}

AI 队友以席位的身份参与房间，和人走同一套判定。平台管理类的操作要求平台管理员身份，AI 队友不在管理员名单里，所以做不了，见[平台管理员](/dev/admins)。

## 成员自己的 Claude Code {#own-agent}

成员自己的 Claude Code（#2991）也是一位 AI 队友，它是主人的私人助手，项目里的人和 AI 都能与它交流。它是 Claude Code 加上主人的一份模型连接：仍是项目里的一个 `AgentInstance`（记忆、席位、名册都照旧），`own_agents` 那一行记它属于谁、用哪份连接：机器上的登录（`device_id`，连接即那台机器上的 `device_claude_login`），或主人账号里保存的 API key、自定义模型服务（`account_connection_id`，`account_model_connections`，密钥加密保存，`agent_instance/account_connections.py`），两者至多一个（`ck_own_agents_one_connection`，`agent_instance/own/`）。连接有名字时，区分写这个名字：账号里的连接是它那一行的 `name`，机器上的登录是 `device_claude_login.name`，都由主人在「模型连接」里改（`PATCH /connector/my/model-connections/{id}`、`PATCH /connector/my/devices/{device_id}/claude-code`），机器上的登录被主人解除时名字一并清掉（`owner_login.revoke`）。一个人在一个项目里每份连接可以有一位，名字是「<昵称>的 Claude Code（<区分>）」：订阅写档位，自定义模型服务写模型名，API key 写 `API`；一直带区分，同一主人两位区分相同时后加入的带序号（`own.label`、`own._name_owners_agents`）。机器重新报告登录时（`owner_login.remember`）照当前档位、模型和主人昵称重新命名；机器不报告名字，主人起的名字不受影响。

- **怎么出现**：只由主人加入：`POST /projects/{id}/own-agents`（`connection_id` 是自己的一份在用连接，类型须被项目允许，`own.add`）；同一份连接再加入不建第二位，移出过的回来，记忆还在。主人用 `DELETE /projects/{id}/own-agents/{handle}` 移出：`is_active` 置否，退出名册，频道席位撤掉（`revoke_project_seats`）。读名册不建任何东西。任何人第一次在频道里 `@` 它时，它入座这个频道；两人私聊只有主人 `@` 它才入座，私聊只容两人（`agent/own_calls.seat_if_named`）。
- **任务归属与入群确认**：所有项目成员和 AI 都能点名它、与它私聊。角色说明要求它只接受主人的任务，其他人的要求先通过 `cheese_ask(to_owner=true)` 在与主人的私聊中征求同意。它创建任务时，`source_message_id` 必须引用主人在本项目发送的消息，平台只校验消息作者和所在项目；被引用的消息在创建任务的这段对话里时才记为任务来源，主人私聊里的同意不会出现在任务的来源里。加入频道后，平台通过同一提问流程向主人发出私聊确认，角色说明要求它得到同意后才在该频道行动。现有智能体的角色说明也在组装 prompt 时按主人现状生成（`own.naming.current_role`），不读保存的配置。
- **项目开关**：`settings.allow_own_agents`，缺省为关，只有值为 `true` 才算打开，管理者在 `PUT /projects/{id}/own-agents` 改。关掉后它们退出项目：不在名册上（`membership/roster.roster`，`/members`、给芝士的名册和 `@` 候选都从这里读），频道里的席位撤掉（`revoke_project_seats`），谁也叫不动，也加入不了。重新打开后，主人加入过的回到名册，第一次被 `@` 时重新入座。
- **允许的连接类型**：`settings.own_agent_kinds`，`subscription`（Claude 订阅）、`api_key`（Anthropic API key）、`model_service`（自定义模型服务）的子集，缺省全部允许，同样由管理者在 `PUT /projects/{id}/own-agents` 改。机器上的连接，类型由 `device_claude_login` 的 `auth_method` 和 `subscription_type` 判出（`device/login_kind.py`）；账号里的连接，类型就是它那一行的 `kind`，只能是 `api_key` 或 `model_service`，订阅不能保存在账号里。类型不被允许的连接加入不了；已加入的一位所用连接的类型后来不被允许时，叫它不起轮次，消息旁落一行说明是哪类连接不被允许（`own.admission.why_not`），会话也不启动，说明同一句（`agent/owner_provider`）。连接被主人解除、退出登录或机器被删掉时同样：叫它不起轮次，会话不启动，说明连接已解除（`own.unusable`）。机器上的连接，会话只在存放这份连接的那台机器上启动，机器不在线时等它回来，不换到主人的其他机器；账号里的连接，会话在会话机上启动（`owner_provider.AccountChannel`），不等任何机器。
- **允许的服务商**：`settings.own_agent_service_hosts`，自定义模型服务服务地址的主机名列表（小写，例如 `api.moonshot.cn`），空或缺省表示不限，同样由管理者在 `PUT /projects/{id}/own-agents` 改（`service_hosts`，只收主机名，不收网址、端口）。只管 `model_service`：机器上的连接比 `device_claude_login.service_host`，账号里的比它那一行 `base_url` 的主机名；主机名未知的视为不在其中（`own.host_refused`）。不在其中的连接加入不了；已加入的一位后来不在其中时，叫它不起轮次、会话不启动，说明里写出主机名（`own.unusable`，`ownAgentHostNotAllowed`）。
- **谁看得到什么**：`GET /projects/{id}/own-agents` 对项目里的人列出每一位的名字、主人和连接类型（订阅带档位，自定义模型服务带模型名，不含服务地址和密钥）；它在哪台机器上、机器在不在线（`machine`）只给主人本人，其他人（管理者也一样）拿到 `null`；连接是否保存在账号里（`connection.on_account`）、项目是否允许它（`connection.allowed`，类型和服务商两项）大家都看得到。读者自己可加入的连接在 `my_connections`，账号里的连接 `on_account` 为真、`machine_name` 为 `null`。
- **在哪里看不到**：它不进给芝士的名册（`roster_rows`），不在队友管理列表和默认队友的候选里（`AgentInstanceService.list_team`）。项目允许时 `/members` 返回它，带 `owner_handle`，前端在所有项目成员的 `@` 候选里列它。

它在哪台机器上跑、怎么计费，见[远程执行](https://github.com/SageSeekerSociety/cheese/blob/main/docs/remote-execution.md)里「A member's own Claude Code」一节。
