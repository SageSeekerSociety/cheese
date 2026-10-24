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

芝士在房间里做的事——发消息、创建任务、递卡、写文档——都是调用平台工具。这一页讲这张表长什么样、它怎么变成三种骨架各自手里的工具，以及机器够不着时会发生什么。

> 讲：平台工具表的来源、三种骨架各自的暴露方式、机器够不着时的答复。不讲：项目自己的 MCP 服务器，见[项目的 MCP 服务器](/dev/remote-mcp)；一轮的整体流程，见[一条消息怎么变成芝士的一轮](/dev/turn)。

## 一张表，20 样 {#table}

`backend/sandbox/cheese` 里的 `PLATFORM_TOOLS` 是一个 `ToolTable` 常量，**会话侧平台工具的唯一来源**（结论 21，由结论 63 修订）。今天表上有 20 样，一个对象一样，括号里是它的动词（见[一个对象一样工具](#objects)）：

| 类别 | 工具 |
| --- | --- |
| 对话 | `chat_send`、`todo_write`、`cheese_ask`、`cheese_chat`（list / search / get / replies / edit） |
| 文档 | `cheese_doc`（get / set / edit / new / list）、`cheese_app`（save / get）、`cheese_canvas`（get / edit） |
| 任务与验收 | `cheese_task`（open / state / close / reopen）、`cheese_card`（request / describe / redeliver / ready） |
| 房间 | `cheese_room`（members / rename）、`cheese_notify` |
| 会话 | `cheese_session`（status / environment / wait / compact / note / deliver_at；compact 要部署打开 `CHEESE_COMPACT_TOOL` 才在清单上） |
| 资料 | `cheese_web`（fetch / search）、`cheese_help`、`cheese_library`（list） |
| 例行事务 | `cheese_routine`（create / list / update / pause / report） |
| 技能 | `cheese_skill`（search / read / propose / update） |
| 反馈 | `cheese_feedback`（propose / list / get / claim / release） |
| 运行记录 | `cheese_run_records`（只在平台自己的项目里读得到，见[运行记录](/dev/run-records#retention)） |
| 其余的平台接口 | `platform_request` |

这张表以前是**问出来的**：会话侧的 MCP 服务器收到 `tools/list` 就去执行器要一份，执行器再把机器上那棵 argparse 树翻成工具。于是一台执行机够不着，整个 `cheese_*` 家族就从清单里消失，agent 被告知「没有这个工具」——而它这一刻最需要的恰恰是跟房间说一句这里出事了。表变成常量之后它不再问任何人：这些要的是平台，不是那台机器，所以从会话直接打后端，机器离线时一样不少。

常用的动作各有一样专门的工具，其余的只有 `platform_request` 一样，原因和用法见下面[其余的平台接口](#rest)。

只有 `cheese_card(action=request)` 还要机器：它先让机器把任务的提交推上去。文档类工具直接收正文，不碰机器，所以任务开始前、改动留不下时也能写实况文档。要作为进程在机器上跑的（任务目录、同步与恢复、预览、文件转换、git 凭据）不在表里，是这个文件的 CLI 子命令。

## 一个对象一样工具 {#objects}

一个对象有几个动词时，它是表上的一行（`_object`），`action` 选做哪一件，每个动词（`_verb`）有自己的说明和参数。schema 是扁平的：`action` 枚举排第一、也是唯一的必填项，后面是各动词参数的并集，每个参数的说明写着哪几件收它、哪几件必填。不用 `oneOf` / `anyOf`：同一份 schema 要过三种骨架的工具机制和它们背后的 Anthropic 兼容网关。

一个动词收什么，在调用时查（`run_platform_tool` 里的 `_checked`），发出任何请求之前：

- 没给 `action`，或者给的不是表上的：`cheese_task 要 action：open / state / close / reopen`。
- 缺这个动词的必填参数：`cheese_task(action=state) 缺少参数：state`。
- 带了这个动词不收的参数，直接拒，并说出哪件收它：`cheese_task(action=close) 不收 subject：subject 是 cheese_card(action=request)、cheese_card(action=describe) 的参数。`，后面列出这个动词收的参数。只给了空值的参数等于没给，不算。

调用按键分派：`<工具>.<action>`（`cheese_task.close`），只做一件事的工具就是它的名字。`request_plan`、`_STEPS`、`_SAY` 都按这个键查。

同一个名字在两张表上可以是两样东西：房间的 `cheese_task` 有 open / state / close / reopen，个人芝士的 `cheese_task` 只有 open，而且以用户的名义、经过授权判断去开；`cheese_card` 在房间里是递卡和维护卡，在个人芝士手里是以审阅人身份 decide。是哪一样看凭证：个人芝士和文档芝士的调用都带代行凭证，pi runner 调 `run_platform_tool(..., delegated=True)`，先查 `DELEGATED_TOOLS`；`catalog.schemas_of` 给这些会话列的也是代行那一样。`chat_send` 也是这样：房间里发在这里，个人芝士手里的那一样以用户的名义发到 `where`，经过授权判断。

一个会话也可以只拿一个对象的几个动词：会话的名单里写键（文档芝士写 `cheese_doc.get`、`cheese_doc.edit`、`cheese_doc.set`），`schemas_of` 给它的 `cheese_doc` 就只有这几个动词，runner 也只放行这几个（`pi/runner.py` 的 `_platform_tool`）。工具的说明是对象的名字加上每个动词一行，由 `ToolTable.narrowed` 按留下的动词重新写，所以没给的动词在说明里一个字也不出现：没开压缩开关的房间读不到「压缩上下文」，文档芝士的 `cheese_memory` 只说读项目记忆。调了没给的动词，回的是这个会话有的那几个（`cheese_memory 要 action：project`），和没写 `action` 时一样（`ask_for_action`）；房间里没开开关时调 compact，三种骨架都经 `run_platform_tool` 拿到同一句，后面跟着它有的那几个动词。

### 退役的名字 {#retired}

会话记录里的调用永远是当时的名字和写法，所以读记录的地方（现场那一行的动词和参数预览）都按键读：`app/domain/session_record/tool_keys.py` 的 `tool_key(name, args)`，前端 `toolLabels.ts` 的 `toolKey`。`RETIRED` 记着并进某个对象、成了它一个动词的旧名字各自变成了哪个键（还在表上、后来才收 `action` 的名字也在，给它收 action 以前的调用）；直接删掉、什么也没变成的工具不在这里，它的旧记录用自己的标签。前端那份副本由 `tests/unit/test_tool_labels.py` 对齐。这张表描述的是库里已有的记录，所以一直留着。

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

**Claude Code 的约定**：服务器注册名固定叫 `native`，项目自己的 MCP 服务不许叫这个名字，撞上直接报错；写 `mcp.json` 时用 `--strict-mcp-config --mcp-config <路径>` 钉住这一次的清单。`native` 底下不全是平台工具：`invoke` 是这个 harness 搬运读写与命令的通道（Read / Edit / Bash 都从它过），`send_user_file` 替内置的 SendUserFile 交文件。项目自己的服务器不在 `native` 底下：它们是 Claude Code 自己的 HTTP 服务器，连在会话的网关上（见[项目的 MCP 服务器](/dev/remote-mcp#gateway)）。

Codex 那条路走 `RemoteTools.discover`：向执行器问 `native` 的 `tools/list`，只留 `NATIVE_TOOLS` 那九个，再把平台表整个并进来；命重当场报错，不猜谁该赢。调用时按路由分两路：平台工具走 `run_platform_tool` 原地打后端，执行器自己的工具走 `invoke`。项目的 MCP 服务器不在这张表里，由 Codex 自己的 MCP 客户端连会话的网关。

平台工具在 pi 里作为 extension 工具注册进去（`platform.ts` 的 `registerPlatformTools`）。送到的是它们由来的那个文件：平台的 `cheese`（`backend/sandbox/cheese`），随 runner 的归档一起送到中心机（`pi/bundle.py`）。`catalog.tools()` 就是 `offered_tools()`，和另外两种骨架拿到的是同一份表。一次调用经 socket 回到 runner（`runner.py` 的 `run_cli`）：平台表里的工具在 runner 里原地打后端，要从机器上取的东西（文件内容、推送）才去房间的执行机。CLI 自己的命令（`cheese worktree`、`cheese sync`……）不是工具：pi 房间和另外两种骨架一样，用机器上的 `bash` 跑 `cheese …`，靠 `cheese --help` 发现。把调用名当成工具名调（`cheese_worktree`）会拿到「没有这个工具」。

项目的 MCP 服务器由 pi 自己的 MCP 客户端（`builtin:mcp`）连会话的网关，扩展只负责把它们一个个注册给 pi（`registerMcpServers`）；pi 开着 codemode，模型可以写一段脚本一次调好几个工具，脚本调过哪些工具记在那一步上（`meta.calls`）。见[项目的 MCP 服务器](/dev/remote-mcp#gateway)。

## 一个文件两种用法 {#file}

`backend/sandbox/cheese` 是一个文件、两种用法：

- **会话侧**把它当模块读：`PLATFORM_TOOLS` 是工具表，`run_platform_tool(tool, args, host)` 执行其中一样。`host` 提供四样东西——房间的 `CHEESE_*` 环境变量、一次后端请求、读一个文件、以及推一次任务的提交。
- **机器上**它是 `cheese` 这条命令：只剩必须在这台机器上作为进程跑的动作。

CLI 的命令在哪种骨架里都不是工具：会话在 bash 里跑 `cheese …`，靠 `cheese --help` 发现。`cli_worker.py` 是机器上的预加载进程：Claude Code 的执行器先把 CLI 加载好，一次 `fork` 跑一次 `cheese …`，省掉每次调用的解释器启动。

## 机器够不着时 {#unreachable}

`executor_transport.py` 里那句话只有一处声明，因为后端 import 的是它、原样发到机器上的也是它：

```text
这台机器现在够不着：文件、命令、项目 MCP 不可用；对话、记忆、平台工具可用。
```

- `MACHINE_OUT_OF_REACH` 是这句话；`MachineOutOfReach` 是它的类型。以前调用方只能比字符串，于是只认得「执行器答了 502/503/504」这一档；机器真的没了时执行器什么也不答——读超时 660 秒到点抛 `TimeoutError`，连接被拒是重试窗口耗尽后抛 `ConnectionRefusedError`，两者的文字都不是那句话。`MachineOutOfReach` 让最该被认出来的那一档认得出来。
- 判「够不着」不能只看非 200：`OUT_OF_REACH_STATUSES` 只有 `{502, 503, 504}`（中间那一跳转不过去，或者执行器没在听），还有一个得连响应头一起看的 409（`_device_is_offline`：`X-Device-Id` 在就是链路断了，不在就是代际冲突，机器好好的）。其余的（500 是机器上某个工具抛了异常、401 是令牌过期、别的 4xx 是执行器比后端旧）手好好的，说成够不着是假话，agent 会照着它放弃整轮的文件与命令操作。
- **答案当场给，不等超时**：接不通只重试到 `CONNECT_RETRY_WINDOW_S = 180` 秒为止；机器上那个 MCP 进程撞上第一次之后，余下的调用在 `RECHECK_AFTER_S = 30` 秒内直接答同一句话，不再一个个去撞 660 秒。
- 平台工具里只有 `cheese_card(action=request)` 要机器，吃这个闸；机器答了错但手还在的调用拿到的是 `EXECUTOR_CALL_FAILED`（「机器还在，这一个可以重试」）。

## 时间线上的工具名 {#timeline}

房间里的施工现场按工具名给动作分级，而同一个平台动作有三种拼法（MCP 前缀、pi 的目录名、`chat_send` 这种别名），所以归一在 `chat.py`：

- `_short_tool_name(raw)` 用 `_MCP_PREFIX`（`^mcp__[a-z0-9_]+__`）去前缀，`mcp__native__chat_send` → `chat_send`。**认服务器名而不是写死某一个**：写死一个的话，服务器改名那天这里会静默失效，而失效的样子和时间线正常的样子一模一样。
- `_PLATFORM_PREFIXES` 是「这是平台动作」的三种前缀：`mcp__cheese__`（历史拼法，老行还躺在库里）、`mcp__native__`、`cheese_`。`_PLATFORM_ALIASES` 收名字里没有 `cheese_` 的那几个：`chat_send`、`cheese_chat(action=edit)`、`todo_write`、`platform_request`，加 Claude Code 传输自己的 `send_user_file`。
- `_NOT_A_PLATFORM_TOOL` 只有一条：`mcp__native__invoke`。它底下**不都是平台动作**——`invoke` 是搬运读写与命令的通道，把它算成平台动作会在时间线上点一颗琥珀色的点，而那只是读了一个文件。
