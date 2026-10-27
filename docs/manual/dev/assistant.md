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
  - backend/app/domain/routine/personal.py
  - frontend/src/components/common/Notification/renders/RenderAssistantNotification.vue
  - frontend/src/composables/useTaskDraft.ts
  - frontend/src/composables/useDraftAgent.ts
---

# 个人芝士 {#assistant}

项目之外的芝士：一个人和芝士的那一条对话怎么存、在哪里跑、能做什么、怎么扣钱，全站唯一的面板怎么接到页面上，以及它和人一起写题目草稿。方向与规则见 #2285；其中规则 1 不许代做验收采纳那一句已被推翻，见[代人执行与确认](#acting)。

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

和房间里的芝士是同一套底座：中心会话机上钉住版本的 pi，由 runner 守着，用同一个启动脚本起（`harness/pi/launch.py`、`host.py`），后端用同一种「等到有新东西才回」的读法读它的日志和正在写的内容（`driven/runner.py`）。区别是没有房间要的那些东西：没有项目、频道锁和房间日志。起会话、发问题、读回答由会话核心做（`agent/session_host/`），和[文档里的芝士](/dev/doc-agent#session)、[房间里的芝士](/dev/harness#contract)共用；这里只决定会话是什么样的（`personal/session.py`）。

- **一段对话一个 pi 会话**，会话 id 就是对话 id，放在会话机的 `~/.cheese/personal/<用户 id>/<对话 id>`。
- **一台自己的电脑**：部署配了会话机（`agent_session_device_id`）、能开云沙箱时，pi 除了平台列给它的工具（`personal/session.py` 的 `TOOLS`）还开自己的读写文件、跑命令（`read`、`write`、`edit`、`ls`、`find`、`grep`、`bash`），落在这段对话自己的标准沙箱里：能上网，第一次用时才开，闲置回收，按时长扣这个人的个人额度。规则里多出「你的电脑」一节，说明这些。文件放在这个人自己的资料库里（他名下那个团队的资料库，见[团队的资料库](#library)），芝士用 `cheese_library(action=take)` 取进电脑、用 `cheese_library(action=give)` 交出来。沙箱怎么归属、怎么要手见[沙箱归谁](/dev/execution#personal-sandbox)。没有会话机或云沙箱的部署不开这些工具，规则里也不说。
- **电脑上的文件只在电脑上**：芝士还不能把电脑上的文件交给人，也不能把人附上的文件放上电脑；结果写进回答。
- 系统提示词替换掉 pi 默认的那份编程助手说明，只写规则，对每段对话都一样；用户眼前是哪一页随问题说（见[一次提问](#ask)）。
- **能做的事处处一样**：不管在哪一页问，工具都是同一套、同一顺序（`personal/session.py` 的 `TOOLS`，`backend/sandbox/cheese` 的 `DELEGATED_TOOLS`）。页面只决定芝士看到什么，不收窄它能做什么：做不了的事由接口按这个人的权限拒绝，不靠少给工具。见[工具](#credential)和[开头保持不变](#cache)。
- **空闲 3 分钟退出**，对话留在磁盘上；下一个问题把它在同一个对话上重新起来。
- **每人最多同时跑 2 个**：起第 3 个时，关掉这个人最久没用的那个；正在回答的不关，所以同时在更多地方提问时会暂时超过 2 个。
- **每个会话内存上限 512 MB**（runner 和 pi 合计，用户级 systemd scope）。起新会话前看会话机剩余内存，不够就排队等，最多 3 分钟（`HOST_WAIT_S`）：答完的会话空闲几分钟就退出，内存是这样空出来的；3 分钟后还不够就回「芝士当前繁忙，稍后重试」。已经在跑的会话不受影响。
- 推理打开（读一份实验指导、写一道题要它），强度 high（`personal/session.py` 的 `EFFORT`，模型在网关的 `cheese_efforts` 里声明了才传，否则用模型默认）。强度和频道里的 AI 队友走同一个设置 `ModelSettings.effort`，各 harness 自己翻译：pi 用 `--thinking`，Claude Code 用 `CLAUDE_CODE_EFFORT_LEVEL`。单次回答最多 16000 token，推理和写进草稿的整篇题目详情都算在里面。对话长到一次提问超过约 4.8 万 token 时，pi 自己把较早的部分压成摘要，保留最近约 8000 token 原文，发生在触发它的那次提问里。
- 打开一段对话（`GET /api/assistant/conversations/{id}`）时，后台顺手把它的会话起起来，第一个问题就不用等启动。「新话题」第一句话发出去才建，这一步帮不上它。

**在新会话之前就有的对话**：会话第一次被问时，把这段对话之前说过的话（最近约 2.4 万字）连同问题一起交给它一次，接着答。

## 凭据 {#credential}

会话启动时拿的是个人凭据（`personal+jwt`，`sandbox_auth.mint_personal_credential`），写着这个人和这段对话，不带任何项目、房间、队友的信息。它只开 `/llm/v1/*`（换上这个人自己的网关虚拟 key 转发给网关）和这段对话自己要手的那一条路由（见[个人芝士对话的沙箱](/dev/execution#personal-sandbox)）。带这张凭据调的模型都记在这个人头上；能不能问，在提问时的准入里判。房间的凭据、项目凭据、平台密钥、人自己的登录都不能代替它；它也打不开任何房间或项目的接口，打不开人自己的 API。

工具用的是每次提问另签的代行凭据（`delegated+jwt`，`sandbox_auth.mint_delegated_credential`）：写着这个人、这段对话（`conversation`）和这次回答的编号，有效期一小时（`ACTING_S`）。回答还没完时，读它的后端每 40 分钟另签一张交给会话（`Answers._keep_going` → 会话机的 `act_as` → runner 的 `acting`：只换工具用的凭据，对会话什么也不说），所以一张泄露的凭据最多用一小时，不是整个回答的四小时。接着读的后端不知道上一张是什么时候签的，按最早可能的时刻算，宁早不晚。没有队友、没有频道的代行凭据就是这个人自己的芝士：它能进这个人能进的任何项目，每个接口照样按这个人的权限判断（`resolver._resolve_delegated`）；凭据上的项目只说明是在哪一页问的。代行凭据只在 `api/auth/` 的 `DELEGATED_ROUTES` 列出的接口上有效；其他人可见的操作走的接口列在 `GATED_ROUTES`，要先经过[授权判断](#acting)。人自己的设置接口（芝士的规则、撤销）不在里面，芝士改不了自己的规则。

工具按这个人打交道的东西分，一种东西一个工具；读和搜只各有一个，因为所有东西都按编号用同一种方式读（见[编号](#refs)）：

| 工具 | 做什么 | 接口 |
|---|---|---|
| `cheese_inbox` | 等他处理的事，和「待办」页同一份，每件带读它或答复它的编号 | `POST /assistant/agent/inbox` |
| `cheese_inbox(action=respond)` | 答复待办里要选一项的事：提醒、项目邀请、转让、设备上运行命令 | 经[授权判断](#acting) |
| `cheese_read` | 按编号读一样东西，长的分段 | 项目里的东西 `POST /assistant/agent/read`；题目、草稿、文件、空间资料走 `/task-drafts/agent/*`；他的资料库 `library`、`library/<名字>` 走 `POST /assistant/agent/library`；`me` 是他参加的题目，`settings` 是他的通知设置；`teams`、`teams/<id>`、`spaces`、`spaces/<id>/people` 是他的团队、空间和成员（`personal/rosters.py`），`spaces/<id>/manage` 是他管理的空间的设置、分类、邀请码、公告和待审核的题目（`personal/space_desk.py`），`<项目短名>/settings` 是项目设置（`personal/project_desk.py`） |
| `cheese_find` | 搜他最近的几个项目或其中一个（频道、消息、任务、文档段落、资料库、产物），或一个空间的题目 | `POST /assistant/agent/search`（在 `routes/project_context.py`，复用项目搜索）；空间走 `/task-drafts/agent/search` |
| `cheese_help` | 检索或读平台使用文档 | `/docs/agent/search`、`/docs/agent/read` |
| `chat_send` | 在频道或任务里发消息 | 经授权判断，`POST /topics/{id}/messages` |
| `cheese_task(action=open)` | 在频道里开任务交给 AI 队友，交代作为第一条消息 | 经授权判断，`POST /topics/{id}/tasks` 再发消息 |
| `cheese_card(action=decide)` | 采纳或退回一项待审阅的改动 | 经授权判断，`/accept-cards/{id}/accept`、`/reject` |
| `cheese_people` | 项目里邀请、改角色、移出；频道里加入、移出；团队里邀请、改角色、移出；空间里加入、移出、设或撤管理员 | 经授权判断，成员接口 |
| `cheese_room(action=create)` | 新建频道 | 经授权判断，`POST /topics` |
| `cheese_room(action=set)` | 项目短名、任务命名、默认 AI 队友、默认模型；频道负责人 | 经授权判断，项目设置的接口 |
| `cheese_space` | 空间的名称、简介、可见题目上限；公告；分类；邀请码；审核待审核的题目 | 经授权判断，空间设置、公告、邀请码和 `PATCH /tasks/{id}` |
| `cheese_settings` | 改他自己的通知设置或某个频道的提醒 | 直接改，可撤销：`/notifications/preferences/agent/set`、`/assistant/agent/notify` |
| `cheese_web(action=fetch)` | 读一个网页 | 经授权判断，`POST /fetch` |
| `cheese_web(action=search)` | 搜网页，和房间里的 agent 同一个工具 | 直接 `GET /web-search`，不经授权判断：搜索词只发给 DeepSeek，它是模型的提供方，本来就收着整段对话；在他自己的网关 key 上搜，花费和这次提问的模型调用一起扣他的额度 |
| `cheese_problem(action=write)` | 写题目草稿 | `POST /task-drafts/agent/write`：见[题目草稿](#drafts) |
| `cheese_problem(action=publish)` | 发布草稿，和人点「发布」「保存更改」同一条路 | `POST /task-drafts/agent/publish` |
| `cheese_library(action=give)` | 把电脑上的一份文件交给他：存进他的资料库，回答里出现文件卡片 | `POST /assistant/agent/files/slot` 领地址，电脑自己 `curl -T` 传上去，再 `POST /assistant/agent/files/handed` 登记；流里发 `handed` |
| `cheese_library(action=take)` | 把他资料库里的一份文件放进电脑 | `POST /assistant/agent/files/link` 领地址，电脑自己 `curl -o` 取下来 |
| `cheese_todo` | 一件长的事的步骤清单，每次写整份 | `POST /assistant/agent/todo` 只核对、数回去；面板从这次调用的参数直接画，见[长的事](#long-work) |
| `cheese_subagent_start` / `cheese_subagent_send` / `cheese_subagent_stop` | 派出分身、给分身发消息（做完的就接着做）、停下分身 | 不走平台接口：扩展自己在会话进程里开分身，见[长的事](#long-work) |

工具的报错原样交给芝士：每条拒绝都写明该怎么改（比如编号该怎么写、分类只能是空间已有的哪几个），芝士照着改了再调。

## 编号 {#refs}

芝士看到、用到的东西都用编号指代，就是这样东西页面地址里 `/projects/` 后面那一段（`domain/assistant/refs.py`）：项目是短名 `ml-course`，频道 `ml-course/channels/3`，任务 `ml-course/tasks/12`，它的审阅卡 `ml-course/tasks/12/review`，文档 `ml-course/docs/5`；项目以外的东西写种类和 id：题目 `spaces/4/tasks/87`、草稿 `drafts/12`、文件 `files/30`、空间资料 `spaces/4/materials/9`、提醒 `alerts/55`、邀请 `invites/<uuid>`。从地址栏复制来的整段地址也认。项目里的号码由 `project/address.py` 换成 UUID；能不能看，由请求自己的身份解析器按打开那一页的同一个判据判断（`api/assistant_deps.py` 的 `ProjectPages`），看不了的答不存在。

读出来的内容都写成文字（`personal/reading.py`）：是什么、在哪、里面有什么，以及继续读的编号。长的分段，每段约 1500 字，末尾写下一段怎么读；消息从最新往前每页 20 条。芝士读过的东西不需要一直留在对话里，要时再读一次。

## 一次提问 {#ask}

`POST /api/assistant/conversations/{id}/ask`，带上问题、问的时候眼前是哪一页（`page`：`{kind: "task" | "draft", id}`，省略时是对话挂着的那样东西）和交给芝士的文件（`files`：先传进他自己的资料库的 `path` 和名字，芝士按 `library/<path>` 读；发布、改题目页上交的是题目材料，仍是经 `POST /attachments` 传上去的 `id`，按 `files/<id>` 读）。以 server-sent events 流式返回：`queued`（在等会话机空出来）、`thinking`（模型开始推理；只说它在推理，推理的内容不发）、`delta`（文字）、`tool`（芝士开始调一个工具，带调用 id）、`tool_args`（这次调用的参数原文，边写边流，带 `at`）、`tool_failed`（这次调用报错了）、`change`（芝士写进了草稿，带这次改动记下的编号，见[题目草稿](#drafts)和[改动与撤销](#changes)）、`published`（芝士发布了一份草稿）、`handed`（芝士交给他一份文件：团队 id、资料库里的名字和大小，回答里画成文件卡片）、`tool_done`（这次调用回来了，没报错）、`subagents`（这一轮派出的分身现在怎样：做什么、做到哪了、最近做的几步）、`error`、`done`（`stopped`：被人停下了）。`tool` 和 `thinking` 带 `at`：它之前回答已经写了多少字，面板把它排在这些字后面；存下的步骤也带着它。

**眼前的页面随问题说**：页面写成一段「当前页面」（题目页是题目说明、这个人的申请和题目材料；草稿页是整份草稿，带 version、空间 id 和这个空间可选的分类、标签、邮箱域名组、能当导师的人——芝士的工具按空间 id 办事），放在问题前面。前端只传页面的种类和 id，正文由服务端按提问人的权限写出（`personal/pages.py`）：每种页面是 `PAGES` 里的一行，写明怎么写出这一页、在这里提问记作哪种用量；加一种页面就是加一个渲染器和一行，提问的路由不用改。请求里的种类列在 `PageKind`，测试保证它和 `PAGES` 一致；不认识的种类在问模型之前就以参数错误拒绝。

「当前页面」开头是一段「这一页」：这一页是做什么的、「这道题」指什么、只附文件不打字在这里是什么意思（发布页是导入，草稿还空着就先填它；改题页是按文件改这道题；题目页是说明文件和题目的关系）。它由管这一页的模块自己写（`drafts.scene`、`prompt.TASK_SCENE`），所以全局规则里不放任何一页专属的做法，加一种页面就在它的渲染器里加一段「这一页」。只发文件不打字时问题是空的，文件名存在这条消息的 `parts.files` 里，读回对话时照样显示。只在它和这段对话上一次听到的不一样时才说（Valkey 里记着上一次说的摘要，`assistant:page:<对话 id>`，六小时后不认），没变就只送问题。系统提示词因此对每段对话都一样，每次提问都能走缓存。

1. **准入**：对话是本人的；对话所在的题目本人打得开（和题目页同一个判断，`_ensure_task_readable`）；网关上 `ASSISTANT_MODEL` 有单价、这个人有一把只认当前 `ASSISTANT_MODEL` 的网关 key（第一次提问时开；模型换了就先把旧 key 的花费扣完，再换一把新的，旧的在网关上吊销），会话机在线，否则拒绝；个人额度有剩余，否则 429 并写明哪天重置；同一段对话同一时间只答一个问题（Valkey 里的锁，和房间、文档的芝士用同一套 `agent/admission.py`：回答期间续期，后端换了由接着读的那个后端续，没人续时 30 秒内自动释放）。会话在回答里起：等会话机时先推一条 `queued`，等不到或起不来就以 `error` 收尾。
2. **跑**：会话起来以后才签这一问的代行凭据，有效期从那时算，排队的时间不占它。问题连同凭据送进会话，pi 经 `/llm/v1` 调模型，工具带着代行凭据调平台接口。后端读会话：正在写的文字变成 `delta`，正在调的工具变成 `tool`；日志里这一问的最后一条记录是答案。答完后会话若还在压缩对话，等它做完再放开锁。
3. **收尾**：这一问由会话核心读到答完（`session_host/consumptions.py`），不靠发问的那个请求，也不靠哪一个后端进程：读它的后端每几秒续一次租约，部署时先把租约放掉，异常退出时租约 20 秒内过期，别的后端看到就接着读，从上次读到的地方往下。读者中途离开、后端重启，照样答完、存下、扣费。回答的每一步都写进这一问在 Valkey 里的一条流，响应只是在读这条流：第一条 `answering` 带这一问的 id，之后每条都带它在流里的位置（`id`）；连接断了，用 `GET /api/assistant/conversations/{id}/answers/{这一问}?after={位置}` 接着读剩下的。打开一段还在答的对话，`GET /api/assistant/conversations/{id}` 的 `answering` 就是这一问的 id，面板接上去看。`delta` 带 `at`：这段字从回答的第几个字起，接着读时重复听到的字按它放回原位。要它停只有 `POST /api/assistant/conversations/{id}/stop`（只有本人能停）：排队中的不再等，正在答的让会话停下；已经写出的部分存下，`assistant_messages.stopped` 记着它是被停下的，面板在它后面写「已停止」。正在答时人又说了一句，走 `POST /api/assistant/conversations/{id}/say`：这句话作为他的话存进对话，再插进正在答的这一轮（`steer`），芝士在下一步之前读到；没有在答时拒绝（409），面板改为提问。会话只在读这一问的那个后端进程手里，接这个请求的未必是它（部署时这一问从旧进程交到新进程，中间几秒到几分钟谁也不读），所以这句话先跟着这一问存在 Valkey 里（`Consumptions.say`），读这一问的进程当场或下次续租时说给会话，每句带固定的编号，runner 收过的不会再收。这一问答完时还没说出去的，回答流最后发一条 `unheard` 原样交回，面板把它们合成一个问题接着问（`kept: true`：这句话已经在对话里，不再记一遍）；人按了停止的不接着问。芝士写完一段话后不当场收尾：会话可能还在做（等分身、压缩对话），读到 runner 说这一轮结束、或会话停下且一会儿没有新东西时才收尾（`session_host/answer.py` 的 `follow`）。做了事却一个字没写的回答记 `parts.silent`，面板写「芝士没有给出回答」。答案存进 `assistant_messages`，连同这一轮做了什么（`parts`：从这一问的流里读出的每段推理及其用时、每次工具调用及其参数和是否报错、每次写草稿、每次发布），面板重新打开时照样画出来；放开锁，然后读这个人 key 上新增的网关花费，扣个人额度（`billing.py`；在草稿页上问的记作写作 `kind = "assistant_write"`，别处的记作问答 `kind = "assistant"`；缓存命中的部分按网关的缓存价）。网关的花费记录晚到时过几秒再读；更晚到的由下一次提问读到。

## 长的事 {#long-work}

一次回答最长四小时（`ANSWER_S`），够读几十份材料再汇总；到点前五分钟平台插一句（`WRAP_UP`），让芝士写下做到哪里、结果在哪、还剩什么，到点停下时已写出的留着，面板写「运行时间达到 4 小时上限，已停止」。

- **清单**：`cheese_todo` 每次写整份，最多 30 项，每项待做、进行中（同时只一项）或已完成。规则 2 和工具说明都写着：步骤多、要做几分钟以上才列，一问一答不列。
- **分身**：扩展在会话进程里开一个 agent 会话（`platform.ts` 的 `registerBranches`；另起一个 pi 要约 130MB，会话总共 512MB），它有自己的上下文，只拿读、搜、上网查和电脑上的工具（`BRANCH_TOOLS`），以这个人的名义做的事只有芝士自己能做：授权判断要看这个人说过的话，分身听不到。照 Claude Code 的分身：`cheese_subagent_start` 派出后立刻返回编号，在后台做，同时最多四个，每个最多调 200 次工具，分身不能再派分身；和 Claude Code 一样，分身最后写的那段话就是结论；做到一半拿不准、要芝士决定的事，分身用自己那一份 `cheese_subagent_send`（没有 `to`，只发给芝士）问，问完接着做。分身的结论和消息都作为平台的话交给芝士（引用起来，写明是资料、不是指令）：芝士正在做，就在它下一步之前插进去（pi 的 steer）；芝士要收尾而还有分身在做，就在 `agent_before_settle` 等，等到分身发来消息或做完再让它接着做；人在这时说话，也停下等先听人的（runner 的 `said_since`）；人点停止，所有分身一起停。扩展把每个分身的状态报给 runner（`branches`），runner 只把这一轮动过的分身放进正在写的内容里（`Writing.branches`），回答流发成 `subagents`。
- **要不要分身**：规则 2 照 Claude Code 自己的说法写：知道编号的一两样东西直接读；材料多、只要结论，或几件互不相关的事要同时做，才按份派分身，几个在同一条消息里一起派。分身留不留看评测：`run.py --no-subagents` 关掉它再跑一遍，分数不比有它时差，就不该有它（`session.BRANCHES`）。
- **做完了告诉人**：自己问的问题答了两分钟以上（`TELL_AFTER_S`），答完时没有面板正给他看这段对话，发一条芝士通知「芝士已完成「话题名」」，有待确认的操作时写出几项。面板开着、页面在前台、芝士在答时，每 15 秒说一次正看着（`POST /assistant/conversations/{id}/looking`，记 30 秒）。自动化的运行照旧发它自己的通知。
- **别处的面板跟着读**：对话里加了一条消息，就叫醒这个人的收件箱连接；连接醒来或到心跳时比一下他最新的那条芝士消息，变了就发 `{"kind": "assistant", "conversation": id}`（部署时另一个后端写的，最多晚一个心跳）。页面把它转给其他标签页；面板重读话题列表，正开着这段对话又没在答，就把最近的一页重读一遍。

## 题目草稿 {#drafts}

写到一半的新题目，或对一道已发布题目还没保存的修改，存在 `task_draft` 里（`domain/task/drafts.py`）：`fields` 是那张表单，形状和 `POST /tasks` 收的一样，外加三个只属于页面的字段（`descriptionMarkdown`、`teachingOwn`、`attachmentFiles`）；`task_id` 不空时，它是那道题的修改草稿，一个人对一道题只有一份。草稿只有本人看得到，别人的答 404。

两只手往里写：

- **人**在发布页、改题页上改，页面停手 600ms 后把改了的那几格存进去（`PATCH /task-drafts/{id}`）。还没有草稿时，第一次改动或第一次问芝士才建（`POST /task-drafts`），地址栏 `?draft=` 记下它。每写一次 `version` 加一，`marks` 记着每一格人最后一次写它时的 `version`。
- **芝士**用 `cheese_problem(action=write)` 写，说的是它自己的字段名（`AGENT_FIELDS`：分类、标签写名字，截止时间写这个人时区的「YYYY-MM-DD HH:MM」，详情写 Markdown，也可以按原文替换几处）。它带着读草稿时的 `version`：人在那之后写过的格子保持人的版本，返回里列在 `kept`。写完记下这次改动（见[改动与撤销](#changes)），往这一问的流里推一条 `change`：改动编号、草稿 id、改了哪几格（各自原来是什么）、`kept`、芝士留给人确认的 `notes`、已撤销的格子。

发布（`POST /task-drafts/{id}/publish`，或芝士的 `cheese_problem(action=publish)`）把草稿当成 `POST /tasks` 或 `PATCH /tasks/{id}` 的请求体，走同一段建题、改题代码（`routes/tasks/_writes.py`），然后删掉草稿、把对话改挂到题目上。页面点「发布」「保存更改」时，先把表单校验过的样子存进草稿，再发布草稿。

## 改动与撤销 {#changes}

芝士答题时往人的东西里写的每一次——题目草稿的几格、文档的几段——都记成一行 `assistant_changes`（`domain/assistant/changes.py`）：记在这次回答的编号（委托凭据里的 `work`）下，写明是谁问的、写的是哪种东西（`kind`：`draft`、`doc`）、哪一份（`target`）、改了什么（`payload`），以及人已经撤销了哪几项（`undone`）。写路由用 `changes.report` 记，草稿那边再把它推到这一问的流里；文档里的选区框在回答结束时按编号读回这一轮的改动。

撤销、恢复是同一对接口，不管写的是什么：`POST /assistant/changes/{id}/undo`、`/redo`，可以带 `part` 只撤一项（草稿是芝士的字段名），不带就是全部。只有问的那个人能撤销，以本人的名义写回，走本人写这样东西时的那道检查。怎么写回由写这种东西的模块登记（`changes.register`，草稿在 `routes/tasks/drafts.py`，文档在 `routes/living_docs.py`，通知设置在 `routes/notification_preferences.py`，频道提醒在 `routes/assistant_agent.py`）：

| 种类 | 一项是 | 撤销 | 恢复 |
|---|---|---|---|
| `draft` | 一格（`AGENT_FIELDS` 的键） | 放回这一轮第一次写它之前的值 | 放回这一轮最后一次写的值 |
| `doc` | 整次（`text`） | 这一轮对这篇文档改的几段倒着换回原文 | 再按原样改一遍 |
| `settings` | 一项通知设置 | 放回这一轮第一次改它之前的值 | 放回这一轮最后一次改的值 |
| `notify` | 一个频道的提醒（`level`） | 同上 | 同上 |
| `memory` | 一条记忆（`memory`） | 放回这一轮第一次动它之前的全文，原来没有就删掉 | 放回这一轮最后一次写的全文，最后删了就删掉 |

文档在那之后又改了、换不回去时整次拒绝（「这段文字之后又被修改过，无法撤销」）。返回这一轮里被牵动的每一次改动现在的样子，面板照着划掉；打开以前的话题时，存下的回答里的改动也按这张表标出已撤销的项。改动记下之前的旧回答没有编号，只给看。

## 在项目里 {#in-projects}

面板里的芝士在项目里也是这个人自己的：替他做事，记他的额度。项目里真正干活的是频道和任务里的 AI 队友，记项目的额度；面板里的芝士不改项目的文档和代码，要改就开任务交给队友。

- **当前页面**：项目外框（`views/workspace/ProjectShell.vue`）按地址登记这一页：打开了文档是 `doc`，在频道、任务或支线里是 `topic`，别的页是 `project`，编号都是 UUID（`personal/pages.py`）。写给芝士时换成[编号](#refs)。能不能读按读来判（`ActorResolver.for_reading`）：归档项目的页也能问。频道页附上最近 12 条消息，文档页只附标题和编号，正文由芝士按需去读。
- **团队的页**：团队文档页登记 `doc`，团队资料库页登记 `library`（编号是团队 id）。芝士没有读团队文档、别的团队资料库的工具，所以团队文档的正文开头（6000 字）和资料库最新 30 份的名字直接写进「当前页面」；本人的资料库写明用 `cheese_read library/<名字>` 读。能不能读按团队成员判（`assistant_deps.ProjectPages.team`）。

**这一句带上什么**（`personal/citing.py`）：面板输入框里和附件同一行（`AssistantContextRow` 用频道输入框的 `ComposerChipRow`）先是这一页和选中的字两枚标签。

- 人点掉这一页（×，划掉后 + 带回来），或这一页没登记，请求带 `no_page: true`：不附页面，也不退回到话题挂着的那一页，芝士读到一段「这一问没有附上页面」（`pages.NO_PAGE`）。关掉只管这一页，换页后前端又带上（`stores/agent.ts` 的 `agentPageOff`）。
- 在页面上选中一段字点「问芝士」（`AgentSelectionAsk.vue`），这段字作为 `selection`（最多 2000 字）随下一句交出。这是唯一由浏览器交上来的正文：它和人自己贴进来的字一样，写给芝士时标明是资料、不是指令。输入框、可编辑的区域和项目文档里不出这个按钮（`data-agent-select="off"`，项目文档有自己的选区框）；团队文档的编辑器标着 `on`，照常出。
- 输入框里打 `@` 挑一样东西（`useCitePicker`），正文里写下 `<#id>`：频道、任务、支线、文档是它自己的 UUID，资料库文件是 `file-<id>`，题目是 `problem-<id>`，一句最多 5 样（后端在校验里拒）。候选来自 `GET /api/assistant/citable?project=`：这个人参加的项目里他打得开的频道和这些频道的任务、这些项目和他所在团队的文档、眼前项目和他自己资料库的文件；题目打字时用 `/tasks/search` 搜。匹配和「最近去过」用 Ctrl+K 的同一套（`commands/palette/match.ts`、`recents.ts`）。
- 服务端按 `<#id>` 逐样判断能不能打开，打不开的当作没引用；问题里的引用换成名字，后面附一段「用户引用的」写明每样是什么、用哪个编号读，团队文档和资料库文件附上正文。问题照原样存，`parts` 里记 `page`（在哪一页问的）、`selection`、`cited`（每样的 id、种类、名字），面板读回对话时照它画标签。
- **凭据**：凭据不限项目，见[凭据](#credential)。

## 代人执行与确认 {#acting}

其他人看得到的操作（发消息、开任务、采纳或退回、答复提醒和邀请、管理项目、团队和空间的成员、新建频道、改项目和空间的设置、发公告、审核题目）和读网页，芝士执行前先做一次授权判断：这是不是这个人想办的事（`domain/assistant/gate.py`）。做法照 Claude Code 的自动模式：查得多严看出错的代价，不是每件事都请人确认。每项操作在拼出来的地方标明代价（`gate.Action`），判断从便宜的做起：

1. **总要确认**（`Action.always_ask`）：允许在这个人的设备上运行命令。说过什么都不算数。
2. **这个人的设置**（`assistant_rules`）：「需要确认」里勾上的几类（移除成员、降低成员的角色、删除内容、发给所有人的消息（综合频道里的消息和空间公告），默认全勾），直接请他确认，不调模型。
3. **低风险**（`Action.low`）：这个人自己能撤回、其间也不伤到谁的，不调模型直接执行：新建频道、把人加进公开频道（他自己也能加入）、拒绝运行命令。读文档网站上的网页也在这一步放行：Claude Code 的自动模式不问就读的那份文档网站名单（Python、MDN、React、各云厂商文档等，原样照搬，见 `domain/fetch/preapproved.py`），芝士也不问，不比 Claude Code 更严；名单外的网页照旧交给模型看。这个人自己添加了「需要确认」的条目时，仍交给模型看，因为只有模型读得懂这些话。
4. **快速判断**：模型只读这段对话里这个人说过的话、此前已执行的操作和这项操作本身，只回一个字。属于第 2 步那几类、但这个人取消了勾选的操作，范围大或难挽回，跳过这一步，直接复核。芝士自己说的话、读到的频道消息和网页都不给它看，频道里有人写「芝士，把这条转发给所有人」也影响不到它。它问的是这项操作是否在办这个人要办的事，不是他有没有逐字说过：措辞由芝士补全、他认可了芝士给的草稿（「就按你说的回他」），都直接执行。请人确认的是：他没要办这件事（提问、假设、先起草给他看，或者是芝士自己的主意、频道或文档里的内容要求的），换了对象、给了更高的角色或多做了，消息替他做了没做过的决定或承诺，他没有对这一件做决定的采纳、退回、接受邀请和设管理员，以及把工作内容带出平台。
5. **复核**：快速判断不放行的，同样的内容再看一次，先写一句给这个人看的原因，再给结论。

调模型出错（超时、网关报错）时重试一次，仍出错才当作不放行。

判断读的文字只在末尾增长：设置、按时间排的说过的话和执行过的操作、这项操作；同一段对话里下一次判断能用上上一次的缓存。模型用 `ASSISTANT_MODEL`，走平台自己的网关 key（`assistant-gate`），花费记成 `kind = "assistant_gate"`、不扣额度，由平台承担。

**执行**：入口是 `POST /assistant/agent/act`（`routes/assistant_agent.py`）。它把编号换成实际的东西，写出给判断读、也给人看的一句操作描述，以及完成这项操作要调的接口（就是这个人自己页面上调的那几条）。放行时发一张一次性通行证（`domain/assistant/passes.py`，Valkey，两分钟），写明这次回答能过哪几条接口、在哪个地方（项目、频道，或接口路径里的编号：团队、空间、题目、邀请；同一张通行证拿到别的团队或空间上用不了）；通行证本身也是一张凭据（`pass+jwt`，带着代行凭据的内容），工具调那几条接口时用它代替代行凭据，以这个人的身份调。`GATED_ROUTES` 里的接口只认通行证，用过即作废，所以绕不过判断。

**留痕**：这些接口上的写入署这个人的名，同时记下是他的芝士做的（`Actor.by_assistant`，代行凭据解析出的身份）：消息块的 `meta.by_assistant`，任务的 `created_by_assistant`，审阅卡的 `decided_by_assistant`（采纳、退回时写；别人对这张卡做决定时清掉），邀请的 `invited_by_assistant`。房间里消息的署名旁标「芝士代发」；任务页、审阅卡、邀请和宣布这些的平台那行字写「芝士代 某人」（`identity.actor.acted_by`，句子 `actedByAssistant`）；读房间的 agent 读到的说话人行是「某人（由本人的个人芝士代发）」（`block.authorship.speaker`）。人在确认卡片上点「确认」发出去的，是他自己发的，不标。踢人、改角色、拉人进出频道、建频道、答复邀请这几件事平台本来就不留宣布的那行字，所以也没有地方可标。见 `docs/agent-principles.md` 一的例外。

**它可以替主人审阅**：授权判断放行后，芝士可以替主人采纳或退回他名下的审阅卡，这个决定标成「芝士代 X 已采纳」「芝士代 X 退回了改动」。这一条是有意推翻 #2285 规则 1 里「验收采纳即使授权也不能代做」的，是产品负责人的决定（2026-10-24），不要当成漏洞去收紧。

**先退回给芝士**：照 Claude Code 自动模式把拒绝交还 agent 的做法，模型判断不放行的操作，一次回答里前 3 次不出卡片，原样退回给芝士，附上原因和接下来怎么办（`gate.RETURNS_PER_ANSWER`，判断记录的 `verdict = returned`）：和用户说的有出入就改了再执行，要用户决定或补充的在回复里问，只差用户同意的带 `confirm=true` 再调一次。退回的不出现在设置页的「最近的操作」里，但仍留在判断记录中，之后的判断读到「芝士想执行，未执行」。规则和总要确认的那几类、带 `confirm` 的、第 4 次起的，以及没有人在场的自动化运行，直接请人确认。

**请人确认**：发消息、开任务照旧是可以改正文的卡片；其他操作是一张通用卡片，写着操作描述和需要确认的原因，带着要调的接口，人点「确认」时前端以他的身份逐个调（`stores/agent.ts` 的 `sendProposal`）。读网页不放行时不出卡片，芝士如实告诉人没有读。

**等人作答**：出了卡片，工具就在那里等（`POST /assistant/agent/wait`，一次最多 50 秒，工具接着再问），人点「确认」或「不用了」，结果当场交还给芝士，它照结果接着做。一张卡最多等 10 分钟；人离开了没作答，芝士说卡片还在面板中，这次回答结束，人之后作答的结果附在下一次提问的末尾告诉它（`proposals.untold`），每张卡只说一次。所以一次回答没有时长上限（`ANSWER_S` 只是事故兜底），人随时可以停止。

**记录**：每次判断一行 `assistant_gate_checks`：操作种类、描述、结果（`allowed` 已执行 / `asked` 请人确认）、在哪一步定下、原因、输入和缓存命中的 token、费用、耗时。这个人在设置的「芝士」页看最近的操作（`GET /assistant/activity`），在同一页改自己的设置（`GET/PUT /assistant/rules`）。

**设置是两张清单**：「需要确认」（勾选的几类，加上自己添加的条目）和「无需确认」（自己添加的条目），每条一句，可以单独添加和删除。条目一行一条存在 `assistant_rules.ask_text` / `allow_text` 里，判断时逐条列给模型。

**自己的设置**：通知设置和频道提醒只影响这个人自己，芝士直接改，不做授权判断，记成可撤销的改动（见[改动与撤销](#changes)）。

## 自己做事 {#on-its-own}

这个人让芝士定时做一件事、在某件事发生时做一件事，或过一会儿提醒他，芝士用 `cheese_routine` 建一条自动化（`assistant_routines`，`domain/routine/personal.py`）。只影响他自己，直接建，不做授权判断。分两种：只做一次的（`spec.freq = once` 或 `spec.once`）和反复做的，每种每人最多 20 条。描述（`describe`）里反复的以「每天」「每次」等开头，只做一次的直接写时间或事情。

- **原话**：建的时候，平台把这段对话里这个人说过的话存进规则（`words`）。运行时授权判断只把这些话当作他说的（`gate._said_and_done` 在最前面加一行「用户（建这条自动化时说）」）。规则的名称和说明是芝士写的，不读；从另一段对话里改规则，那段对话里他说的话追加进去。运行中的芝士不能建或改自动化。建好或改过时，回答里放一张卡片（`rule` 事件，随回答存进 `parts.rules`）：什么时候、做什么；状态按编号从 `GET /assistant/routines` 读（`useAgentRules`），等待中的可取消、开着的可暂停、暂停的可恢复。
- **触发**：定时（`routine.schedule` 的每小时、每天、每周、每月，或某个时刻一次，按他的时区），或发生了一件事：有人 @ 他或回复他参与的支线（`mention`，可以只看某几个人 `from`、某个频道或任务 `place`）、有任务提交审阅且审阅人有他（`review`）、某个任务或他提的任务（`task` / `mine`）提交审阅（`filed`）或被采纳、退回（`decided`，`outcome` 可以只等一种）、空间题目快到截止（`due`）、收到邀请（`invite`）；都可以只看一个项目。
- **事件从哪来**：事情发生的地方，在同一个事务里写一行 `happenings`（`domain/happenings`，C5 最底层，不认识芝士和规则）：投递账本给人记下一条通知时（`delivery/ledger.record_mailbox`，`notified`），审阅递卡时（`review/room_notices._announce_filed`，`task_filed`），卡被采纳或退回时（`_settle_the_card`，`task_decided`）。提交后经 Valkey 列表 `cheese:happenings:wake` 叫醒周期任务；周期任务一次取走最多 500 行、读完即删，逐条对照所有启用中的事件规则，命中一条记一行 `assistant_runs`（`occurrence = <规则>:h:<事件>`，`(user_id, occurrence)` 唯一）。没有规则在等的事件照样删掉。一条反复的事件规则一小时最多运行 6 次，超出的记为跳过。
- **运行**：周期任务 `assistant routines` 被事件叫醒时只处理事件和开始运行；每 30 秒的那一拍做全部（过期的卡、长时间未登录、额度恢复、到点的定时规则、等太久的规则），也兜住丢了的叫醒。叫醒不占那一拍的租约，所以只在持有所有权锁的进程上跑（`PeriodicRunner` 的 `woken_by`）。每人同时只跑一个，同一条规则上一次还没跑完，这一次跳过。只做一次的规则在建它的那段对话里运行，命中第一件事就是 `done`，之后的不再触发；14 天（`WAITS_FOR`）没等到就是 `done` 并通知他，通知点开是那段对话。反复的每次开一段新对话（`place_kind = routine`）。第一条消息由平台写（`role = platform`，授权判断、对话标题都不读它；面板上只显示「自动化「X」开始运行」；只做一次的等到任务审阅时说发生了什么（`assistant_runs.shown`，如「任务「X」提交审阅了」）），触发它的那件事写成资料。运行和人提问走同一条路（`personal.service.admit` / `ask`），记这个人的额度，没有人看着也能答完。
- **不等确认**：运行中要请人确认的操作照样出卡片，但工具不等（`agent_wait` 看到这次回答是一次运行就立刻返回），芝士接着做别的或结束。这些卡片保留 3 天；同一条规则下一次运行开始时，上一次没处理的也作废，状态为 `expired`，不能再确认。
- **结束**：运行结束时按这次回答的 token 和网关价格算出花费，记下留给他确认的卡片数，再给他发一条通知（`ASSISTANT_NOTICE`）：已完成、有几项待他确认、或未能完成，只做一次的说法和他提问后答完的一样（「芝士已完成「X」」）；芝士在运行中已经用 `cheese_notify` 通知过他的，不再发。通知点开在待办页打开那段对话（`/inbox?assistant=<对话>`，`AgentHost` 认这个参数）。只做一次的规则跑完即为 `done`：不能暂停或恢复，不占名额，3 天后连同运行记录自动删除；芝士给它改一个新时间，它重新生效。
- **暂停**：额度用完时这个人的自动化全部暂停并通知他一次，有额度后自动恢复（每 10 分钟看一次）；14 天没有登录（`user_sessions.last_used_at`）也全部暂停并通知他，回来后在设置页恢复。恢复后从现在起运行，暂停期间错过的不补。
- **设置页**：「芝士」页的「自动化」是一个列表：什么时候、做什么、现在怎样。等待中的只做一次的排在前面，可以查看建它的对话、取消；反复的有下次运行、最近一次的结果、近 7 天花了多少点，可以查看对话、暂停、恢复、删除（`GET /assistant/routines` 等）。只做一次的做完后不再列出。通知设置里「芝士发来的通知」一行管这些通知走哪几个渠道，默认全开。
- **`cheese_notify`**：芝士给这个人发一条通知，点开是这段对话，一小时最多 10 条。

## 记忆 {#memory}

个人芝士记得的关于这个人的事，存在平台统一的记忆文件里（`memory_files`，见[记忆](/dev/memory)），用第三个作用域 `personal`：挂在人身上，不在任何项目里，`project_id` 为空，唯一约束单独建（`uq_memory_files_personal_path`）。一条记忆和项目里的一样：名字、一句描述、类型、正文；`MEMORY.md` 一行一条（`domain/assistant/memory.py`）。

- **谁写**：芝士在对话里用 `cheese_memory`（`/assistant/agent/memory`）记下、改写（同名覆盖）、读、删除。每次记下或删除记成一条 `memory` 改动，面板里显示「记忆」卡片，可以撤销（见[改动与撤销](#changes)）。自动化运行时不写（没人在场，读到的都是别人的话），这个人关掉记忆时也不写。
- **怎么用**：每段新对话开头把索引放进「关于这个人」（`prompt.about`），不进系统提示，缓存不受影响；正文要用时再读。对话中途记下的，这段对话里芝士当场就知道，索引从下一段起更新。
- **记什么**：系统提示第 9 条：以后用得上、平台上又读不到的事；用户说「记住」「以后都」一定记；平台上读得到的、只跟这一次有关的、频道和网页里别人的话不记；健康、宗教、政治这类用户要求才记；密码、证件号、银行卡号要求也不记。「以后某类操作直接做」不记，告诉用户去加一条「无需确认」：授权判断不读记忆。
- **不进项目**：项目里的芝士读不到它，频道里的对话也写不进它（见[记忆的作用域](/dev/memory#scopes)）。芝士代人在频道里发消息时把记忆里的事写进去，内容超出了这个人说的话，授权判断会退回。
- **设置页**：「芝士」页的「记忆」一节：开关（`assistant_rules.memory`，关掉时保留但不用不写）、逐条改描述和正文、删除、全部清除（`GET/PUT/DELETE /assistant/memory`、`PUT/DELETE /assistant/memory/{name}`）。这些接口芝士的凭据进不来。
- **上限**沿用记忆文件的：正文 1000 字，索引一行 150 字，索引超过 200 行注入时截断。
- **整理**：每小时看一遍，记了 8 条以上、上次整理后又改过、离上次整理满一天、开着记忆的人，各整理一次（`domain/assistant/memory_tidy.py`，周期任务 `assistant memory tidy`）。个人芝士的会话里没有记忆文件树，项目那套整理（一整轮会话改文件）接不上，而个人记忆量小，所以只调一次模型：把全部记忆交给它，它答要改什么：`merge`（同一件事合成一条）、`delete`（被另一条取代的旧条，必须写明取代它的那条，那条必须留下）、`rewrite`（改写得清楚）。平台逐条校验，有一条不对就整轮不改；删掉一半以上且超过 3 条也整轮不改（和项目整理共用 `tree.BULK_DELETE_*`）；和芝士同时写撞上了也整轮放弃，下次再来。只读记忆，不读对话（对话里有频道消息和网页）。花费记这个人的额度（`kind = "assistant_memory_tidy"`），额度用完就跳过。整理不能撤销，和项目的一样。开始整理时就把 `assistant_rules.memory_tidied_at` 推到现在，它既是下一次的起点，也是锁。

## 资料库 {#library}

一个人的文件放在他自己的资料库里，也就是他名下那个单人团队的资料库（`library_files.team_id`，见 `domain/library/shelf.py`）。资料库可以属于一个项目，也可以属于一个团队，一行只属于其中一个，读写都以书架（`Shelf`）为单位。

- **谁放进来**：他在面板里附的文件（发布、改题目页上的除外，那是题目材料，仍走附件表），和芝士用 `cheese_library(action=give)` 交出来的文件。他也能在团队的「资料库」页直接上传。
- **芝士怎么用**：`cheese_read library` 列出全部，`library/<名字>` 读文字；`cheese_library(action=take)` 把一份放进电脑。芝士的凭据只进 `/assistant/agent/library`、`/files/slot`、`/files/part`、`/files/handed`、`/files/link` 这五条（`api.auth.DELEGATED_ROUTES`），团队资料库的页面接口（`/teams/{id}/library`）它进不来。
- **上限**：每个团队的资料库共 `records.capacity()`：设了 `team_library_bytes` 就是它；没设时按新文件存在哪，本地盘 1 GB，文件桶和 `files_bucket_file_max_bytes` 相同（20 GB，空的资料库放得下一份最大的文件）。被替换下来的旧版本也算；单个文件最大：存在服务器本地盘上时和附件相同（100 MB），比项目资料库的 10 MB 宽；存进文件桶时是 `files_bucket_file_max_bytes`。放不下时在领上传地址时就拒（`libraryFull`），报错说出这份文件多大、还剩多少、一共多少。
- **卡片**：`handed` 事件和这次回答的其他部分一起存进 `assistant_messages.parts.handed`，面板重新打开时照样画出文件卡片。

## 打转 {#loops}

个人芝士用的模型五花八门，弱一些的会原地打转。会话自己拦三种情况，都不靠模型自觉：

- 同一个工具、同样的参数，一次回答里第三次调用不执行，告诉模型换个做法（`platform.ts` 的 `guardTheRound`）；
- 一次回答调用工具满 200 次后不再调用，模型说明做到哪里，人说「继续」再接着做；每个分身另算 200 次；
- 写出的文字结尾同一段（60 字以上）连续出现三遍，停止这次回答并告诉人原因（`personal/loops.py`）。

## 开头保持不变 {#cache}

模型服务按请求开头逐字缓存，命中的部分按缓存价计。所以每次提问发给模型的开头对所有人、所有页面、整段对话都一样，对话只往后加：

- 系统提示词只有规则，对每个人都一样；这个人是谁、时区和记忆的索引在对话开头说一次（`prompt.about`），现在的时间附在每个问题末尾（`prompt.now_line`）。
- 工具表固定，同一组、同一顺序，不随页面增减。
- 当前页面变了才附，放在问题前面。
- 工具结果在写出时就定好长度，之后不改；读过的东西要时再读。只有 pi 在对话快满时压成摘要这一次改写。
- 每次回答在 `assistant_messages.parts.usage.calls` 里记下每次模型调用的输入 token 和其中缓存命中的部分，「个人芝士」数据页按它算回答的缓存命中率。`test_each_model_call_begins_with_the_whole_of_the_one_before` 守着这一条：会话退出重起之后，开头也逐字不变。

## 评测 {#eval}

改工具描述、返回格式、规则或换模型之后，跑 `evals/assistant/` 下的两套评测比较前后的分数（不进 CI，怎么跑见那里的 `README.md`）：

- **授权判断**（`gate_eval.py`）：一组带标准答案的操作，用平台自己的 `gate.judge` 判，数漏放（该确认却执行了）和误拦（该执行却请确认了），按类型分开；其中「听上去像同意、其实不是」的一类出得最多。
- **整体**（`run.py`）：本机起真实的后端、真实的 pi，模型走 dev 网关，在一个模拟的工作区（`world.py`：四个项目、几百条消息、等人审阅的卡、邀请和提醒、看不到的私密频道）里问三十多个问题（`tasks.py`）。每题从同一份工作区开始；跑完按平台上的结果核对（发了什么、发到哪、变成了哪些卡、设置改成了什么），再由评审模型按要点打分。报告同时给工具调用次数、token、缓存命中率（算法同「个人芝士」数据页）、费用和用时。

两套都有三成留出的题，调整时不看它们，它们的分数才说明改动有没有用。

## 面板 {#panel}

题目页「问芝士」、发布页、改题页用的是同一个 `AssistantPanel`，输入框和工作台的是同一套零件（`ComposerEditor`、`ComposerChipRow`、`ComposerActions`），文件能点「+」选，也能拖进面板，芝士在答时也能交（随插话一起进这一轮）；麦克风和工作台输入框同一套（`composables/useComposerVoice.ts`），听写走 `POST /assistant/voice`：不在哪个项目里，费用记在本人账上，每天的秒数和频道里共用（`voice_inputs.project_id` 可以空着）；芝士每一轮做的事由 `AssistantTurn` 画：说的话和调的工具按发生的先后排开（`lib/assistantView.ts` 的 `interleave`，按每一步的 `at`；没记位置的旧回答把步骤都排在话前面），接连调的工具一行一步（动词 + 一个参数，答完每组收成一行「N 步」），工具回来就打勾，最后几步都回来、还没写下一段话时末尾一行「思考中」；进度清单画在最上面，做完收成「N/N 项已完成」；分身挂在派它的那一步上，跟着转圈、打勾，写「进行中」「已完成」「已停下」「失败」，点开看它做过的几步；写进草稿的改动是一张卡（每格能撤销），`notes` 是「待你确认」，发布的结果带「查看题目」。话题一次读 30 个、一个话题先读最近 50 条消息（`GET /assistant/conversations?before=`、`GET /assistant/conversations/{id}/messages?before=<seq>`），翻到顶接着读：先读完最上面那个话题，再读它上面的话题；读回来的接在上面，眼前看着的不动。芝士在答时输入框照样能打字，打了字或带了文件按钮是发送（插进这一轮），空着是停止。题目页的抽屉打开时才挂；发布页、改题页上它一直停在右边，窄屏是底部弹层。面板只拿 props、发事件，取数和流式读取在 `useAssistant`。

发布页、改题页上，表单跟着芝士动（`useTaskDraft`、`useDraftAgent`）：

- `tool_args` 流进来时，页面把参数读成表单的几格先填上（`lib/taskDraftAgent.ts` 的 `previewOf`，只填能确定的值），这几格这时不算人改的；`change` 到了，用服务端的值覆盖预览，记成已存；`tool_failed` 就收回预览。只改详情里几处（`description_edits`）时，预览在现在的详情上把已经写出来的那几处换掉。
- 参数常常一大块一起到，`lib/typist.ts` 按格子定节奏（哪些参数长、哪些只用来定位由工具说，`cheese_problem(action=write)` 的是 `DRAFT_WRITE_PACE`）：边读那段 JSON 边认出在写哪个字段，一格的字每秒 60 个，字多就加快，短格子最多 1 秒、详情和评分标准最多 2 秒写完；换到下一格前停 0.22 秒，让指针先过去；定位用的字段（草稿 id、要替换的原文）一下子放完。调用结束、`change`、`published` 都排在放完之后。
- 要整格重写、原来有字时，停的那一下把原来的字选中；改详情里的一段时，先选中原文，再跟着新文字走（`useAgentPointer` 在编辑器里找那段字，`AgentLayer` 画选中）。
- 页面跟着芝士滚：换到一格时平滑滚进视野；字写到看不见的地方时立刻滚过去，先滚那一格自己（详情超过最大高度后在框里滚），再滚页面。人刚自己滚过就不跟。
- 表单上一层是 `AgentLayer`：整张表单一个指针。写打字的格子（名称、详情）时是带「芝士」名字的文字光标，停在字的末尾（输入框量字宽，编辑器读 TipTap 挂在 `.ProseMirror` 上的编辑器）；写选项、数字、日期、列表时是箭头。正在写的那一格描着边，写过的格子标「芝士已填写」，人改过这一格后标记消失。位置由 `useAgentPointer` 量 `data-agent-field` 标着的那几格：芝士在答时每一帧都量，不在答时格子变了大小也量。描边只在换格子时滑过去，光标不带过渡，免得落在字后面。「芝士已填写 撤销」那一小行是 `AgentMark`，以后别的页面上芝士改过的地方也用它。
- 撤销一格走 `POST /assistant/changes/{id}/undo`：服务端放回芝士这一轮第一次改它之前的值，当作人改的存下，表单照着填回（`useTaskDraft.restored`），不再存一遍。对话里眼前这份草稿一轮只有一张改动卡，同一格写了几次只留最后一次。

一份文件拆出几道题时，芝士在同一段对话里建的几份新题目草稿排在表单上方（`TaskDraftStrip`）：点了切换，对话还是那一段，不重读；芝士正在写的那份转圈，还没建好的先占一格。「全部发布」先存下眼前这份，再一份份发布（`POST /task-drafts/{id}/publish`）：草稿列表的 `missing`（后端 `drafts.missing`，名称、详情、参与方式、难度）不空的那份不发，标出缺几项，切过去时表单标出缺的格子。

芝士的「当前页面」里列着空间的题目模板。`cheese_problem(action=write)` 给 `template` 时，先按模板填好名称、详情和几项设置（和页面上「使用模板」一样），再写同一次给出的其他字段；返回里带着套好的详情骨架，芝士在它的小标题下面填。
