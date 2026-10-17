---
title: 项目的 MCP 服务器
kind: 参考
summary: 项目的 MCP 服务器从哪来、会话怎么连到它们（会话机上的网关）、本地命令在哪跑、凭据和密钥放在哪。
covers:
  - backend/app/domain/remote_mcp/
  - backend/app/api/routes/remote_mcp.py
  - backend/app/api/routes/local_mcp.py
  - backend/app/domain/agent/executor_transport.py
  - backend/app/domain/agent/harness/driven/mcp_gateway.py
  - backend/app/domain/agent/harness/claude_code/remote_execution/bootstrap.py
  - backend/app/domain/agent/harness/claude_code/remote_execution/runtime_calls.py
  - backend/app/domain/device/mcp_machines.py
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/platform_notices.py
  - backend/tests/integration/test_remote_mcp.py
  - backend/tests/integration/test_local_mcp_servers.py
---

# 项目的 MCP 服务器 {#remote-mcp}

项目的 MCP 服务器有三个来源：仓库主干上的 `.mcp.json`、项目设置里添加的服务器、队友类型自带的服务器。每个服务器要么是**远程的**（一个地址，平台拿着凭据去调），要么是**本地命令**（一条命令，在房间的机器上跑）。三种 harness 都用自己的 MCP 客户端，连到会话机上同一个网关；网关再把调用送到服务器所在的地方。

> 讲：服务器清单怎么合、会话怎么连、调用往哪送、凭据和密钥放在哪、本地命令在成员电脑上要谁允许。不讲：平台自己的工具表怎么交给模型，见[平台工具与会话侧 MCP](/dev/mcp)；会话从哪来、为什么连接一个服务会重启空转的会话，见[会话与轮次](/dev/session)。

## 清单：主干的、设置里加的、类型自带的 {#declare}

`remote_mcp/declared.py` 的 `parse()` 读 `mcpServers`：带 `url` 的是远程服务器（`RemoteServer`，`transport` 是 `sse` 或 `http`，`headers` 可以写请求头模板），带 `command` 的是本地命令（`LocalServer`：`command`、`args`、`env`）。`${VAR}` 与 `${VAR:-默认值}` 按 Claude Code 的写法认。

读的是**主干上提交的那一份**，不读房间的 checkout：agent 能改自己的 checkout，从那里取一个 URL，就等于让它把一个已连接的名字指到自己挑的主机，再把项目的 token 送过去；从那里取一个 `${VAR}`，就等于让它点名要项目的任何一个密钥。结果按项目缓存 60 秒，设置页每次读最新的。取不到时 `Declared.problem` 是 `missing` / `invalid` / `unreadable`；`unreadable`（forge 挂了）保留上一次的答案，免得一次故障让空转的会话来回重启。

在项目设置里添加的服务器由 `with_settings` 并进来：远程的存在 `project_mcp_servers`，本地命令存在 `project_mcp_commands`（`command` 按 shell 的写法拆成命令和 `args`，加上 `env`）。名字和 `.mcp.json` 里的重了，以仓库的为准，设置里那条标成 `shadowed`、不生效。项目里一个名字只能有一个来源：添加时名字已被任何来源用了就拒绝（`_name_free`）。队友类型自带的服务器由 `with_types` 并进来，一个名字项目已经用了就归项目；几个类型声明同一个本地命令，列一次，标上每个声明它的类型（`local_types`）。

## 远程服务器的状态 {#states}

`service._status()` 给每个远程服务器算一个状态：

| 状态 | 什么时候是这样 |
| --- | --- |
| `missing_values` | 展开时缺一个没有默认值的 `${VAR}` |
| `ready` | 靠请求头里的密钥授权，不需要连接 |
| `disconnected` | 没有连接记录 |
| `needs_reconnect` | 授权被拒过，或地址和当初发 token 的那个不一样了 |
| `connected` | 其余 |

