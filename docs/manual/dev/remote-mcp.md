---
title: 项目自定义 MCP
kind: 参考
summary: 项目 .mcp.json 声明的 MCP 服务怎么被平台代连、代桥接，凭据放在哪。
covers:
  - backend/app/domain/remote_mcp/
  - backend/app/api/routes/remote_mcp.py
  - backend/app/domain/agent/executor_transport.py
  - backend/app/domain/agent/harness/claude_code/remote_execution/bootstrap.py
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/platform_notices.py
  - backend/tests/integration/test_remote_mcp.py
---

# 项目自定义 MCP {#remote-mcp}

项目在主干上放一份 `.mcp.json`，就能给自己所有会话带进它的 MCP 服务。这一页讲平台怎么读它、每个服务由谁来调用、凭据放在哪，以及还没连接的时候房间里会说什么。

> 讲：`.mcp.json` 的解析、连接与调用路径、凭据的存放。不讲：平台自己的工具表怎么交给模型，见[平台工具与会话侧 MCP](/dev/mcp)；会话从哪来、为什么连接一个服务会重启空转的会话，见[会话与轮次](/dev/session)。

## 声明：只认有 `url` 的，只读主干那一份 {#declare}

`remote_mcp/declared.py` 的 `parse()` 读 `mcpServers`，**只留下带 `url` 的条目**：`transport` 是 `sse`（2024-11-05 的 HTTP+SSE 传输）或 `http`（Streamable HTTP），`headers` 里可以写请求头模板。带 `command` 而没有 `url` 的 stdio 服务不在这份名单里，它们是执行机的。`${VAR}` 与 `${VAR:-默认值}` 按 Claude Code 的写法展开，值来自项目设置里存的那几项。

读的是**主干上提交的那一份**：`declared.read()` 通过 `ProjectFiles(..., "committed")` 取 `.mcp.json`，不读房间的 checkout。理由写在模块开头——agent 能改自己的 checkout，从那里取一个 URL，就等于让它把某个已连接的名字指到自己挑的主机，再把项目的 token 送过去。**决定 token 往哪送的，只能是提交过的配置。**

结果按项目缓存 60 秒（`_CACHE_S`）；设置页每次都读最新的（`fresh=True`）。取不到时 `Declared.problem` 是 `missing` / `invalid` / `unreadable`：前两个如实说；`unreadable`（forge 挂了）保留上一次的答案——会话带着哪些服务启动是它身份的一部分，一次故障期间答「没有」会让每个空转的会话来回重启。

## 四种状态，两份名单 {#states}

`service._status()` 给每个声明过的服务算一个状态，设置页和房间都渲染它：

| 状态 | 什么时候是这样 |
| --- | --- |
| `missing_values` | 展开时缺一个没有默认值的 `${VAR}` |
| `ready` | 条目带 `headers`：它靠请求头里的密钥授权，不需要连接 |
| `disconnected` | 没有连接记录 |
| `needs_reconnect` | 授权被拒过，或 `.mcp.json` 现在指的 URL 与当初发 token 的那个不一样 |
| `connected` | 其余 |

能用的只有 `connected` 和 `ready`（`USABLE`）。`session_servers()` 按它分成 `usable` / `unusable` 两份名单，`session_target()` 把能用的写进会话的执行目标（`central_provider.py`）：`{"path": "/topics/{id}/mcp", "servers": [...]}`，一个能用的都没有时这个键不出现。**这个键属于启动身份（`screen_identity.launch_identity`）**，所以有人连上一个服务，空转的会话会被重启带进来；没连上的那批不进执行目标，进的是提示里的一句话（见「没连接时」一节）。

## 连接：平台当 OAuth 客户端 {#connect}

`POST /projects/{id}/mcp/servers/{name}/connect` 走 `service.begin_connect()`：带 `headers` 的服务直接拒绝（它不需要连接）；缺值先请人去填；然后 `oauth.discover()` 按 MCP 授权规范（2025-11-25）走 RFC 9728 找授权服务器、RFC 8414 找端点，`oauth.register()` 按规范给的顺序拿客户端——预先登记的、平台自己域名上的 Client ID Metadata Document、动态注册（RFC 7591）。授权码流程用 PKCE S256。

`state` 是一份密封的 JSON，装着这次流程的全部（jti、600 秒过期、服务名、URL、verifier、注册结果），并在 `single_use_state` 里先占一个 jti；浏览器带着它去授权。回来落在 `GET /api/mcp/oauth/callback`：先 `claim()` 那个 jti（**只能用一次**），再按 RFC 9207 核对 `iss` 与发起时的 issuer 是不是同一个（不是就当 mix-up 拒绝），换 token 时带上 RFC 8707 的 `resource`。写库前先把上一次的连接撤销并删掉；一行 `project_mcp_connections` 记下这次授权，`upstream.forget()` 丢掉这个服务在这个进程里的 MCP 会话。

日常只在 `service._bearer()`：到期前 60 秒刷新；服务回 401 就带着被拒的那个 token 再刷一次（若另一次请求已经换过了，直接用新的）；刷新被拒就标 `needs_reconnect`，请人回项目设置重连。断开走 `disconnect()`：尽力撤销（授权服务器没有撤销端点，或它自己挂了，就留给 token 自己过期），删行，丢会话。

