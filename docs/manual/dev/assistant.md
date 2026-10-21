---
title: 个人芝士
kind: 流程
summary: 一个人自己的芝士：全站唯一的面板、一条对话怎么存、在哪里跑、能做什么、怎么扣钱，面板怎么接到页面上，它和人一起写题目草稿，按编号读和搜，以及代人执行其他人可见的操作前怎么判断要不要请人确认。
covers:
  - backend/app/domain/agent/personal/
  - backend/app/domain/assistant/
  - backend/app/api/routes/assistant.py
  - backend/app/api/routes/assistant_agent.py
  - backend/app/api/assistant_deps.py
  - backend/app/domain/feature_stats/features/personal_assistant.py
  - backend/app/api/routes/tasks/drafts.py
  - backend/app/domain/task/drafts.py
  - backend/app/domain/agent/session_host/
  - frontend/src/components/assistant/AssistantPanel.vue
  - frontend/src/components/assistant/AssistantTurn.vue
  - frontend/src/components/agent/AgentLayer.vue
  - frontend/src/components/agent/AgentMark.vue
  - frontend/src/composables/useAgentPointer.ts
  - frontend/src/lib/assistantView.ts
  - frontend/src/lib/draftChangeView.ts
  - frontend/src/lib/typist.ts
  - frontend/src/api/agentChanges.ts
  - frontend/src/api/agentProposals.ts
  - frontend/src/components/agent/AgentProposal.vue
  - frontend/src/stores/agentThread.ts
  - frontend/src/stores/agent.ts
  - frontend/src/layouts/AgentHost.vue
  - frontend/src/api/assistantRules.ts
  - frontend/src/views/user/settings/Assistant.vue
  - frontend/src/views/user/settings/AssistantView.vue
  - frontend/src/views/admin/features/PersonalAssistantPage.vue
  - frontend/src/composables/useTaskDraft.ts
  - frontend/src/composables/useDraftAgent.ts
---

# 个人芝士 {#assistant}

项目之外的芝士：一个人和芝士的那一条对话怎么存、在哪里跑、能做什么、怎么扣钱，全站唯一的面板怎么接到页面上，以及它和人一起写题目草稿。方向与规则见 #2285。