能用的是 `connected` 和 `ready`。`session_target()` 把能用的写进会话的执行目标：`{"path": "/topics/{id}/mcp", "servers": [...]}`。**这个键属于启动身份**，所以有人连上一个服务器，空转的会话会被重启带进来；没连上的那批进提示里的一句话（见[没连接时](#notice)）。

## 会话怎么连：会话机上的网关 {#gateway}

每个会话在会话机上开一个网关（`harness/driven/mcp_gateway.py`）：只听本机回环地址，每个服务器一条路径（`/mcp/<名字>`），只收带这个会话密钥（`X-Cheese-Gateway`）的请求——会话机是共用的，回环地址别的会话也够得着。它自己回握手，回的是 `initialize` 那一代协议：客户端问的修订版它会说就照它的回，不会说就回三家都会说的 `2025-06-18`；先发 `server/discover`（无状态那一代）的客户端收到「没有这个方法」，按规范退回 `initialize`。它只提供工具：`resources/*`、`prompts/*` 一律「没有这个方法」，免得为了一张没人读的清单去起一条命令或占一台机器。

工具的列表和调用交给 `RemoteClient.call`（`executor_transport.py`），送到服务器所在的地方：远程服务器打成平台的 `POST /topics/{id}/mcp/{name}`，凭据在平台侧附上，不占机器；本地命令走房间机器上的执行器。三种 harness 怎么连：

| harness | 网关开在哪 | 怎么告诉它 |
| --- | --- | --- |
| Claude Code | `client.py gateway` 子进程，`bootstrap` / `launch` 在 exec Claude Code 之前起（`start_gateway`），随 Claude Code 结束 | `mcp.json` 里每个服务器是 `type: "http"`，地址和请求头写成 `${CHEESE_MCP_PORT}`、`${CHEESE_MCP_SECRET}`，由 Claude Code 自己展开 |
| Codex | runner 进程里（`codex/entry.py`，`RemoteTools.gateway()`） | `app-server` 的 `-c mcp_servers.<名字>.url=…`，请求头用 `env_http_headers` 从环境变量读 |
| pi | runner 进程里（`runner.open_servers`） | `platform.json` 的 `mcp` 交给扩展，`platform.ts` 逐个 `pi.registerMcpServer`；启动时加载 `builtin:mcp`、`builtin:codemode`、`builtin:tool-search` |

密钥只走环境变量，不上命令行：命令行会话机上的每个进程都读得到。三家等服务器第一次列工具都放宽到 300 秒（`STARTUP_S`）：一条命令第一次跑要下载包，机器也可能还在准备。

### 钩子由谁触发 {#hooks}

项目在 `.claude/settings.json` 里写的 PreToolUse / PostToolUse 钩子，由会自己触发钩子的 harness 自己触发，网关把调用标成「已触发」（`platform: True`），执行器不再跑一遍：Claude Code 本来就会；pi 的扩展（`applyProjectHooks`）在每次调用前后问 runner，MCP 工具也问。Codex 自己从不触发：它自带的工具全由平台的动态工具代替，钩子一直是执行器替它跑的。所以 Codex 的网关换一个 `tool_call`（`RemoteTools.mcp_tool_call`）：本地命令交给执行器连钩子一起跑，远程服务器用 `remote_call_with_hooks` 在机器上先后跑两次钩子。

## 本地命令：在哪跑，拿到什么，谁允许 {#local}

本地命令由房间机器上的执行器起：仓库里的那条按**它自己 checkout 里**的定义（`bootstrap.process_servers()` 挑没有 `url` 的）；设置里加的和队友类型自带的那条，定义写进执行目标的 `mcp_commands`（设置里加的同名时优先），由 `RemoteClient` 随调用带过去。平台在旁边管三件事（`remote_mcp/local.py`）：

- **能拿到哪些密钥。** 一条命令第一次被用到时，`RemoteClient._local_start` 问 `POST {mcp_path}/{name}/start`，拿回 `values`：主干上（或设置里）**这个名字的定义**里出现的 `${VAR}` 中已经填了值的那几个；设置里加的远程服务器的请求头密钥（`CHEESE_MCP_*`）永远不给命令。值随调用交给执行器（`params.values`），执行器起进程时展开 `command`、`args`、`env`、`cwd` 里的 `${VAR}`；值不写进会话机上的任何文件。设置页明说：这个值在项目机器上，芝士读得到。
- **在成员自己的电脑上，要电脑的主人允许。** 同一个请求还拿回 `allowed`：项目机器是成员的电脑（`mcp_machines.machine`，`Supply` 不是 `cloud`）时，是主人允许过的命令指纹；平台的机器上是 `None`，谁也不问。执行器起进程前对**它真要跑的那条定义**（`${VAR}` 不展开）算指纹（`runtime_calls.server_fingerprint`），不在清单上就不起，报错以 `MCP_APPROVAL_REQUIRED` 开头带上指纹和命令；`RemoteClient.local_outcome` 把它报成 `awaiting`，后端在 `project_mcp_approvals` 记一条，连同命令写在哪（`source`：仓库、设置、类型）和谁写的，主人在「等你处理」里答（`POST /mcp/approvals/{id}`，只有设备主人能答）。允许过的同一条命令不再问，命令一改指纹就变，再问一次。所有答复的摘要写进执行目标（`mcp_decided`），所以一有答复，空转的会话会带着新清单重启。成员自己启动的 Claude Code（设备启动，没有 `mcp_path`）不问：那台电脑是他自己的，也是他自己开的会话。
- **上次运行得怎样。** 网关把每个服务器第一次列工具的结果报给 `POST {mcp_path}/{name}/run`：成功是 `running`，失败是 `failed` 带日志最后 12 行（执行器起进程失败时把 `mcp-<hash>.log` 的末尾接在报错后面），等主人是 `awaiting`；网关关门时把报过 `running` 的改回 `idle`。`running` 只信 12 小时（`RUNNING_FOR`）：一个和会话一起死掉的网关说不出它停了。存在 `project_mcp_runs`，设置页读它。

平台在机器上放 Node 24（`node` / `npm` / `npx`）和 uv（`uv` / `uvx`），排在机器自己的 PATH 后面：成员电脑上已有的 node 照用（`toolchain.py`、`machine_launcher.py`）。Docker、Deno、Bun 不提供。

## 连接远程服务器：平台当 OAuth 客户端 {#connect}

`POST /projects/{id}/mcp/servers/{name}/connect` 走 `service.begin_connect()`：带 `headers` 的服务器直接拒绝（它不需要连接）；缺值先请人去填；然后 `oauth.discover()` 按 MCP 授权规范（2025-11-25）走 RFC 9728 找授权服务器、RFC 8414 找端点，`oauth.register()` 按规范给的顺序拿客户端——预先登记的、平台域名上的 Client ID Metadata Document、动态注册（RFC 7591）。授权码流程用 PKCE S256。

`state` 是一份密封的 JSON，装着这次流程的全部，并在 `single_use_state` 里先占一个 jti；回调落在 `GET /api/mcp/oauth/callback`：先 `claim()` 那个 jti（只能用一次），再按 RFC 9207 核对 `iss`——指向别的授权服务器要拒，授权服务器声明了会带 `iss` 却没带也要拒——换 token 时带上 RFC 8707 的 `resource`。到期前 60 秒刷新；服务器回 401 就刷新一次再试；刷新被拒标 `needs_reconnect`。断开时尽力撤销。

设置里添加和编辑服务器（`POST` / `PUT` / `DELETE /projects/{id}/mcp/servers…`，`kind` 是 `remote` 或 `command`）要「配置 AI 队友」的权限。改了一条命令，它在成员电脑上的指纹就变了，主人会被再问一次。改了地址或换了登录方式，已有的连接和请求头一并清掉（`_forget_credentials`），免得一个密钥被送到不是给它的主机。

## 凭据：放在哪，谁看得到 {#credentials}

`project_mcp_connections` 存连接（token、client secret、端点、`authorized_by`），`project_mcp_secrets` 存 `${VAR}` 的值，按「项目 + 变量名」一份，远程服务器和本地命令共用。它们是**项目的**：谁连的，项目之后的会话就以谁的上游账号行动，`authorized_by` 给所有人看。值用 `Purpose.MCP_OAUTH_TOKEN` / `MCP_SECRET` 密封，绑定到所在的行。

**没有任何路由回一个凭据值。** `settings_view()` 对每个变量只给 `set`、`updated_by`、`updated_at`；房间侧的只读视图（`GET /topics/{id}/mcp/servers`）只列远程服务器。唯一把密钥值送出平台的是 `.../start`，只送给本地命令所在的机器，只送它主干定义里点了名的那几个。

会话的两类请求（远程调用 `POST /topics/{id}/mcp/{name}`、本地命令的 `start` / `run`）用**会话的凭据**认证（`auth.session_in`：`t` 是这段对话，`p` 是它所属的项目，凭据上的队友还在名册上）。远程调用过了关之后的失败一律写成工具的回答（`ok({"error": ...})`）：对 agent 来说，没连接、名字不对、服务器没答复，下一步能做的事一样。出网请求都过 `remote_mcp/http.py`：非 HTTPS 拒、不跟随跳转、平台自己的网段拒。

## 没连接时，房间里说一声 {#notice}

会话在一个房间里开起来的时候（`chat.py` 的 `_unconnected_mcp()`），`unusable` 里的每个远程服务器在房间里落一条 event block（`notice_meta.EVENT_MCP_NOT_CONNECTED`），正文是「`<名字>` 需要在项目设置里连接」，每间房每个服务器只说一次；提示里同时多一行，免得 agent 去找那些不存在的工具。整件事包在 `except` 里：一条通知不许弄坏一轮。

## 谁保证它不坏 {#held}

网关的协议行为：`tests/unit/test_mcp_gateway.py`。三种 harness 经网关列工具、调用、钩子：`test_agent_type_mcp_harnesses.py`、`test_remote_mcp_hooks.py`、`test_codex_checkout_mcp.py`、`test_remote_mcp_session.py`（Claude Code 那两条起真的 Claude Code）。远程服务器的连接与状态：`test_remote_mcp.py`、`test_mcp_servers_added_in_settings.py`。本地命令的密钥、运行状态和允许：`test_local_mcp_servers.py`；执行器拒绝没允许的命令、展开变量：`test_local_mcp_on_the_machine.py`。