## 凭据：放在哪，谁看得到 {#credentials}

两张表：`project_mcp_connections` 存连接（access / refresh token、client secret、各端点、`authorized_by`），`project_mcp_secrets` 存 `${VAR}` 的值。它们都是**项目的**，不是某个人的：谁连的，这个项目之后的会话就以谁的上游账号行动，`authorized_by` 记下来给所有人看。值用 `Purpose.MCP_OAUTH_TOKEN` / `MCP_SECRET` 密封，绑定到「项目 + 服务 + 列名」，换一行就解不开。

**没有任何路由回一个凭据值。** `settings_view()` 对每个变量只给 `set: true/false` 加 `updated_by`、`updated_at`；`room_view()` 只给名字、主机、谁授权、什么时候。两份都带 `declared_by`，说这个服务从哪来：项目 `.mcp.json` 里的是 `null`，否则是声明它的那几个队友类型（`name` 与 `title`）；设置页和现场的列表据此在每一行标出来源。管理路由全过 `member()`：登录、且是项目成员——**任何成员都能连、能断、能填值**，因为这份连接是项目的；房间侧那份只读（`GET /topics/{id}/mcp/servers`）。

会话那条路是 `POST /topics/{id}/mcp/{name}`（`include_in_schema=False`）：用**房间的凭据**认证（`x-cheese-token`，`t` 必须是这个房间、`p` 必须是它所属的项目，凭据上的队友还得在这个房间的名册上，`require_seated_agent`），服务凭据由平台在这里附上。凭据不过关是 HTTP 401/403；过了关之后的失败一律写成工具的回答（`ok({"error": ...})`）而不是 HTTP 错误：对 agent 来说，没连接、名字不对、服务没答复，下一步能做的事都一样。

出网的每一个请求都过 `remote_mcp/http.py` 这一个客户端：非 HTTPS 直接拒、不跟随跳转、主机落在平台自己的网段就拒（`refuse_internal_host`）。`remote_mcp_allow_private_hosts` 只给本地测试服务器开。

## 一次调用：平台代连，stdio 在机器上跑 {#call}

| 条目 | 谁调用 | 怎么到那 |
| --- | --- | --- |
| 带 `url`（远程） | 平台 | 会话的调用转成 `POST /topics/{id}/mcp/{name}`，凭据在平台侧附上（`executor_transport.remote_mcp()`：「不走房间的机器，也不为它占一台机器」） |
| 带 `command`（stdio） | 房间的机器 | `bootstrap.process_servers()` 只挑没有 `url` 的启动；给模型的名字是 `mcp__<服务>__<工具>` |

Claude Code 那边两类共用一座桥：`prepare()` 写 `mcp.json` 时把机器上的 stdio 服务和项目里已连的远程服务并进同一张 `bridged` 名单，每个都注册成本地的 stdio MCP 服务，服务器名就用服务自己的名字——**除了 `native`，那是文件操作的，撞上直接报错**。桥进程要么就地转发（每个 `tools/call` 走 `RemoteClient.call`），要么被 exec 到另一侧跑；送到哪由 `RemoteClient.call` 判：名字在远程服务名单里就走 `remote_mcp()`，也就是打成平台的那个 POST。Codex 走同一份名单（`harness/codex/channel.py`），`project_tools` 列服务时也是 stdio 与远程一起列。

私聊没有检出，所以没有 checkout 里的 stdio 服务；但私聊里的队友还是同一位队友，执行目标照样带上项目的远程服务和它类型自己的服务（`central_provider.py` 对两种目标同一段代码），类型的 stdio 服务跑在私聊的草稿容器里。

## 没连接时，房间里说一声 {#notice}

会话在一个房间里开起来的时候（`chat.py` 的 `_unconnected_mcp()`），`unusable` 里的每个服务在房间里落一条 event block（`platform_notices.EVENT_MCP_NOT_CONNECTED`，`meta.server` 记名字），正文是「`<名字>` 需要在项目设置里连接」。**每间房每个服务只说一次**：发之前先查这个房间有没有同名的这类 block。同一时刻提示里多一行：「项目的远程 MCP 服务器 …… 需要项目成员在项目设置里连接，这个会话里用不了它们的工具」——不说的话，agent 会去找那些不存在的工具，或者把它们的缺席当成环境坏了。整件事包在 `except` 里：一条通知永远不许弄坏一轮。

## 谁保证它不坏 {#held}

连接、状态、调用和那条房间通知由 `backend/tests/integration/test_remote_mcp.py` 与 `test_remote_mcp_session.py` 覆盖；机器上那份 stdio 的行为由 `scripts/remote_execution/room_fixture.py` 带一个真会回话的 `custom_mcp.py` 覆盖。名字相近的 `scripts/remote_execution/mcp_contract.py` 讲的不是这里：它钉的是执行器自己的 `claude mcp serve`（读写与命令），属于[平台工具与会话侧 MCP](/dev/mcp)。
