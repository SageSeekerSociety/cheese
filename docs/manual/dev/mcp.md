---
title: 平台工具与会话侧 MCP
kind: 参考
summary: 平台工具表怎么变成每种骨架手里的工具，以及机器够不着时怎么办。
covers:
  - backend/sandbox/cheese
  - backend/app/domain/agent/cli_worker.py
  - backend/app/domain/agent/harness/pi/catalog.py
  - backend/app/domain/agent/executor_transport.py
  - backend/app/domain/agent/harness/codex/tools.py
  - backend/app/domain/agent/harness/claude_code/remote_execution/client.py
---

# 平台工具与会话侧 MCP {#mcp}

芝士在房间里做的事——发消息、开任务、递卡、写文档——都是调用平台工具。这一页讲这张表长什么样、它怎么变成三种骨架各自手里的工具，以及机器够不着时会发生什么。

> 讲：平台工具表的来源、三种骨架各自的暴露方式、机器够不着时的答复。不讲：项目自己声明的 MCP 服务，见[项目自定义 MCP](/dev/remote-mcp)；一轮的整体流程，见[一条消息怎么变成芝士的一轮](/dev/turn)。

## 一张表，46 样 {#table}

`backend/sandbox/cheese` 里的 `PLATFORM_TOOLS` 是一个 `ToolTable` 常量，**会话侧平台工具的唯一来源**（结论 21，由结论 63 修订）。今天表上有 46 样：

| 类别 | 工具 |
| --- | --- |
| 对话 | `chat_send`、`chat_edit`、`todo_write` |
| 读房间 | `cheese_chat_list`、`cheese_chat_search`、`cheese_chat_get`、`cheese_chat_replies` |
| 文档 | `cheese_doc_get`、`cheese_doc_set`、`cheese_doc_edit`、`cheese_doc_new`、`cheese_doc_list` |
| 任务与验收 | `cheese_task`、`cheese_close_task`、`cheese_accept_request`、`cheese_describe`、`cheese_ready`、`cheese_tell` |
| 记录 | `cheese_title` |
| 通知与拍板 | `cheese_notify`、`cheese_ask` |
| 资料 | `cheese_fetch`、`cheese_docs_search`、`cheese_docs_read`、`cheese_library_ls` |
| 锁 | `cheese_lock`、`cheese_unlock` |
| 房间状态 | `cheese_members`、`cheese_status` |
| 机器与调度 | `cheese_machine`、`cheese_wait_machine`、`cheese_note`、`cheese_deliver_at` |
| 定时与触发 | `cheese_routine_draft`、`cheese_routine_list`、`cheese_routine_update`、`cheese_routine_pause`、`cheese_routine_report` |
| 项目技能 | `cheese_skill_draft`、`cheese_skill_update` |
| 反馈 | `cheese_feedback_propose`、`cheese_feedback_list`、`cheese_feedback_get`、`cheese_feedback_claim`、`cheese_feedback_release` |
| 其余的平台接口 | `platform_request` |

这张表以前是**问出来的**：会话侧的 MCP 服务器收到 `tools/list` 就去执行器要一份，执行器再把机器上那棵 argparse 树翻成工具。于是一台执行机够不着，整个 `cheese_*` 家族就从清单里消失，agent 被告知「没有这个工具」——而它这一刻最需要的恰恰是跟房间说一句这里出事了。表变成常量之后它不再问任何人：这些要的是平台，不是那台机器，所以从会话直接打后端，机器离线时一样不少。