> 讲：对话怎么存、会话在哪里跑、工具、一次提问从准入到扣费、题目草稿、编号、代人执行操作前的授权判断。不讲：项目里干活的 AI 队友（见[一轮对话](/dev/turn)），额度本身（见[计费流程](/dev/billing#personal)）。

## 对话 {#conversations}

每人一个芝士、一条对话：在哪一页打开面板，都是这一条。它由一个个话题组成，每个话题是一行 `assistant_conversations`，「新话题」开下一个（`POST /api/assistant/conversations`，等第一句话发出去才建）；`GET /api/assistant/conversations` 列出这个人的全部话题，最近的在前，面板打开时接着最近的那个，更早的往上翻时再读。问的时候眼前是哪一页随问题一起说（见下文「当前页面」），所以话题不挂在页面上：新话题的 `place_kind` 是 `personal`；早先挂在题目或草稿上的话题照旧列出，在里面提问时没说哪一页，就当作在它挂着的那一页问的。草稿还记着写它的那个话题（`task_draft.conversation_id`），发布页用它把同一份文件拆出的几道题排在一起。对话只有本人看得到，别人的对话和不存在的对话答同一个 404，id 不泄露任何东西。

说过的话存在 `assistant_messages`，按顺序只追加，面板显示的就是它。模型那一份对话不在数据库里，在这段对话自己的会话里。


## 面板接到页面上 {#host}

面板全站只有一个，挂在应用外壳里（`layouts/AgentHost.vue`），对话和面板状态在模块里（`stores/agent.ts`、`stores/agentThread.ts`）：外壳在落地页和应用之间会重新挂载，芝士答到一半不跟着丢，换页时照样答完。入口三处：桌面左侧导航栏底部的芝士头像、手机顶栏右侧的芝士头像、`Ctrl/Cmd+J`。项目里也是同一个面板、同一条对话，记这个人自己的额度，见[在项目里](#in-projects)。面板头部只写「芝士」。退出登录时对话、待交的文件一并清掉。

页面不接线，只登记两件事，都是栈（后登记的在上面，组件卸载或被 keep-alive 收起时自己撤下）：

- `useAgentContext`：这一页是什么（`page`，芝士读到的「当前页面」），以及面板在这一页的问候、开场问题、输入框提示、能不能交文件。没登记的页面用通用的说法，不附「当前页面」。
- `useAgentSurface`（可选）：芝士写进这一页时页面怎么动——`tool_args` 边到边填、`change` 定稿、`published` 之后、撤销，以及面板上那一轮怎么画（改动卡能不能撤销、第几道）。题目页登记了 `published`（芝士保存了这道题的修改，页面重读）；发布页和改题页由 `useDraftAgent` 登记整套。
  - 有表单的页面在可写面里再说表单（`form`）：外框在哪、有哪几格（各叫什么、是不是打字写的）、芝士正在写哪格、哪几格标着「芝士已填写」、撤销标记怎么做。指针那一层（`components/agent/AgentLayer.vue`）由宿主画，送进表单的外框里（`Teleport`），页面不接线、不再自己挂 `useAgentPointer`。格子在页面上用 `data-agent-field` 标出来。

面板上那一轮怎么画分两半：不管写的是什么都一样的那一半（面板拿的形状、每一步怎么说）在 `lib/assistantView.ts`；写进题目草稿的改动卡、发布的结果怎么说在 `lib/draftChangeView.ts`。

桌面上面板默认停在右边（把页面挤窄，芝士往表单里写时并排看得见），可以取消固定，变成右下角的浮层；这一项记在浏览器里（`cheesex.agentDocked`）。发布页、改题页在桌面上打开时，停在右边的面板自动打开。手机上从底部升起。
## 会话 {#session}

和房间里的芝士是同一套底座：中心会话机上钉住版本的 pi，由 runner 守着，用同一个启动脚本起（`harness/pi/launch.py`、`host.py`），后端用同一种「等到有新东西才回」的读法读它的日志和正在写的内容（`driven/runner.py`）。区别是没有房间要的那些东西：没有项目、执行机、频道锁、工作租约和房间日志。起会话、发问题、读回答由会话核心做（`agent/session_host/`），和[文档里的芝士](/dev/doc-agent#session)、[房间里的芝士](/dev/harness#contract)共用；这里只决定会话是什么样的（`personal/session.py`）。

- **一段对话一个 pi 会话**，会话 id 就是对话 id，放在会话机的 `~/.cheese/personal/<用户 id>/<对话 id>`。
- **没有手**：不给执行目标，pi 只开平台列给它的工具（`personal/session.py` 的 `TOOLS`，`--tools`），pi 自己的读写文件、跑命令都不开。系统提示词替换掉 pi 默认的那份编程助手说明，只写规则，对每段对话都一样；用户眼前是哪一页随问题说（见[一次提问](#ask)）。
- **能做的事处处一样**：不管在哪一页问，工具都是同一套、同一顺序（`personal/session.py` 的 `TOOLS`，`backend/sandbox/cheese` 的 `DELEGATED_TOOLS`）。页面只决定芝士看到什么，不收窄它能做什么：做不了的事由接口按这个人的权限拒绝，不靠少给工具。见[工具](#credential)和[开头保持不变](#cache)。
- **空闲 3 分钟退出**，对话留在磁盘上；下一个问题把它在同一个对话上重新起来。
- **每人最多同时跑 2 个**：起第 3 个时，关掉这个人最久没用的那个；正在回答的不关，所以同时在更多地方提问时会暂时超过 2 个。
- **每个会话内存上限 512 MB**（runner 和 pi 合计，用户级 systemd scope）。起新会话前看会话机剩余内存，不够就排队等，最多 3 分钟（`HOST_WAIT_S`）：答完的会话空闲几分钟就退出，内存是这样空出来的；3 分钟后还不够就回「芝士当前繁忙，稍后重试」。已经在跑的会话不受影响。
- 推理打开（读一份实验指导、写一道题要它），强度 high（`personal/session.py` 的 `EFFORT`，模型在网关的 `cheese_efforts` 里声明了才传，否则用模型默认）。强度和频道里的 AI 队友走同一个设置 `ModelSettings.effort`，各 harness 自己翻译：pi 用 `--thinking`，Claude Code 用 `CLAUDE_CODE_EFFORT_LEVEL`。单次回答最多 16000 token，推理和写进草稿的整篇题目详情都算在里面。对话长到一次提问超过约 4.8 万 token 时，pi 自己把较早的部分压成摘要，保留最近约 8000 token 原文，发生在触发它的那次提问里。
- 打开一段对话（`GET /api/assistant/conversations/{id}`）时，后台顺手把它的会话起起来，第一个问题就不用等启动。「新话题」第一句话发出去才建，这一步帮不上它。

**在新会话之前就有的对话**：会话第一次被问时，把这段对话之前说过的话（最近约 2.4 万字）连同问题一起交给它一次，接着答。

## 凭据 {#credential}

会话启动时拿的是个人凭据（`cxpu_`，`sandbox_auth.mint_personal_credential`），写着这个人和这段对话，不带任何项目、房间、队友的信息。它只开 `/llm/v1/*`：换上这个人自己的网关虚拟 key 转发给网关。带这张凭据调的模型都记在这个人头上；能不能问，在提问时的准入里判。房间的凭据、项目凭据、平台密钥、人自己的登录都不能代替它；它也打不开任何房间或项目的接口，打不开人自己的 API。

工具用的是每次提问另签的代行凭据（`cxdg_`，`sandbox_auth.mint_delegated_credential`）：写着这个人、这段对话（`conversation`）和这次回答的编号，有效期是回答上限再加一分钟。没有队友、没有频道的代行凭据就是这个人自己的芝士：它能进这个人能进的任何项目，每个接口照样按这个人的权限判断（`resolver._resolve_delegated`）；凭据上的项目只说明是在哪一页问的。代行凭据只在 `api/auth/` 的 `DELEGATED_ROUTES` 列出的接口上有效；其他人可见的操作走的接口列在 `GATED_ROUTES`，要先经过[授权判断](#acting)。人自己的设置接口（芝士的规则、撤销）不在里面，芝士改不了自己的规则。

工具按这个人打交道的东西分，一种东西一个工具；读和搜只各有一个，因为所有东西都按编号用同一种方式读（见[编号](#refs)）：

| 工具 | 做什么 | 接口 |
|---|---|---|
| `cheese_inbox` | 等他处理的事，和「待办」页同一份，每件带读它或答复它的编号 | `POST /assistant/agent/inbox` |
| `cheese_inbox_respond` | 答复待办里要选一项的事：提醒、项目邀请、转让、设备上运行命令 | 经[授权判断](#acting) |
| `cheese_read` | 按编号读一样东西，长的分段 | 项目里的东西 `POST /assistant/agent/read`；题目、草稿、文件、空间资料走 `/task-drafts/agent/*`；`me` 是他参加的题目，`settings` 是他的通知设置 |
| `cheese_search` | 搜他最近的几个项目或其中一个（频道、消息、任务、文档段落、资料库、产物），或一个空间的题目 | `POST /assistant/agent/search`（在 `routes/project_context.py`，复用项目搜索）；空间走 `/task-drafts/agent/search` |
| `cheese_help` | 检索或读平台使用文档 | `/docs/agent/search`、`/docs/agent/read` |
| `cheese_message_send` | 在频道或任务里发消息 | 经授权判断，`POST /topics/{id}/messages` |
| `cheese_task_open` | 在频道里开任务交给 AI 队友，交代作为第一条消息 | 经授权判断，`POST /topics/{id}/tasks` 再发消息 |
| `cheese_review_decide` | 采纳或退回审阅卡 | 经授权判断，`/accept-cards/{id}/accept`、`/reject` |
| `cheese_member_set` | 项目里邀请、改角色、移出；频道里加入、移出 | 经授权判断，成员接口 |
| `cheese_channel_create` | 新建频道 | 经授权判断，`POST /topics` |
| `cheese_settings_set` | 改他自己的通知设置或某个频道的提醒 | 直接改，可撤销：`/notifications/preferences/agent/set`、`/assistant/agent/notify` |
| `cheese_web_fetch` | 读一个网页 | 经授权判断，`POST /fetch` |
| `cheese_problem_write` | 写题目草稿 | `POST /task-drafts/agent/write`：见[题目草稿](#drafts) |
| `cheese_problem_publish` | 发布草稿，和人点「发布」「保存更改」同一条路 | `POST /task-drafts/agent/publish` |

工具的报错原样交给芝士：每条拒绝都写明该怎么改（比如编号该怎么写、分类只能是空间已有的哪几个），芝士照着改了再调。

## 编号 {#refs}

芝士看到、用到的东西都用编号指代，就是这样东西页面地址里 `/projects/` 后面那一段（`domain/assistant/refs.py`）：项目是短名 `ml-course`，频道 `ml-course/channels/3`，任务 `ml-course/tasks/12`，它的审阅卡 `ml-course/tasks/12/review`，文档 `ml-course/docs/5`；项目以外的东西写种类和 id：题目 `spaces/4/tasks/87`、草稿 `drafts/12`、文件 `files/30`、空间资料 `spaces/4/materials/9`、提醒 `alerts/55`、邀请 `invites/<uuid>`。从地址栏复制来的整段地址也认。项目里的号码由 `project/address.py` 换成 UUID；能不能看，由请求自己的身份解析器按打开那一页的同一个判据判断（`api/assistant_deps.py` 的 `ProjectPages`），看不了的答不存在。

读出来的内容都写成文字（`personal/reading.py`）：是什么、在哪、里面有什么，以及继续读的编号。长的分段，每段约 1500 字，末尾写下一段怎么读；消息从最新往前每页 20 条。芝士读过的东西不需要一直留在对话里，要时再读一次。

## 一次提问 {#ask}

`POST /api/assistant/conversations/{id}/ask`，带上问题、问的时候眼前是哪一页（`page`：`{kind: "task" | "draft", id}`，省略时是对话挂着的那样东西）和交给芝士的文件（`files`：先经 `POST /attachments` 传上去的 id 和名字）。以 server-sent events 流式返回：`queued`（在等会话机空出来）、`thinking`（模型开始推理；只说它在推理，推理的内容不发）、`delta`（文字）、`tool`（芝士开始调一个工具，带调用 id）、`tool_args`（这次调用的参数原文，边写边流，带 `at`）、`tool_failed`（这次调用报错了）、`change`（芝士写进了草稿，带这次改动记下的编号，见[题目草稿](#drafts)和[改动与撤销](#changes)）、`published`（芝士发布了一份草稿）、`error`、`done`（`stopped`：被人停下了）。

**眼前的页面随问题说**：页面写成一段「当前页面」（题目页是题目说明、这个人的领取和题目材料；草稿页是整份草稿，带 version、空间 id 和这个空间可选的分类、标签、邮箱域名组、能当导师的人——芝士的工具按空间 id 办事），放在问题前面。前端只传页面的种类和 id，正文由服务端按提问人的权限写出（`personal/pages.py`）：每种页面是 `PAGES` 里的一行，写明怎么写出这一页、在这里提问记作哪种用量；加一种页面就是加一个渲染器和一行，提问的路由不用改。请求里的种类列在 `PageKind`，测试保证它和 `PAGES` 一致；不认识的种类在问模型之前就以参数错误拒绝。

「当前页面」开头是一段「这一页」：这一页是做什么的、「这道题」指什么、只附文件不打字在这里是什么意思（发布页是导入，草稿还空着就先填它；改题页是按文件改这道题；题目页是说明文件和题目的关系）。它由管这一页的模块自己写（`drafts.scene`、`prompt.TASK_SCENE`），所以全局规则里不放任何一页专属的做法，加一种页面就在它的渲染器里加一段「这一页」。只发文件不打字时问题是空的，文件名存在这条消息的 `parts.files` 里，读回对话时照样显示。只在它和这段对话上一次听到的不一样时才说（Valkey 里记着上一次说的摘要，`assistant:page:<对话 id>`，六小时后不认），没变就只送问题。系统提示词因此对每段对话都一样，每次提问都能走缓存。

1. **准入**：对话是本人的；对话所在的题目本人打得开（和题目页同一个判断，`_ensure_task_readable`）；网关上 `ASSISTANT_MODEL` 有单价、这个人有一把只认当前 `ASSISTANT_MODEL` 的网关 key（第一次提问时开；模型换了就先把旧 key 的花费扣完，再换一把新的，旧的在网关上吊销），会话机在线，否则拒绝；个人额度有剩余，否则 429 并写明哪天重置；同一段对话同一时间只答一个问题（Valkey 里的锁，和房间、文档的芝士用同一套 `agent/admission.py`：回答期间续期，后端换了由接着读的那个后端续，没人续时 30 秒内自动释放）。会话在回答里起：等会话机时先推一条 `queued`，等不到或起不来就以 `error` 收尾。
2. **跑**：会话起来以后才签这一问的代行凭据，有效期从那时算，排队的时间不占它。问题连同凭据送进会话，pi 经 `/llm/v1` 调模型，工具带着代行凭据调平台接口。后端读会话：正在写的文字变成 `delta`，正在调的工具变成 `tool`；日志里这一问的最后一条记录是答案。答完后会话若还在压缩对话，等它做完再放开锁。
3. **收尾**：这一问由会话核心读到答完（`session_host/consumptions.py`），不靠发问的那个请求，也不靠哪一个后端进程：读它的后端每几秒续一次租约，部署时先把租约放掉，异常退出时租约 20 秒内过期，别的后端看到就接着读，从上次读到的地方往下。读者中途离开、后端重启，照样答完、存下、扣费。回答的每一步都写进这一问在 Valkey 里的一条流，响应只是在读这条流：第一条 `answering` 带这一问的 id，之后每条都带它在流里的位置（`id`）；连接断了，用 `GET /api/assistant/conversations/{id}/answers/{这一问}?after={位置}` 接着读剩下的。打开一段还在答的对话，`GET /api/assistant/conversations/{id}` 的 `answering` 就是这一问的 id，面板接上去看。`delta` 带 `at`：这段字从回答的第几个字起，接着读时重复听到的字按它放回原位。要它停只有 `POST /api/assistant/conversations/{id}/stop`（只有本人能停）：排队中的不再等，正在答的让会话停下；已经写出的部分存下，`assistant_messages.stopped` 记着它是被停下的，面板在它后面写「已停止」。答案存进 `assistant_messages`，连同这一轮做了什么（`parts`：从这一问的流里读出的每段推理及其用时、每次工具调用及其参数和是否报错、每次写草稿、每次发布），面板重新打开时照样画出来；放开锁，然后读这个人 key 上新增的网关花费，扣个人额度（`billing.py`；在草稿页上问的记作写作 `kind = "assistant_write"`，别处的记作问答 `kind = "assistant"`；缓存命中的部分按网关的缓存价）。网关的花费记录晚到时过几秒再读；更晚到的由下一次提问读到。

## 题目草稿 {#drafts}

写到一半的新题目，或对一道已发布题目还没保存的修改，存在 `task_draft` 里（`domain/task/drafts.py`）：`fields` 是那张表单，形状和 `POST /tasks` 收的一样，外加三个只属于页面的字段（`descriptionMarkdown`、`teachingOwn`、`attachmentFiles`）；`task_id` 不空时，它是那道题的修改草稿，一个人对一道题只有一份。草稿只有本人看得到，别人的答 404。

两只手往里写：

- **人**在发布页、改题页上改，页面停手 600ms 后把改了的那几格存进去（`PATCH /task-drafts/{id}`）。还没有草稿时，第一次改动或第一次问芝士才建（`POST /task-drafts`），地址栏 `?draft=` 记下它。每写一次 `version` 加一，`marks` 记着每一格人最后一次写它时的 `version`。
- **芝士**用 `cheese_problem_write` 写，说的是它自己的字段名（`AGENT_FIELDS`：分类、标签写名字，截止时间写这个人时区的「YYYY-MM-DD HH:MM」，详情写 Markdown，也可以按原文替换几处）。它带着读草稿时的 `version`：人在那之后写过的格子保持人的版本，返回里列在 `kept`。写完记下这次改动（见[改动与撤销](#changes)），往这一问的流里推一条 `change`：改动编号、草稿 id、改了哪几格（各自原来是什么）、`kept`、芝士留给人确认的 `notes`、已撤销的格子。

发布（`POST /task-drafts/{id}/publish`，或芝士的 `cheese_problem_publish`）把草稿当成 `POST /tasks` 或 `PATCH /tasks/{id}` 的请求体，走同一段建题、改题代码（`routes/tasks/_writes.py`），然后删掉草稿、把对话改挂到题目上。页面点「发布」「保存更改」时，先把表单校验过的样子存进草稿，再发布草稿。

## 改动与撤销 {#changes}

芝士答题时往人的东西里写的每一次——题目草稿的几格、文档的几段——都记成一行 `assistant_changes`（`domain/assistant/changes.py`）：记在这次回答的编号（委托凭据里的 `work`）下，写明是谁问的、写的是哪种东西（`kind`：`draft`、`doc`）、哪一份（`target`）、改了什么（`payload`），以及人已经撤销了哪几项（`undone`）。写路由用 `changes.report` 记，草稿那边再把它推到这一问的流里；文档里的选区框在回答结束时按编号读回这一轮的改动。

撤销、恢复是同一对接口，不管写的是什么：`POST /assistant/changes/{id}/undo`、`/redo`，可以带 `part` 只撤一项（草稿是芝士的字段名），不带就是全部。只有问的那个人能撤销，以本人的名义写回，走本人写这样东西时的那道检查。怎么写回由写这种东西的模块登记（`changes.register`，草稿在 `routes/tasks/drafts.py`，文档在 `routes/living_docs.py`，通知设置在 `routes/notification_preferences.py`，频道提醒在 `routes/assistant_agent.py`）：

| 种类 | 一项是 | 撤销 | 恢复 |
|---|---|---|---|
| `draft` | 一格（`AGENT_FIELDS` 的键） | 放回这一轮第一次写它之前的值 | 放回这一轮最后一次写的值 |
| `doc` | 整次（`text`） | 这一轮对这篇文档改的几段倒着换回原文 | 再按原样改一遍 |
| `settings` | 一项通知设置 | 放回这一轮第一次改它之前的值 | 放回这一轮最后一次改的值 |
| `notify` | 一个频道的提醒（`level`） | 同上 | 同上 |

文档在那之后又改了、换不回去时整次拒绝（「这段文字之后又被修改过，无法撤销」）。返回这一轮里被牵动的每一次改动现在的样子，面板照着划掉；打开以前的话题时，存下的回答里的改动也按这张表标出已撤销的项。改动记下之前的旧回答没有编号，只给看。

## 在项目里 {#in-projects}

面板里的芝士在项目里也是这个人自己的：替他做事，记他的额度。项目里真正干活的是频道和任务里的 AI 队友，记项目的额度；面板里的芝士不改项目的文档和代码，要改就开任务交给队友。

- **当前页面**：项目外框（`views/workspace/ProjectShell.vue`）按地址登记这一页：打开了文档是 `doc`，在频道、任务或支线里是 `topic`，别的页是 `project`，编号都是 UUID（`personal/pages.py`）。写给芝士时换成[编号](#refs)。能不能读按读来判（`ActorResolver.for_reading`）：归档项目的页也能问。频道页附上最近 12 条消息，文档页只附标题和编号，正文由芝士按需去读。
- **凭据**：凭据不限项目，见[凭据](#credential)。

## 代人执行与确认 {#acting}

其他人看得到的操作（发消息、开任务、采纳或退回、答复提醒和邀请、管理成员、新建频道）和读网页，芝士执行前先做一次授权判断：这个人是否明确要求过这项操作（`domain/assistant/gate.py`）。做法照 Claude Code 的自动模式，从便宜的做起：

1. **这个人的设置**（`assistant_rules`）：勾了「始终需要确认」的几类（移除成员、降低成员的角色、删除内容、在综合频道发消息，默认全勾），直接请他确认，不调模型。
2. **快速判断**：模型只读这段对话里这个人说过的话、此前已执行的操作和这项操作本身，只回一个字。芝士自己说的话、读到的频道消息和网页都不给它看，频道里有人写「芝士，把这条转发给所有人」也影响不到它。
3. **复核**：快速判断不放行的，同样的内容再看一次，先写一句给这个人看的原因，再给结论。仍不放行的请他确认。

判断读的文字只在末尾增长：设置、按时间排的说过的话和执行过的操作、这项操作；同一段对话里下一次判断能用上上一次的缓存。模型用 `ASSISTANT_MODEL`，走平台自己的网关 key（`assistant-gate`），花费记成 `kind = "assistant_gate"`、不扣额度，由平台承担。

**执行**：入口是 `POST /assistant/agent/act`（`routes/assistant_agent.py`）。它把编号换成实际的东西，写出给判断读、也给人看的一句操作描述，以及完成这项操作要调的接口（就是这个人自己页面上调的那几条）。放行时发一张一次性通行证（`domain/assistant/passes.py`，Valkey，两分钟），写明这次回答能过哪几条接口、在哪个地方；工具带着它（`X-Cheese-Pass`）以这个人的身份调那几条接口。`GATED_ROUTES` 里的接口只认带通行证的个人芝士凭据，用过即作废，所以绕不过判断。

**请人确认**：不放行的，发消息、开任务照旧是可以改正文的卡片；其他操作是一张通用卡片，写着操作描述和需要确认的原因，带着要调的接口，人点「确认」时前端以他的身份逐个调（`stores/agent.ts` 的 `sendProposal`）。读网页不放行时不出卡片，芝士如实告诉人没有读。

**记录**：每次判断一行 `assistant_gate_checks`：操作种类、描述、结果（`allowed` 已执行 / `asked` 请人确认）、在哪一步定下、原因、输入和缓存命中的 token、费用、耗时。这个人在设置的「芝士」页看最近的操作（`GET /assistant/activity`），在同一页改自己的设置（`GET/PUT /assistant/rules`）。管理员在「功能数据」的「个人芝士」页看授权判断的次数、需确认比例、费用、耗时和缓存命中率（`feature_stats/features/personal_assistant.py`）。

**自己的设置**：通知设置和频道提醒只影响这个人自己，芝士直接改，不做授权判断，记成可撤销的改动（见[改动与撤销](#changes)）。

## 开头保持不变 {#cache}

模型服务按请求开头逐字缓存，命中的部分按缓存价计。所以每次提问发给模型的开头对所有人、所有页面、整段对话都一样，对话只往后加：

- 系统提示词只有规则，对每个人都一样；这个人是谁、时区在对话开头说一次（`prompt.about`），现在的时间附在每个问题末尾（`prompt.now_line`）。
- 工具表固定，同一组、同一顺序，不随页面增减。
- 当前页面变了才附，放在问题前面。
- 工具结果在写出时就定好长度，之后不改；读过的东西要时再读。只有 pi 在对话快满时压成摘要这一次改写。
- 每次回答在 `assistant_messages.parts.usage.calls` 里记下每次模型调用的输入 token 和其中缓存命中的部分，「个人芝士」数据页按它算回答的缓存命中率。`test_each_model_call_begins_with_the_whole_of_the_one_before` 守着这一条：会话退出重起之后，开头也逐字不变。

## 面板 {#panel}

题目页「问芝士」、发布页、改题页用的是同一个 `AssistantPanel`，输入框和工作台的是同一套零件（`ComposerEditor`、`ComposerChipRow`、`ComposerActions`），文件能点「+」选，也能拖进面板；芝士每一轮做的事由 `AssistantTurn` 画：调的工具一行一步（动词 + 一个参数，答完收成一行「N 步」），写进草稿的改动是一张卡（每格能撤销），`notes` 是「待你确认」，发布的结果带「查看题目」。题目页的抽屉打开时才挂；发布页、改题页上它一直停在右边，窄屏是底部弹层。面板只拿 props、发事件，取数和流式读取在 `useAssistant`。

发布页、改题页上，表单跟着芝士动（`useTaskDraft`、`useDraftAgent`）：

- `tool_args` 流进来时，页面把参数读成表单的几格先填上（`lib/taskDraftAgent.ts` 的 `previewOf`，只填能确定的值），这几格这时不算人改的；`change` 到了，用服务端的值覆盖预览，记成已存；`tool_failed` 就收回预览。只改详情里几处（`description_edits`）时，预览在现在的详情上把已经写出来的那几处换掉。
- 参数常常一大块一起到，`lib/typist.ts` 按格子定节奏（哪些参数长、哪些只用来定位由工具说，`cheese_problem_write` 的是 `DRAFT_WRITE_PACE`）：边读那段 JSON 边认出在写哪个字段，一格的字每秒 60 个，字多就加快，短格子最多 1 秒、详情和评分标准最多 2 秒写完；换到下一格前停 0.22 秒，让指针先过去；定位用的字段（草稿 id、要替换的原文）一下子放完。调用结束、`change`、`published` 都排在放完之后。
- 要整格重写、原来有字时，停的那一下把原来的字选中；改详情里的一段时，先选中原文，再跟着新文字走（`useAgentPointer` 在编辑器里找那段字，`AgentLayer` 画选中）。
- 页面跟着芝士滚：换到一格时平滑滚进视野；字写到看不见的地方时立刻滚过去，先滚那一格自己（详情超过最大高度后在框里滚），再滚页面。人刚自己滚过就不跟。
- 表单上一层是 `AgentLayer`：整张表单一个指针。写打字的格子（名称、详情）时是带「芝士」名字的文字光标，停在字的末尾（输入框量字宽，编辑器读 TipTap 挂在 `.ProseMirror` 上的编辑器）；写选项、数字、日期、列表时是箭头。正在写的那一格描着边，写过的格子标「芝士已填写」，人改过这一格后标记消失。位置由 `useAgentPointer` 量 `data-agent-field` 标着的那几格：芝士在答时每一帧都量，不在答时格子变了大小也量。描边只在换格子时滑过去，光标不带过渡，免得落在字后面。「芝士已填写 撤销」那一小行是 `AgentMark`，以后别的页面上芝士改过的地方也用它。
- 撤销一格走 `POST /assistant/changes/{id}/undo`：服务端放回芝士这一轮第一次改它之前的值，当作人改的存下，表单照着填回（`useTaskDraft.restored`），不再存一遍。对话里眼前这份草稿一轮只有一张改动卡，同一格写了几次只留最后一次。

一份文件拆出几道题时，芝士在同一段对话里建的几份新题目草稿排在表单上方（`TaskDraftStrip`）：点了切换，对话还是那一段，不重读；芝士正在写的那份转圈，还没建好的先占一格。「全部发布」先存下眼前这份，再一份份发布（`POST /task-drafts/{id}/publish`）：草稿列表的 `missing`（后端 `drafts.missing`，名称、详情、参与方式、难度）不空的那份不发，标出缺几项，切过去时表单标出缺的格子。

芝士的「当前页面」里列着空间的题目模板。`cheese_problem_write` 给 `template` 时，先按模板填好名称、详情和几项设置（和页面上「使用模板」一样），再写同一次给出的其他字段；返回里带着套好的详情骨架，芝士在它的小标题下面填。
