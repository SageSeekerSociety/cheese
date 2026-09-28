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

芝士在房间里做的事——发消息、开任务、递卡、写文档、记决策——都是调用平台工具。这一页讲这张表长什么样、它怎么变成三种骨架各自手里的工具，以及机器够不着时会发生什么。

> 讲：平台工具表的来源、三种骨架各自的暴露方式、机器够不着时的答复。不讲：项目自己声明的 MCP 服务，见[项目自定义 MCP](/dev/remote-mcp)；一轮的整体流程，见[一条消息怎么变成芝士的一轮](/dev/turn)。

## 一张表，32 样 {#table}

`backend/sandbox/cheese` 里的 `PLATFORM_TOOLS` 是一个 `ToolTable` 常量，**会话侧平台工具的唯一来源**（结论 21，由结论 63 修订）。今天表上有 32 样：

| 类别 | 工具 |
| --- | --- |
| 对话 | `chat_send`、`chat_edit`、`todo_write` |
| 读房间 | `cheese_chat_list`、`cheese_chat_search`、`cheese_chat_get`、`cheese_chat_replies` |
| 文档 | `cheese_doc_get`、`cheese_doc_set` |
| 任务与验收 | `cheese_task`、`cheese_close_task`、`cheese_accept_request`、`cheese_describe`、`cheese_ready`、`cheese_tell` |
| 记录 | `cheese_milestone`、`cheese_decision`、`cheese_title` |
| 通知与拍板 | `cheese_notify`、`cheese_ask` |
| 资料 | `cheese_fetch`、`cheese_docs_search`、`cheese_docs_read`、`cheese_library_ls` |
| 锁 | `cheese_lock`、`cheese_unlock` |
| 房间状态 | `cheese_members`、`cheese_status` |
| 机器与调度 | `cheese_machine`、`cheese_note`、`cheese_deliver_at` |
| 反馈 | `cheese_feedback_propose` |

这张表以前是**问出来的**：会话侧的 MCP 服务器收到 `tools/list` 就去执行器要一份，执行器再把机器上那棵 argparse 树翻成工具。于是一台执行机够不着，整个 `cheese_*` 家族就从清单里消失，agent 被告知「没有这个工具」——而它这一刻最需要的恰恰是跟房间说一句这里出事了。表变成常量之后它不再问任何人：这些要的是平台，不是那台机器，所以从会话直接打后端，机器离线时一样不少。

只有两样还要机器上的一份东西：`cheese_doc_set` 要读机器上那个文件，`cheese_accept_request` 先让机器把任务的提交推上去。要作为进程在机器上跑的（任务目录、同步与恢复、预览、文件转换、git 凭据）不在表里，是这个文件的 CLI 子命令。

## 三种骨架，三种拼法 {#harnesses}

同一件事在三种骨架里名字不同，因为把工具交给模型的机制各不相同：

| 骨架 | 怎么拿到工具 | 名字长什么样 |
| --- | --- | --- |
| Claude Code | 执行器的 `native` MCP 服务器：`tools/list` 的最后一批就是 `PLATFORM_TOOLS.schemas()`；容器里的 `proxy.js` 给它们加 `mcp__native__` 前缀 | `mcp__native__chat_send` |
| Codex | 后端把表当 `dynamicTools` 发过去（`harness/codex/tools.py` 的 `platform_tools()`、`RemoteTools.discover`），调用按 `PLATFORM` 这条路由原地执行 | `chat_send` |
| pi | 没有 MCP：`harness/pi/catalog.py` 当场读装在这台机器上的那个 CLI 文件，生成目录 | `chat_send` |

**Claude Code 的约定**：服务器注册名固定叫 `native`，项目自己的 MCP 服务不许叫这个名字，撞上直接报错；写 `mcp.json` 时用 `--strict-mcp-config --mcp-config <路径>` 钉住这一次的清单。`native` 底下不全是平台工具：`invoke` 是这个 harness 搬运读写与命令的通道（Read / Edit / Bash 都从它过），`project_tools` 是项目 MCP 的入口（见[项目自定义 MCP](/dev/remote-mcp)）。

Codex 那条路走 `RemoteTools.discover`：先向执行器问 `native` 和各个项目服务的 `tools/list`，`native` 底下的只留 `NATIVE_TOOLS` 那九个，项目服务的一律写成 `mcp__<服务>__<工具>`，最后把平台表整个并进来；命重当场报错，不猜谁该赢。调用时按路由分两路：平台工具走 `run_platform_tool` 原地打后端，项目工具走执行器的 `invoke`。

**pi 没有 MCP**，所以它的平台工具不可能像另外两个那样「送到」。能送到的是它们由来的那个文件：平台装在机器上的 `cheese` 一条命令，既带平台工具表也带 CLI 的命令树。`catalog.tools(source)` = `PLATFORM_TOOLS.schemas()` + `cli_worker._tools(parser)`，在**这台机器上**生成——后端发过更新的 CLI，下一次启动就是更新的目录。`catalog.argv()` 再把一次工具调用翻回命令行并交给 argparse 复核，`SystemExit` 转成 `ValueError`：不管它，`SystemExit` 会从 socket 处理器里走出去，把 runner 的事件循环一起带走。

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
- `_PLATFORM_PREFIXES` 是「这是平台动作」的三种前缀：`mcp__cheese__`（历史拼法，老行还躺在库里）、`mcp__native__`、`cheese_`。`_PLATFORM_ALIASES` 收名字里没有 `cheese_` 的那几个：`chat_send`、`chat_edit`、`todo_write`，加平台自己的 `platform_request`、`send_user_file`。
- `_NOT_A_PLATFORM_TOOL` 只有一条：`mcp__native__invoke`。它底下**不都是平台动作**——`invoke` 是搬运读写与命令的通道，把它算成平台动作会在时间线上点一颗琥珀色的点，而那只是读了一个文件。