常用的动作各有一样专门的工具，其余的只有 `platform_request` 一样，原因和用法见下面[其余的平台接口](#rest)。

只有两样还要机器上的一份东西：`cheese_doc_set` 要读机器上那个文件，`cheese_accept_request` 先让机器把任务的提交推上去。要作为进程在机器上跑的（任务目录、同步与恢复、预览、文件转换、git 凭据）不在表里，是这个文件的 CLI 子命令。

## 其余的平台接口：platform_request {#rest}

房间成员在网页上能做的事，agent 一样也不该做不到；可后端有六百多个接口，每个都做成一样工具，工具定义每一轮都要整份塞进模型的上下文。所以专门工具只给常用的动作，其余的走一样通用工具 `platform_request`，它自己能被找到：

- **找**：只传 `find`（几个英文关键词），它读后端自己的 `GET /openapi.json`，按词首匹配方法、路径、摘要、说明、`operationId` 和分组，返回前 12 个的方法、路径、摘要、说明首段、路径与查询参数、JSON 请求体字段，再列出其余匹配的方法和路径；什么都不传，列出按分组的接口数。目录来自正在跑的后端，这里没有第二份清单会落后于它。整份文档约 800 KB，不交给模型。
- **调**：传 `method`、相对 API 根的 `path` 和 `body`，返回后端的 JSON 原文。路径只能是 API 根下的相对路径（带协议、主机或 `#` 的当场拒，一个请求也不发），因为房间凭据跟着它走。回的不是 JSON（文件下载）时说用 `cheese pull` / `cheese library get` 取。
- **权限在路由里**：它带的是会话启动时铸的那张凭据（`mint_session_token`，项目级、写着是哪位 agent），后端照常按席位、角色和项目成员身份判，跟坐在同一个位置上的人得到同一个答案——普通成员改不了房间名册，owner / admin 才能；协作模式的项目里，采纳验收卡在服务里只归人（`_forbid_ai`）。哪条路由都不按「调用方是不是 agent」分支。
- **够不着的那一类**：这张凭据绑在一个项目上，所以不点名项目或房间的「个人」接口（跨项目的 `/awaiting-me`、`/notifications` 之类）拒它。agent 的收件箱是 `GET /projects/{project_id}/inbox`；`/awaiting-me` 收的是验收人、需求提出者、回答提问的人这些只有人会被点到的事项。

## 三种骨架，三种拼法 {#harnesses}

同一件事在三种骨架里名字不同，因为把工具交给模型的机制各不相同：

| 骨架 | 怎么拿到工具 | 名字长什么样 |
| --- | --- | --- |
| Claude Code | 执行器的 `native` MCP 服务器：`tools/list` 的最后一批就是 `PLATFORM_TOOLS.schemas()`；容器里的 `proxy.js` 给它们加 `mcp__native__` 前缀 | `mcp__native__chat_send` |
| Codex | 后端把表当 `dynamicTools` 发过去（`harness/codex/tools.py` 的 `platform_tools()`、`RemoteTools.discover`），调用按 `PLATFORM` 这条路由原地执行 | `chat_send` |
| pi | 平台给 pi 的 extension（`harness/pi/platform.ts` 的 `registerPlatformTools`）把目录注册成工具；目录由 `harness/pi/catalog.py` 读随 runner 一起送到中心机的那份 CLI 文件生成 | `chat_send` |

**Claude Code 的约定**：服务器注册名固定叫 `native`，项目自己的 MCP 服务不许叫这个名字，撞上直接报错；写 `mcp.json` 时用 `--strict-mcp-config --mcp-config <路径>` 钉住这一次的清单。`native` 底下不全是平台工具：`invoke` 是这个 harness 搬运读写与命令的通道（Read / Edit / Bash 都从它过），`project_tools` 是项目 MCP 的入口（见[项目自定义 MCP](/dev/remote-mcp)），`send_user_file` 替内置的 SendUserFile 交文件。

Codex 那条路走 `RemoteTools.discover`：先向执行器问 `native` 和各个项目服务的 `tools/list`，`native` 底下的只留 `NATIVE_TOOLS` 那九个，项目服务的一律写成 `mcp__<服务>__<工具>`，最后把平台表整个并进来；命重当场报错，不猜谁该赢。调用时按路由分两路：平台工具走 `run_platform_tool` 原地打后端，项目工具走执行器的 `invoke`。

**pi 自己没有 MCP 客户端**，平台工具因此不走 MCP，而是作为 extension 工具注册进去（`platform.ts` 的 `registerPlatformTools`）。送到的是它们由来的那个文件：平台的 `cheese`（`backend/sandbox/cheese`），既带平台工具表也带 CLI 的命令树，随 runner 的归档一起送到中心机（`pi/bundle.py`）。`catalog.tools()` = `PLATFORM_TOOLS.schemas()` + `cli_worker._tools(parser)`。一次调用经 socket 回到 runner（`runner.py` 的 `run_cli`）：平台表里的工具在 runner 里原地打后端，要从机器上取的东西（文件内容、推送）才去房间的执行机；其余的是 CLI 命令，在执行机上、在工作区里跑。`catalog.argv()` 把调用翻回命令行并交给 argparse 复核，`SystemExit` 转成 `ValueError`：不管它，`SystemExit` 会从 socket 处理器里走出去，把 runner 的事件循环一起带走。

项目的 MCP 服务器由 runner 充当 pi 的 MCP 客户端（`harness/pi/mcp.py`），走另外两个骨架同一个 `RemoteClient`：检出里 `.mcp.json` 的 stdio 服务器和队友类型声明的 stdio 服务器由执行机上的执行器起，执行器在调用两边跑项目的 PreToolUse / PostToolUse 钩子；远程服务器经后端的 `/topics/{id}/mcp/{name}` 调用，凭据只在后端。它们的工具在开场时列一次，以 `mcp__<服务>__<工具>` 的名字由 `platform.ts` 的 `registerMcpTools` 注册，调用回到 runner 再转给对应的服务器。

## 一个文件两种用法 {#file}

`backend/sandbox/cheese` 是一个文件、两种用法：

- **会话侧**把它当模块读：`PLATFORM_TOOLS` 是工具表，`run_platform_tool(tool, args, host)` 执行其中一样。`host` 提供四样东西——房间的 `CHEESE_*` 环境变量、一次后端请求、读一个文件、以及推一次任务的提交。
- **机器上**它是 `cheese` 这条命令：只剩必须在这台机器上作为进程跑的动作。

`cli_worker.py` 是「CLI 的 argparse 树」这件事本身，和任何骨架无关：`_tools(parser)` 把叶子命令翻成工具 schema（`cheese_<命令>`），`_command(parser, tool, arguments)` 把一次调用翻回 argv，**并让那个 parser 自己复核一遍**（`leaf.parse_args`）——缺必填字段或值不在枚举里，报的是 argparse 自己的话，而不是一条格式不对的命令行。机器上它还带一个预加载进程：一次 `fork` 跑一次调用，省掉每次调用的解释器启动。

## 机器够不着时 {#unreachable}

`executor_transport.py` 里那句话只有一处声明，因为后端 import 的是它、原样发到机器上的也是它：

```text
这台机器现在够不着：文件、命令、项目 MCP 不可用；对话、记忆、平台工具可用。
```

- `MACHINE_OUT_OF_REACH` 是这句话；`MachineOutOfReach` 是它的类型。以前调用方只能比字符串，于是只认得「执行器答了 502/503/504」这一档；机器真的没了时执行器什么也不答——读超时 660 秒到点抛 `TimeoutError`，连接被拒是重试窗口耗尽后抛 `ConnectionRefusedError`，两者的文字都不是那句话。`MachineOutOfReach` 让最该被认出来的那一档认得出来。
- 判「够不着」不能只看非 200：`OUT_OF_REACH_STATUSES` 只有 `{502, 503, 504}`（中间那一跳转不过去，或者执行器没在听），还有一个得连响应头一起看的 409（`_device_is_offline`：`X-Device-Id` 在就是链路断了，不在就是代际冲突，机器好好的）。其余的（500 是机器上某个工具抛了异常、401 是令牌过期、别的 4xx 是执行器比后端旧）手好好的，说成够不着是假话，agent 会照着它放弃整轮的文件与命令操作。
- **答案当场给，不等超时**：接不通只重试到 `CONNECT_RETRY_WINDOW_S = 180` 秒为止；机器上那个 MCP 进程撞上第一次之后，余下的调用在 `RECHECK_AFTER_S = 30` 秒内直接答同一句话，不再一个个去撞 660 秒。
- 平台工具里只有那两样要机器（`cheese_doc_set`、`cheese_accept_request`）吃这个闸；机器答了错但手还在的调用拿到的是 `EXECUTOR_CALL_FAILED`（「机器还在，这一个可以重试」）。

## 时间线上的工具名 {#timeline}

房间里的施工现场按工具名给动作分级，而同一个平台动作有三种拼法（MCP 前缀、pi 的目录名、`chat_send` 这种别名），所以归一在 `chat.py`：

- `_short_tool_name(raw)` 用 `_MCP_PREFIX`（`^mcp__[a-z0-9_]+__`）去前缀，`mcp__native__chat_send` → `chat_send`。**认服务器名而不是写死某一个**：写死一个的话，服务器改名那天这里会静默失效，而失效的样子和时间线正常的样子一模一样。
- `_PLATFORM_PREFIXES` 是「这是平台动作」的三种前缀：`mcp__cheese__`（历史拼法，老行还躺在库里）、`mcp__native__`、`cheese_`。`_PLATFORM_ALIASES` 收名字里没有 `cheese_` 的那几个：`chat_send`、`chat_edit`、`todo_write`、`platform_request`，加 Claude Code 传输自己的 `send_user_file`。
- `_NOT_A_PLATFORM_TOOL` 只有一条：`mcp__native__invoke`。它底下**不都是平台动作**——`invoke` 是搬运读写与命令的通道，把它算成平台动作会在时间线上点一颗琥珀色的点，而那只是读了一个文件。
