---
title: 任务与工作目录
kind: 参考
summary: 一条任务 = 一段自己的对话 + 一份实况文档 + 一个分支 + 一个工作目录，以及同一条规则算出来的任务列表与待处理清单。
covers:
  - backend/app/domain/room_task/
  - backend/app/domain/conversation/
  - backend/app/api/routes/topics_tasks.py
  - backend/app/api/task_instructions.py
  - backend/app/api/routes/awaiting.py
  - backend/app/api/awaiting_deps.py
  - backend/app/api/routes/git_http.py
  - backend/app/domain/addressing.py
  - backend/app/domain/task/package.py
  - backend/app/domain/library/mount.py
  - backend/app/domain/project/starter.py
  - backend/app/domain/task/visibility_service.py
  - backend/app/api/routes/tasks/
  - backend/sandbox/cheese
---

# 任务与工作目录 {#tasks}

一条任务是房间旁边的一段对话，带着自己的实况文档、分支和工作目录；「全部任务」里的每一组和「待我处理」里的每一行，都是同一份规则算出来的。

> 讲：一条任务由哪几件东西组成、谁能在里面说话、怎么创建和开始、状态怎么变、机器上的工作目录怎么来、同步与备份、任务列表的列和短语。不讲：交付链路本身（分支→PR→采纳合并，见[任务 → 分支 → PR → 验收合并](/dev/delivery)），验收卡与合并态（见[验收与采纳](/dev/accept)），任务列表、项目总览和待我处理读的其余几块（见[看板](/dev/boards)）。

## 一条任务由什么组成 {#anatomy}

人创建任务时造出第一件，分支和工作目录是**算出来的名字**，不是另开的记录：

| 东西 | 在哪 | 谁造的 |
|---|---|---|
| 任务卡 | `tasks` 表一行（`room_task/models.py` 的 `Task`） | `TaskService.open_thread` |
| 对话 | `conversations` 登记表一行，`kind = task`，id 就是任务的 id | 数据库触发器，插入任务时登记、删除时移除 |
| 实况文档 | `documents` 一行（不属于任何房间），`tasks.document_id` 指向它 | 第一次被要时建（`TaskService.ensure_document`） |
| 分支 | `task/<id 的前 8 个 hex>`（`Task.branch_name`） | 同一个函数，从 id 算 |
| 工作目录 | 执行机上 `$CHEESE_WORKTREE_ROOT/<task_id>`（默认 `/work/<task_id>`） | `cheese worktree`，在机器上建 |

`workspace_name` 是同一个 id 的另一种写法（`task_<8 位 hex>`），给人看的名字。三个名字都从卡的 id 推出来，没有第二个来源。

一条活**只属于一个房间**（`Task.room_id` 指向 `topics`），活不嵌套——「这条活的活」是同一个房间里的另一条活。卡上另外两组人：`owner_handle` 是谁的任务（唯一的负责人，一个人），`reviewer_handle` 是谁说它可以落地；验收人在**开始那一刻**解析并写死（开始时指定的，否则任务上已有的，否则负责人本人——前提是他审阅得了，否则项目设置 `branch_protection.default_reviewer`，都没有就拒绝开始），不在递卡时回头读设置——设置是会变的政策，而「这条任务交给了谁」是那一刻的事实。`agent_handle` 是哪位 AI 队友在做（它在名册上的座位），空着就是所在频道的队友。任务由 AI 队友创建，由创建它的那位做；从一条消息转来，由那条消息交给的那位做。

## 状态只有两个 {#status}

`TaskStatus` 是 `open` / `closed`，只有这两档：一条活要么开着，要么关了。开着的任务在做、在等、卡住还是等审，不是这一列的值，是从卡、做它的 AI 队友的会话在不在跑和它报的进度算出来的显示状态，见[任务列表与待处理清单](#awaiting)。

三个时间戳各说各的事，不互相顶替：

| 列 | 什么时候写 | 写完之后 |
|---|---|---|
| `accepted_at` | 每次采纳（那次合并成功），记最近一次 | 交付标记，和 `status` 无关；批准被撤销也不抹掉 |
| `delivered_head` | 同上，记下最近合进去的那一版 | 下一步的工作目录从这里把没合进去的提交带走 |
| `closing_since` | 最后一步采纳时 | 任务还开着，AI 队友写完总结用 `cheese_task(action=close)` 关掉它，最晚 45 分钟（`room_task.closing`），但任务里有总结以外的活在做、或有一条消息在等着被读时不关，最晚再等两小时；这期间显示「已采纳」 |
| `closed_at` | 负责人、协作者或任务的会话关闭（`cheese_task(action=close)`，写完总结的那次也是），或最后一步采纳后平台到点的关闭 | `status` 变 `closed` 的**时刻**，独立于「是不是 closed」 |

所以有两条容易读错的组合：一条活可以**采纳过前几步却还开着**（分步交付，见[分步交付](#steps)），也可以**关掉却什么都没交付**（显式放弃）。显示状态先看开没开着：关了的才按有没有采纳写「已采纳」或「已关闭」，开着的照常按进度写（`presentation.task_presentation` 的第一条规矩）。唯一的例外是 `closing_since` 那一会儿：任务还开着，已经写「已采纳」。

窗口跟着关闭一起结束：任何一条关闭路径都清掉 `closing_since`（`close_task`）。关了的任务没有在等的总结，而留着它，负责人重开时拿到的会是一条开着却写「已采纳」的任务 —— 左侧栏要 `closing_since` 为空、「未完成」要它不显示「已采纳」、「已关闭」要 `status` 是 closed，三个哪儿都不收它（2026-10-11，任务 #58）。

## 一条任务是一段对话 {#conversation}

任务在房间旁边有自己的对话，地址就是任务自己的 id：和房间的对话走同一套 `/topics/{id}/…` 接口（发言、标题、实况文档、历史、清单、现场、预览、工作电脑），`id` 换成任务的就是任务的。项目里的人都能在里面说话，AI 队友里只有这条任务自己的会话能发（`POST /topics/{task}/messages`）。负责人和协作者说的话直接叫醒任务的 AI 队友，别人的话 `@` 了它才叫醒，否则跟着下一轮读到（`agent/turn/intake/human.py` 的 `prepare_human`）；会话开场的「这个任务」一段告诉它谁的话是指令（`harness/prompt.task_section`）。做这条任务的是一条独立的会话，按 `(任务 id, 队友, 骨架)` 记在 `agent_sessions` 上（见[会话与轮次](/dev/session#layers)）。它的凭证签的就是任务的 id（claim `t`），`CHEESE_TOPIC` 也是任务的 id，所以它调的每个接口都落在这条任务上，碰不到房间和别的任务（`ActorResolver._confine_task_credential`）。谁能看任务，按它所在房间的名册判。

**创建只由人做**，创建的人就是负责人：

| 入口 | 路由 | 之后 |
|---|---|---|
| 房间 ⋯ 菜单里的「新建任务」 | `POST /topics/{room}/tasks` | 空任务，负责人第一句话时会话才起 |
| 主线上一条消息「转为任务」（支线页头那颗也是它） | `POST /blocks/{id}/upgrade` | 这条消息的支线**原地**转成任务，见下 |
| 支线里的一条回复、任务里的一条消息、主线上的文件「转为任务」 | `POST /blocks/{id}/upgrade` | 另开一件，任务的会话收到一段开场提示，带着那条消息和它前面的几条讨论 |
| AI 队友的 `cheese_task(action=open)` | `POST /topics/{支线}/teammate-tasks` | `here` 为真时这条支线原地转成任务；否则另开一件，同上，再加上它写的 `brief`（请求里的 `summary`） |

任务建出来就在做，没有「开始」这一步：芝士从第一轮起就能改动项目。审阅人在建任务时定下（`TaskService.default_reviewer`）：负责人审阅得了就是负责人，审阅不了（协作模式里的 AI 队友）用项目默认审阅人，都没有时留空，第一次交付时再要（`review/services/reviewers.py`）。

**原地转**（`app/api/thread_task.turn_into_task`）：任务沿用支线的 id，所以对话、AI 队友的会话（`agent_sessions` 按对话 id 记）、花费都留在原处；`threads` 那一行也留着，它把这段对话挂在主线消息下面。`conversations` 登记表里同一个 id 从 `thread` 改成 `task`，由触发器维护（迁移 `e4f7009313c1`：插任务时这个 id 已经是支线就只改 kind；删任务时支线还在就改回 `thread`；删支线时它已经是任务就不动），删了登记行会连带删掉对话和会话。做这件任务的队友沿用支线正在和它说话的那位（`thread_teammate`），换一位就是新的一段会话。`PlaceResolver.conversation` 先认任务再认支线，所以从下一轮起 `Place.keeps_work` 为真，执行凭证和仓库令牌按地点现问，一轮当中转也接得上（见[支线的会话](/dev/session#scratch)）。转的那一刻，对话里落一行「已转为任务，某某 负责」，平台提示附在这一行上（`became_task_prompt`），下一轮交给芝士。转过的支线在主线上不再画「N 条回复」那一行，「支线」页签也不再列它（`thread/reads.py` 的 `_became_a_task`）；任务行带 `from_thread`，前端据此在频道页右半边打开它。支线的结果回原处（`tell_origin`）对它不再发：原处就是它自己。

一条消息可以另开好几件任务。消息在支线里时，任务记在支线挂着的那条主线消息上（`upgraded_from_block_id`），频道主线把任务卡挂在那条消息下面；单独新建的任务才在主线上落一行 `task_created`。

任务另记它是从哪句话来的（`source_block_id`），可以是支线里的一条回复，也可以是另一个任务里的一条：转为任务的是被转的那一条；`cheese_task(action=open)` 建的是这个 AI 队友在那段对话里最近收到的一句人话，频道主线上只认点了它名字的那句。任务页顶上引这句话，点它回到那段对话并停在这一条上（`?block=`）。这一列加上之前建的任务没有它，退回 `upgraded_from_block_id`；两样都没有的（单独新建的任务）照实说是谁新建的。

`cheese_task(action=open)` 只在有人说要做时调。`owner` 是要这件事的人，省略时是这一轮回应的那个人（`asked_in`），再没有就是支线挂着的那条消息的作者。芝士自己看出来该做的，先用 `cheese_ask` 问一句「要转为任务吗？」，选项「转为任务」「不转」；选了的人发出的那句话叫醒它，它再调 `cheese_task(action=open)`，负责人就是那个人。

私聊里没有任务：新建、转为任务、`cheese_task(action=open)` 这三条在同一处拒绝（`privateChatHasNoTasks`）。

另开的任务，开场指令标成「开场那一条」（`agent/opening.py`）。读任务（`GET /topics/{task}/task`）时，文档还空着就带一个 `opening`：`drafting`、`waiting` 或 `failed`，从投递账本、会话对这份输入的回执和会话报上来的状态现读，不另存：读到之前和会话还在干活时是 `drafting`，读进去的工作失败了、或会话读到之后停下来却一句话没说是 `failed`。失败后负责人和协作者用 `POST /topics/{task}/opening` 把同一条指令再发一次。`GET /topics/{task}/related` 交回任务从哪句话来（`source`）、原讨论（支线或频道主线）和讨论里用到的文档、文件，开场指令里也附着同一份清单。

交给 AI 队友的指令在投递账本里过了 30 分钟还没开始（`delivery/agent.py` 的 `GIVE_UP_AFTER`），记为失败，不再每五分钟重试；有意在等的（等发问的会话、等回执）不算。

**实况文档**写这件事要做什么、做到哪、定了什么（`GET /topics/{task}/document` 第一次要时建）。看得见任务的人都能读，负责人、协作者和这条任务自己的会话能写。芝士边做边写，不先写完再动手。

**转交**：`PATCH /topics/{task}/task`（`GET` 同一个地址读任务本身），负责人把任务交给房间里的另一个人（`owner_handle`），或换一位 AI 队友（`agent_handle`，项目里一位队友的座位，`null` 交还频道的队友）。转交前，任务里执行环境落在新负责人不能用的电脑上的队友（原负责人自己的电脑，不进沙箱或那人电脑的规格），先照换环境的流程换到新负责人能用的那个（`environments.usable_by_owner`：任务的默认、房间的默认、项目默认，不能用的跳过，最后是标准沙箱），任务指着这样一台电脑的默认清掉；推送不成功就不转交，说明原因。

**关闭**：`POST /topics/{task}/close`，参加这件活的人（负责人或协作者，`TaskService.takes_part`）或这条任务自己的会话（`cheese_task(action=close)`）。带不带结论都显示「已关闭」，结论跟在房间里落的那条平台消息后面，说它怎么结束的。任务一关闭——人关、任务的会话写完总结自己关、写总结的指令被放弃、采纳后 45 分钟到点，都走同一个 `room_task/closing.close_task`——AI 队友在这条任务里还没做完的活就被打断，写下的留着。「写完总结」只认会话调了 `cheese_task(action=close)` 这件事：会话读到了总结指令、之后停下来，平台看不出它写没写，任务照旧开着等到点。后两种是平台自己关的，下面几样都是平台核得了的事实，有一样就不关，等它们没了之后的那次再关（`closing.unfinished`）：任务的会话 runner 报着在干活、而它还没读到总结指令（那它在干的就是别的）；有发给 AI 队友的消息还没被读；有消息读进去之后那段工作失败了。这样平台自己关任务时通常什么都不用打断，只有采纳 45 分钟还没写完的总结会被打断；另一个例外是采纳 45 分钟再过两小时（`closing.STOP_WORK_AFTER`）还没停的活，到这时任务照关、会话照停，按会话的座位各记一条运行记录，房间里落一条平台消息列出没交付的是什么（`review/room_notices.tell_room_undelivered`）。打断的方式：关闭落到持有会话的后端就当场打断（`ChatService.stop_work`）；落到别的后端（部署交接时接手的那个）时，持有会话的进程按任务已关闭的状态打断（`ChatService.stop_work_in_closed_tasks`，每 `closed_task_stop_interval_s` 看一次）。关闭后任务对话照样能说话，和支线一样：会话的改动留不下（`Place.keeps_work`），只有被叫到才回答，`task_section(closed=True)` 告诉它只回答、不改；按关闭状态打断和结束轮次的两处（`turn_inputs.stop_work_in_closed_tasks`、`open_turns`）只管关闭之前开始的轮次，关闭之后有人来问的那一轮不受影响。任务自己的会话关闭时不当场打断，那一轮就是它自己；关闭 30 秒后还在跑，同样打断。交付的改动被采纳时任务自己关。人或任务的会话关闭任务时，任务那个还开着的 PR 在提交后被关掉，分支留着（`review/task_pr.close_pr_after_commit`）；已经合并或已经关掉的 PR 不动。随项目归档一起关掉的任务不碰它的 PR，理由见 `review/archive.py`：归档的人未必是这个 PR 的请求人。关 PR 失败不挡关闭，只在日志里留一条 warning，PR 保持开着。

人在任务上看得到的变化，做它的 AI 队友也要知道，不然人说的一句话它对不上（`room_task/heard.py`）。它自己做的（递卡、报状态、关任务、起名）由它调的那个工具在回复里说清对任务做了什么；别人做的——人，或者平台自己——分两种。要它动手的（验收退回、作废、检查红了、冲突、PR 被拒绝合并或离开合并队列、采纳之后接着做或写总结）是一条指令，经投递账本直接交给任务自己的会话，把它叫起来（`heard.act` → `delivery/agent.py` 的 `record_task_instruction`），不经房间的芝士转；给人看的那一行在同一个事务里落进任务对话，任务已关闭时只落那一行。依赖通知、消息被编辑也走投递账本，各自写在 `review/pr_publish.py` 和 `block/editing.py` 里。只要它知道的（换验收人、撤回采纳、人关闭或重新打开任务、改名、换负责人或协作者、换 AI 队友、平台关任务）不叫醒它：那句话挂在任务对话里一个块的 `agent_notice` 上，跟着它下一次的输入进去，读过就盖戳（`block/history.notices_after`）。这件事本来就有一行给人看的，话挂在那一行上（`heard.informing`）；没有的落一行房间里不显示的（`in_room: false`，`heard.inform`）。看板上的「已离开」不告诉它：那说的是它的会话不在，会话一回来这个标记就撤掉，它读得到的时候已经不成立了。任务对话里花的钱记在房间下，也记在任务下（`usage.task_id`）。任务里每位队友的执行环境是自己的；它第一次要手时按 任务的默认（`tasks.default_environment`）→ 房间的默认（`topics.default_environment`）→ 项目默认 取一份，之后默认再变也不跟着动。负责人、项目管理员、任务此刻所占设备的主人，或任务自己的会话，用 `PUT /topics/{task}/sessions/{session}/environment` 换一位、用 `PUT /topics/{task}/environment-default` 改任务的默认；设备页的批量挪走对任务会话走的就是前一条。共享给项目所在团队的设备谁的任务都能用；只放进这个项目、没共享给团队的设备算接入人自己的电脑，只做接入人本人负责的任务（`environment.works_tasks_of`）：选不了，那个人电脑的沙箱也不放在上面。

## 工作目录怎么来 {#worktree}

`cheese worktree <task_id>`（`backend/sandbox/cheese`）是机器上唯一建工作目录的地方：

1. `POST /projects/{project_id}/git/tasks/{task_id}`——这一下**才是**把 `author_handle` 写上的地方（`TaskService.record_author`，只认第一个）。创建任务和房间成员都不证明是谁在干活。只有任务自己的会话（凭据签在任务的对话里）能打开它；房间里的会话来打开，答「这条任务由它自己对话里的 AI 队友来做」。不是这个频道的任务答「这个频道里没有这条工作任务」，已结束答「这条任务已结束，请创建新任务」。房间的会话仍然读得到任务、存取得了它的备份：换工作电脑时 `cheese sync --all` 要同步这台机器上的每个任务目录（`git_http._task_for`）。
2. 在 `~/.cheese/repositories/<project>.git` 造/复用一个裸仓，凭证由 `!cheese git-credential` 现取；`fetch --filter=blob:none` 只取提交不取历史 blob（注释里记着实测：同一份仓库整下 195 MB、这样 9.6 MB；一个 169 MB 的 fetch 曾冻住后端 3.7 秒）。历史仍在，`git log` 和与基准的 diff 照常，某个文件的旧内容真被读时才取。
3. `git worktree add` 到 `/work/<task_id>`，起点是远端分支（还没开出来就是基准分支）。
4. 写两个钩子：`post-commit` → `cheese sync`，`prepare-commit-msg` → `cheese git-attribution`。钩子装在裸仓上，同一裸仓的每个工作目录都会跑它们，包括在任务目录旁边用 `git worktree add` 另开的那种；只有带任务标记（`cheese-task.json`）的任务目录才署名、才同步，其余目录照普通 git 提交，不碰任何任务。
5. 这个环境里第一次建出这个目录时，把平台上这条任务最新的快照恢复进来（[新环境里的自动恢复](#restore)），在准备依赖之前：恢复回来的改动可能改了要装什么。
6. 跑项目的启动脚本准备依赖（环境准备脚本的 `task` 模式）。成功后在任务的 git 目录里记下 `cheese-environment-prepared`：跑之前房间钉住的环境配置加每个依赖文件（`_DEPENDENCY_FILES`：各生态的锁文件和清单，如 `uv.lock`、`pyproject.toml`、`setup.cfg`、`requirements/**`、`environment.yml`、`package.json`、`pnpm-lock.yaml`、`go.mod`、`Gemfile`，树里任何位置、提交没提交都算，被 git 忽略的不算）内容的摘要，以及跑完后检出里没跟踪的安装目录（`node_modules`、`.venv`、`venv`、`vendor`）。下次打开时摘要一样、这些目录都还在就不再跑；改了依赖文件、切到依赖文件不同的分支、换了环境配置、删了装好的目录（`rm -rf node_modules`、`git clean -xdf`），都会再跑一次；`cheese worktree <id> --prepare` 不看这些再跑一次（机器上有环境准备脚本时）。失败时删掉记录，下次一定重跑。

再次打开一个已经在任务当前分支上、远端地址没变、没有待恢复快照的目录（`_opened_as_left`），跳过第 2、3 步对托管平台的 `ls-remote` 和 `fetch`：agent 的 shell 每条命令都回到房间目录，几乎每条命令都要 `cd "$(cheese worktree …)"`，dev 上 10-12 到 10-17 十分钟内的重开中位数 16 s、p90 76 s，却什么都没改。这样打开时 `refs/remotes/origin/*` 停在这个检出上次 fetch 或推送时的样子，要最新的自己 `git fetch`。任务的分支变了（一步合并了）、远端地址变了，照旧走完整流程。

两处守卫写在注释里：创建期要拿 `<project>.lock` 串行化（同一台机器上几条会话会并发要工作目录，一个被打断的 clone 会被当成完整仓库）；这台机器上有 `~/.cheese-environment/config.json` 却找不到准备脚本时**直接失败**——静默跳过会交出一个依赖从没装过的工作目录。

## 同步、备份、恢复 {#sync}

`cheese sync` 做两件事，顺序不能换（`_sync_task`）：

1. **备份未提交的文件**：用另一个索引文件（`GIT_INDEX_FILE=…/cheese-snapshot-index`）`read-tree HEAD` + `add -u`，再加上该带的未跟踪文件，`write-tree`，把树写成一个挂在当前 HEAD 上的临时提交（`commit-tree`）。工作树自己的暂存区不受影响。带哪些见下面的[备份里有什么](#backup-contents)。
2. **推 HEAD**（`_deliver_task_branch`）：HEAD 已在本检出上次推送或 fetch 时记下的分支尖（`refs/remotes/origin/<branch>`）里，就不问托管平台、什么都不推：托管平台一时连不上时，问一句只会把它已经有的工作报成「没能推回」，而且每次 sync 都报一遍。否则先读托管平台上这条分支的尖。HEAD 已在其中——同一任务的另一个检出走得更远——就什么都不推；尖在 HEAD 的历史里就普通推送；两边分叉而那个尖是本检出自己在这条分支上有过、后来 amend/rebase 掉的提交，就以它为 lease 替换（`--force-with-lease`），整理本任务的提交（比如移除依赖）本来就是工作的一部分；尖里有本检出从没有过的提交就拒绝，替换会把别人的工作从 PR 上删掉。

手动跑的 `cheese sync`（不带 `--all`）先看这个检出有没有要做的（`_synced_already`）：没有未提交的改动、HEAD 已在本检出上次推送或 fetch 时记下的分支尖里、也没有待恢复的快照，就只问一次平台这条任务现在在哪条分支：还是检出所在的这条（或任务已结束），就直接退出 0，不探托管平台、不问快照。前两条和 `sync --all` 跳过没有东西要同步的任务（`_has_work_to_sync`）是同一个判断；分支那一条是因为一步合并后任务换到新分支，完整的 sync 会把检出带过去、在新分支上交付，交卡（`cheese_card(action=request)`）走的正是这条 `cheese sync --task`。agent 常在 `git commit` 后面再手动跑一次 `cheese sync`，而提交的钩子刚同步完。

任务的 PR 在合并队列里时（任务数据里的 `merge_queued_pr`），托管平台锁着这条分支：有新东西要推就不推，只备份，房间里说清楚这些提交为什么不在 PR 里——重试推不上去，合并后在新任务里交付，或由验收人退回、PR 撤出队列。

任务已结束时只做第 1 步：不推，也不看检出当前在哪个分支——结束后被拿去干别的（切到别的分支、detach）的检出，里面的东西照样进备份。备份失败时 `cheese sync --all` 那一行写成「已结束的任务 X 同步失败」，换工作电脑时平台按它区别对待（见 [turn.md](turn.md)）。机器自己不为已结束的任务往房间发「改动没能推回」：它没有哪一轮的改动丢了，换机时是哪条任务、为什么留在原来那台上，由平台那一条提示说。

备份和推送各自遇到连接被重置或超时，就在同一次 sync 里再试一次；连着两次才算失败——一次重置是网络，连着两次多半是这个请求本身（大小、中间代理的限制），再试只会拖长换机时那两分钟的推送。

备份不是每次都往对象存储打——`_backup_task_snapshot` 先看 `rev-list --count <snapshot> ^<base>`，是 0 说明每个对象都已经在托管平台上，就地返回；再问一次后端这条任务最新那份备份（`GET …/snapshots/latest`），它的 head 和文件树跟现在一样，也不再发——快照提交每次现做，不问这一句，没动过的已结束任务每次 sync 都会重新打包上传一遍，换机时的推送就等着它们。问不通、还没有备份、本机已经没有那份快照，都照常上传。叠在另一条任务分支上的任务，底座分支合并后会被删掉（这里 fetch `--prune` 也跟着删），这时改成扣掉 `--remotes=origin`：托管平台任何一条分支上见过的提交都不进备份。真要备份的，进 `refs/cheese/snapshots/<task_id>`、打成 bundle、随 `PUT /projects/{id}/git/tasks/{id}/snapshots/{sha}` 交给后端（`room_task/snapshots.py` 的 `save`），落进**私有** bucket（`task-snapshots/<project>/<task>/<sha>/<digest>.bundle`），单次上限 512 MiB（超了回「请将大文件移入附件存储」；未跟踪文件已经被[预算](#backup-contents)挡住，能超的只剩提交过、托管平台上还没有的内容和跟踪文件的改动），服务端按 sha256 复核 digest、并校验它真是个 git bundle。同一份内容再交一次不会再存一遍；它要是已经不是最新那份（文件改动过又改了回去），就新记一行指向原来那个对象，让它重新成为最新——否则恢复出来的是那次已经撤掉的改动，下一轮 sync 也会因为「最新」对不上而每次都重传。bundle 每次现打，上传完不论成败都删：底座会前进，留下一份被拒的原样再发，只会每轮都被拒。

同步失败**会写进现场**（`_report_sync_failure`，一条 `sync_failed` 记录）：一轮结束时改动还在机器上，和一轮成功长得一模一样——这正是「一次被拒的推送被读成了一个完成的回合」的由来，直到机器被回收、改动跟着没了。它写进跑这次同步那个会话的现场，不属于房间：房间里没有人能重推，而原来那句「请重试本轮」是给机器看的，摆在人面前只会让人去找一个不存在的按钮。它带的是检查点自己那一轮的 id：平台跑 `cheese sync --all` 时把刚结束的那一轮放进 `CHEESE_TURN`（Claude Code 的 Stop 钩子先问运行器 `checkpoint_turn`；driven 的骨架在 `turn_ended` 里读自己那份 owner），机器同步失败时随 `X-Cheese-Turn` 带回来，所以它出现在那一轮的「查看过程」里，也在整段现场里。没有轮次的同步（提交钩子自己跑的那一次）照样写，`as_block` 把它标成平台自己的话，现场按没有轮次的那一行画它。房间的检查点做 `sync --all` 时会带上同屋别的任务，那条记录就落在房间的现场、正文点名是哪条分支；任务自己的凭据不许写进另一个任务的会话，所以它不会出现在那个任务的页面上。PR 在合并队列里是例外——重试救不了，要人决定退回卡还是换个任务交——那一条仍旧在房间里说。成功不发消息，那会是训练人跳过它的噪音。

### 每轮的检查点 {#checkpoint}

每一轮结束时，会话让机器跑一次 checkpoint（执行器的 `checkpoint` 控制，跑的是 `cheese sync --all`）：这台机器上每个有东西没推的任务目录都备份、推送一遍。三个骨架是同一个动作，只是谁在一轮结束时去要它不同：Claude Code 用平台装的 Stop 钩子（`remote_execution/client.py` 的 `checkpoint`），Codex 和 pi 由 runner 在一轮结束时要（`driven/runner.py` 的 `turn_ended`）。runner 不让这一轮等它：房间在 agent 停下时就看到这一轮结束，checkpoint 在后面跑，一次只跑一个，跑的时候又结束了一轮就跑完再来一次；失败由 `cheese sync` 自己写进现场，是平台的一行。

### 新环境里的自动恢复 {#restore}

沙箱用完就扔，换上的新环境里没有任务目录。`cheese worktree` 第一次在这个环境里建出某条任务的目录时，在它的 git 目录里留一个 `cheese-restore-pending`，然后（`_restore_snapshot`）：

1. 问平台这条任务最新的快照（`GET …/snapshots/latest`，带 `created_at`）。没有，或者快照里的提交已经都在分支上，就什么都不做。
2. 下载、校验 sha256、按提交 id 取前置提交、`bundle unbundle` 进本机的裸仓。
3. 快照的 head 就是分支尖，或者分支尖在它的历史里（上次推送没成功），而且目录是干净的：先把分支快进到 head（这些提交下次 sync 推上去），再用 `read-tree -m -u <head> <快照>` 把快照的文件放进工作区、`reset` 把暂存区还回 HEAD。改过的文件回来是未暂存的改动，新文件回来是未跟踪的。
4. 其余情况——目录里已经有改动、分支在快照之后又前进了（快照里有没提交的改动时）、两边分叉——一律不套用，免得覆盖：快照原样检出到 `~/.cheese/recovered/<task>-<快照前 12 位>`，和 `cheese recover` 是同一个目录。
5. 恢复了什么、没恢复什么打到 stderr，跑 `cheese worktree` 的 agent 在工具结果里读到：哪条任务、快照的时间、几个文件、几个提交；依赖和缓存、生成目录、任务目录以外的文件（包括 `/tmp`）、正在运行的进程不在快照里，不会回来；备份时就略过的未跟踪文件照快照提交的说明列出来。

做完才删掉 `cheese-restore-pending`。没做完（平台一时连不上）就留着，下一次 `cheese worktree` 或 `cheese sync` 再试；**留着它的时候这个目录不备份**：一个还没恢复的新目录备份上去，会成为最新的快照，把要恢复的那份顶掉。已结束的任务直接跳过备份，平台上那份快照就是它的备份。

`cheese recover <task_id>` 把最近一次备份恢复到 `~/.cheese/recovered/<task>-<snapshot 前 12 位>`，**不动原工作目录和评审分支**；bundle 的 sha256 对不上就一个文件都不恢复。备份缺的历史（bundle 头里的前置提交）按提交 id 从托管平台取，不经底座分支：叠放任务的底座合并后就被删了，而一台从没检出过这条任务的机器除了提交 id 没有别的可取。合并过的 PR 在托管平台上留着它的头，这些提交还在。

### 备份里有什么 {#backup-contents}

`_snapshot_index` 决定：

- **跟踪的文件一律带上**，不论多大：改过的、删掉的，以及 agent 已经 `git add`、还没提交的新文件（包括越过 `.gitignore` 强行加的）。对跟踪文件的改动永远是工作。
- **被忽略的文件一律不带**（`.gitignore`、`.git/info/exclude`、全局 excludes）。
- **其余未跟踪的文件**，先去掉落在只有工具才写的目录里的（`_GENERATED_DIRS`：`node_modules`、`__pycache__`、`.venv`/`venv`、各种工具缓存），它们重装就回来，而且一个 `node_modules` 的文件数比任务里其他东西加起来都多；剩下的合计超过 `_UNTRACKED_BACKUP_BUDGET`（64 MiB）就从最大的开始略过，直到放得下。`dist/`、`build/`、`target/` 不按名字去掉：这些名字也有人用来放手写的文件，它们是大文件时靠这道预算挡住。

略过了什么要说出来：写进那次快照提交的说明里跟着备份走，`cheese recover` 恢复完照着列一遍；单条任务的 `cheese sync`（提交钩子跑的就是它）打到 stderr，提交的那个 agent 能看到；因为太大被略过的文件，每个在房间里说一次（这台检出说过哪些记在 `cheese-backup-left-out`），生成目录不说。`sync --all` 成功时什么都不打：平台把它的输出全当失败读（`session_work._failed_tasks`）。

## push-fix {#push-fix}

`cheese push-fix [--task <id>] [--drop-dependency]` 先跑一次 sync，再调 `POST /topics/{task}/push-fix`，把新提交刷到这条活**已有的**那个 PR 上并刷新验收状态；没有可推的东西就打印原因，不报错。`--drop-dependency` 用在「已经整理并验证是独立改动」之后：把现有 PR 改到项目默认分支，清掉任务依赖和旧批准。

一次交付只有一个 PR（`Task.pr_number`），改验收卡不会新开 PR，`push-fix` 推的还是同一个。任务分步交付时，见[分步交付](#steps)。

## 分步交付 {#steps}

一件任务可以交付好几次。每次递卡时芝士说明这是不是最后一步（`cheese_card(action=request)` 的 `completes_task`，存在 `AcceptCard.completes_task`；这一列之前递的卡都算最后一步）。合并之后怎么走只在一处决定（`review/task_landing.py` 的 `delivery_landed`），采纳、合并队列、轮询器、在托管平台上直接合并的那几条路都经过它：

- 最后一步：AI 队友写完总结后任务关闭（见上面 `closing_since`），`after_close` 收拾机器上的工作目录。例外是合并落地时，或者写总结的窗口里，AI 队友正在做递卡之后才开始的事（它的会话 runner 报着在干活，而它读到了这张卡递上之后人或它自己发给它的一条消息；平台的那几种指令——别的座位被叫进任务、冲突/CI 修复——不算），或者任务里有一条发给它的消息还没被读（窗口里由周期任务 `task closing` 每一跳看一次，`pr_poll.sweep_closings` → `task_landing.go_on_while_working`，每条任务各自一个事务，一条够不着代码托管不影响别的）：那件事不在这次合并里，关掉任务就把它半路打断、也没处交付，所以这次合并按「不是最后一步」走，平台叫芝士把手上的活作为下一步交付，没东西可交就自己关任务。没送出去的总结指令不再发（`delivery.agent.dispatch_pending`）。
- 不是最后一步：任务开着，`TaskService.next_step` 把它换到一条从默认分支切出的新分支上（`task/<id>` 之后是 `task/<id>-2`、`-3`……），清掉 `pr_number`，下一步开自己的 PR；平台在任务的会话里留一条指令叫芝士接着做。叠在它旧分支上的任务由 `retarget_completed_dependencies` 改到默认分支。

**一笔合并只落地一次**：`delivery_landed` 认这次合并落到分支上的那个提交（`Task.delivered_head`），已经落过的那一笔直接不做。几条路看到的是同一笔合并，而任务可能早换到下一步的分支上、或者已经被重开了 —— 第二次落地会把已经走过去的任务再关一次。没有提交可认的交付（一份讨论、一份产物、一个网址）不受这条约束。

机器上的工作目录在下一次 `cheese worktree` 时跟过去（`_follow_task_branch`）：`delivered_head` 之后的提交变基到最新的默认分支上，没提交的文件原样带过去；和最新代码冲突就留在原分支上不动，报出冲突让芝士先解决。只有这件任务自己早先的分支会被跟过去，芝士自己切出的分支不动。

每次采纳（以及带结论关闭）的结果由任务的 AI 队友回复到它出自的那条支线里（`tell_origin`），没有支线就开一条。角色有 `Permission.tasks` 的人或任务自己的会话（`cheese_task(action=reopen)`）可以重新打开关了的任务（`POST /topics/{task}/reopen`）。比能关闭它的人宽，是有意的不对称：关闭撤不回（开着的卡作废、PR 关掉，重开都不还原），所以只给参加这件活的人；重开能再关回去，房间里也写着谁重开的，所以给项目里做任务的人。别的任务的会话、房间的会话凭证两样都做不了。关闭之后任务对话照样能叫到它的会话，有人要它接着做，它就自己重开；交付过的同样换到新分支上，没交付过的忘掉旧 PR、留在原分支上，草稿 PR 扫描再为这条分支开一个 PR（旧 PR 当初没关掉的话就沿用它）。

## 任务列表与待处理清单是同一份规则 {#awaiting}

两处显示状态**都不存库，读的时候从已有事实算**，而且只在后端算一次（`room_task/presentation.py`）。这条逻辑以前在前端是两份 TypeScript，同一句规矩写两遍就会走散；而 CLI、通知和以后的任何客户端问的是同一个问题，它们读不到那两份。这一层是纯函数：没有 I/O，不碰 session，不读时钟——「现在几点」是参数。

列回答的不是「进行到哪一步」，是「**该谁动**」：

| 列 | 意思 | 允许出现的短语 |
|---|---|---|
| `building` 进行中 | 在做，还没递出交付 | 芝士在做、在等结果、进行中、空闲（房间）、草稿（房间） |
| `delivering` 检查中 | 下一步在平台 / 芝士手上 | 检查运行中、等待检查、修复检查、解决冲突、平台更新分支 |
| `needs_you` 待处理 | 下一步在人手上 | 检查未通过、待审阅、已退回、待回答、卡住了 |
| `done` 已关闭 | 已关闭：最后一步已采纳，或没交付就关了 | 已采纳、已关闭（留没留结论都是这一句） |
| `archived` 已归档 | 房间才有；任务不归档 | 已归档 |

同一个客观事实会因为「谁负责下一步」落在不同列：CI 红了但平台已经派芝士去修是 `delivering`（显示「修复检查」）；芝士推不上去、那个红没人能清掉就是 `needs_you`（显示「检查未通过」）。列**从短语推出来**（`_show`：每个短语是它那一列专属枚举的成员，列由成员的类型查表得到），所以「显示了一句不属于本列的话」在结构上写不出来。

一件任务只有一个状态：开启、在做、在等、卡住、待回答、待审阅、退回、关闭（`room_task/presentation.task_presentation` 是看板唯一的判据）。平台核得了的事实定它：有一张卡在等审阅就是待审阅，由卡说清在哪一步，验收人被点到；卡被退回或作废，平台把任务放回在做；人关闭、重新打开就是关闭、开启。没有卡的时候，做它的 AI 队友用 `cheese_task(action=state)`（`PUT /topics/{task}/task/progress`）报 `working`、`waiting` 或 `blocked`，外加一句说明，记在 `tasks.progress_*` 那几列上（`room_task/progress.py`）：界面上的标签分别是「在做」「在等」「卡住」，它那句说明写在标签后面那一行（`lib/taskState.ts`）；卡住了落进 `needs_you` 并点到负责人。递卡时它之前报的作废；卡在等审阅时它报不了，接口回 409。这几样都会告诉它：递卡在 `cheese_card(action=request)` 的回复里说，退回、作废在叫醒它的那条指令里说，重新打开在它下一次的输入里说。做它的那位队友的会话在跑是核得了的事实：没有卡、也没有待回答的问题时，它的会话在跑就是在做，除非它在这一次跑里报了别的。「这一次跑」由 runner 自己说，平台不推：开始一次跑的那个 `running` 事件的 (epoch, seq) 就是这一次的名字，runner 记着它，会话里每一次平台工具调用都带上（`X-Cheese-Run`，`executor_transport.PlatformHost.run`；Claude Code 的工具在会话自己的 transport 进程里跑，它像 `compact` 一样经会话 socket 问 runner）。平台在应用这个事件时把它记在 `session_states.run_epoch` / `run_seq` 上（每一个应用到的 `running` 事件都记，不管是哪一次登记送来的、状态翻没翻：事件按 runner 写下的次序应用，最后一个就是正在跑的那一次；替掉一个还读作在跑的旧进程，新进程的这一次是新的），报状态时把调用带来的那一次记在 `tasks.progress_run_*` 上，看板拿 (座位, harness, epoch, seq) 和这条活自己那位队友此刻在跑的那一次比（`agent.teammate_runs`）。Codex 和 pi 的 runner 不报会话状态，没有「哪一次」可说，它们报的状态照旧算数；还没被替换的旧版 runner 不带这一次，它在跑时任务显示在做。报了「在等」之后被构建叫醒、报了「卡住」之后有人回了它，都是在做；在这一次里报的照它说的，runner 的报告晚到也一样。审阅的、被叫进来的别的 agent 在这段对话里跑，不算。会话开始或停下时，runner 的报告让任务所在的页面重读（`room_task/live.sessions_moved`）。平台不从轮次推断，会话停下不改它报的；会话结束只在旁边记一笔 `agent_left_at`（页头写「已离开」），不改它报的——只认报这句话的那位队友自己的会话（`progress_set_by`），任务对话里别的队友的会话进出不算。会话还在跑、而它报的状态 `chat_progress_reminder_after_s` 没动过，平台提醒它再报一次（`turn/steps/reminders.StaleProgressReminder`）。报的是「卡住了」时还有一处补位：它多半是轮次末尾报的，等有人回话时那一轮早结束了，上面那条提醒够不着，所以人说话开的那一轮，平台在输入前面再问它一句能不能往下走（`harness/notices.blocked_report_reminder`）——只问，不替它改。

「待我处理」那份清单（`api/awaiting_deps.py` 的 `waiting_items` + `room_task/awaiting.py`）读的是**同一个** `task_presentation` / `room_presentation`，差别只有范围：「全部任务」是一个项目内全部，清单是跨项目、且点到我的那些。它不能从通知表拼出来——通知是一条条事件记录，卡被驳回、作废或别人先处理掉了，那条记录还躺着，而从它身上读不出已经不作数了；一份「现在还没处理完的有哪些」必须从当下的事实重算。

收件人判据也只有一份（`addressing.py` 的 `address()`，通知投递读的也是它），六种关系：验收人（卡是递给他的）、提需求的人（他等的东西有了结果）、被问的那个人（芝士停在待确认问题上，只有他能回答）、任务负责人（下一步只有他能推：任务文档写好了等他开始、检查红了芝士清不掉、被退回之后没人在改、队友说它卡住了，见 `presentation.owner_acts_on`；只是任务开着不算）、设备的主人（有 agent 开始在他登记的机器上干活）、公告的读者（空间发了一条公告，发布那一刻空间里除作者以外的每个人）。一件事从「平台手上」转入「参与者手上」的那一刻通知那一个人，一次；平台手上的事（含「已关闭」「已归档」）谁都不通知。

## 题目附件 {#task-attachments}

题目的材料是题目自己的资料库（`Shelf.of_task`，`library_files.task_id`）里的文件，字节走 `app.core.blob_store`，和项目、团队的资料库是同一套记录；替换留历史行（`superseded_at`）。读写在 `domain/task/package.py`，路由在 `routes/tasks/files.py`。三条判据各自一句话：

| 判据 | 谁 |
|---|---|
| 谁能放进、替换、删掉 | 出题人本人，或这块板的所有者 / 管理员（`task_roles.teaches`） |
| 谁能看到清单 | 看得见这道题的人 |
| 谁能下载 | 出题人 / 板管理员 / **参与者**，即名下有一份还在、已经通过的申请（自己的，或所在团队的；`claims.claim_of`）；等批的、被拒的、退出的、不再有效的都不能 |

清单绝不比题目本身更保密——看不见材料就无法判断要不要申请；下载比「看得见」窄一格、比「管得了」宽一格——参与者拿不到材料就没法做题。清单接口（`GET /tasks/{id}/files`）先过题目详情那三道闸（`ensure_task_readable`），不是服务里那条更宽的 `can_view_task`：文件名常常就是答案，「有没有清单」本身就是那道题的探针。返回里带 `canDownload`，前端据此显示「下载」还是「开始做这道题之后才能下载」。

改题页直接上传，和资料库是同一条分段上传的路（`library.md` 的「资料库的记录表」一节）：领一次上传的凭据（`POST /tasks/{id}/files/uploads`），按段领地址（`…/uploads/part`），浏览器把每一段连同它的 MD5 放进存储，再登记（`POST`、替换是 `PUT`）。发题页上题目还不存在，选的文件先传成通用附件，`POST /tasks` 带着 `attachmentIds`，建题时拷进题目资料库（`package.add_uploads`）。只认本人传的那几份：**附件 id 是可猜的连续整数**，不认上传者的话，谁都能把别人交作业附的文件拷进自己的题目公开出去。

### 版本 {#task-package-revisions}

`task_package_revisions` 记题目内容的每一版：发布是第 1 版（`changes = {"published": true}`），之后描述、阶段（名称、说明、截止时间、顺序）、提交内容、评分要点（含 `rubric_public`）、材料每变一次记下一版，`changes` 写明哪几样，材料带名字（`added` / `replaced` / `removed`）。`PATCH /tasks/{id}` 改之前、改之后各取一份比对（`_writes._package_parts`），和「在做的人收到要求变了的通知」是同一次比对。每记一版，题目资料库里的 `题目说明.md` 换成这一版的样子，它的历史就是每一版的说明；它不算材料，清单里不列，不能单独替换或删除。提交记下当时的版本号（`task_submission.package_revision`）。

### 项目里的 `题目/` {#task-folder}

从题目派生的项目（`Project.external_task_id`），资料库多一个只读的 `题目/`：`library/mount.py` 把这个前缀下的名字解析到题目资料库（读的是那一边现在的行，不是拷贝），清单、读文本、取字节、版本、项目搜索都经过它；放进、替换、移动、删除、恢复碰到这个前缀一律 422。有没有它跟着项目来自的那一份申请走（`package.task_folder_shown`）：从题目建的项目只来自一份申请，个人的或一个团队的，按项目所在的团队认出来（`claims.project_claim`）。这份申请还在、已经通过，项目里的人和 agent 都看得到；等批、被拒、退出或不再有效，谁都看不到，读的人自己名下别的申请不算。读的那一侧（`app/api/project_library.py`）把判过的结果放在 `Shelf.with_task` 上交进来，没说就看不到，`题目/` 下的字节、文本、版本一律 403（`libraryTaskFolderNeedsApprovedClaim`）。题目删了（`task.deleted_at`），文件夹跟着不见；项目自己在挂上之前放在 `题目/` 下的文件被盖住。

题目更新之后，项目总览「最近进展」里每一版一条（`project_progress/feed.py` 的 `revised`，只算项目建好之后的版本）；项目里的 agent 开场带一段「题目」（`package.section_for_project`），是 `TRACKED_SECTIONS` 之一，版本一变，跑着的会话下一轮就读到新的版本号和改动。

### 起始代码 {#starter-code}

起始代码是题目资料库里名为 `起始代码.zip`（`package.STARTER`）的那一份：它有自己的登记接口（`PUT` / `DELETE /tasks/{id}/starter`），字节和材料走同一条分段上传（`POST /tasks/{id}/files/uploads` 起头）；材料清单不列它，材料接口碰到这个名字一律 422；替换照样留历史行，设上、换掉、删掉各记一版（`changes.starter` 是 `added` / `replaced` / `removed`）。发题页上题目还不存在，人选的压缩包留在页面上，发布之后再走这条上传（传不成时题目照样发布，提示到改题页重传）；芝士写进草稿的是人交给它的一份附件（`starterAttachmentId`，只认本人传的），随草稿发布。

压缩包在登记时就查一遍（`project/starter.py` 的 `check`），项目建好后做提交时（`build`）走的是同一个函数（`_entries`），所以登记时收下的，建项目时一定提交得上。提交不经过磁盘上的工作目录：每一项从压缩包直接流进 `git fast-import` 成为 blob，路径的长短与后台在哪个目录干活无关，压缩包里的东西也不会落到 git 自己的对象以外。规矩是：绝对路径、盘符、`..`、符号链接和其他非普通文件拒绝；`zipfile` 解不开的压缩方式（如 Deflate64）拒绝；每层名字超过 255 字节、整条路径超过 4096 字节、含控制字符（NUL 在内）的拒绝；其余路径问 git 自己（`update-index --index-info`，开着 `core.protectNTFS` 和 `core.protectHFS`），它不收的（`GIT~1`、`.git ` 这类 `.git` 的别名）拒绝，并说是哪一项；文件数、解开后的总大小有上限（`starter_max_files`、`starter_max_bytes`），解开后超过 64 MiB 且大于压缩包 `starter_max_ratio` 倍的当压缩炸弹拒绝；`__MACOSX/`、`.DS_Store` 和任何一层叫 `.git`（大小写不论）的路径跳过；只有一个顶层文件夹时去掉这一层；没标 UTF-8 的文件名按 UTF-8、GBK 还原；压缩包自带的 `.gitignore`（顶层的和各个文件夹里的）忽略掉的文件不进提交：进提交的正是 `git add -A` 从解开的文件夹里会提交的那些。由 git 自己判，不另抄一份规矩：只把这些 `.gitignore` 放进一个临时仓库的原位置，再用 `git check-ignore --no-index --stdin` 判每一条路径，被排除的文件夹里的文件不能再被放回来、开头的 BOM、多大的文件都和 git 一样（`tests/unit/test_starter_archive.py` 拿真的 `git add -A` 对照）；放不进临时仓库的 `.gitignore` 整份拒绝（`starterIgnoreUnreadable`），不默认把它列的文件放进去。分段上传登记时才合成一份，所以登记在一个保存点里先放进来、再查：按范围从存储读压缩包末尾的目录（`package._Stored`），不整份取回、不进内存；不合格的退回保存点并删掉这份字节。内容损坏（CRC 对不上、压缩数据坏了）要到项目建好后做提交时才读得出来：不管解压器报的是哪种异常，都是压缩包的事，记 `failed`（`starterNotZip`），不再重试；停掉 `fast-import` 时管道上的报错不会顶替它。

一道题怎么开始看 `task.start_mode`（`domain/task/inputs.py` 的 `START_DIRECT` / `START_APPLY`，判断在 `claims.starts_now`）。`DIRECT`：领取一写下就是通过的，同一个请求里就开始（建项目），截止时间是题目自己的 `deadline`，所有人一样；题目没有 `deadline` 就谁都没有截止。`APPLY`：领取等出题人通过，截止时间是通过那一刻加 `default_deadline` 天；通过不建项目，领题的人之后再开始。两种的截止时间都由服务端在通过时算（`claims.deadline_from`），领题的人发来的截止时间一概不收（请求体里没有这一格），个人的截止只由出题人在参与者路由上改；一份领取的截止时间一变（出题人单独改，或跟着题目的 `deadline` 走），它的完成状态当场重推（`refresh_completion_status`），清扫按旧截止判了失败的，延到以后又是没交；`DIRECT` 的题改了 `deadline`，还停在旧值上的已通过领取跟着改（`claims.follow_the_task_deadline`，在改题路由里），出题人单独设过的不动。迁移 `8103084f5e42` 把已有的题都设成 `APPLY`：此前每份领取都等出题人通过，`APPLY` 就是那时的行为。新题不说就是 `DIRECT`（`POST /tasks`、草稿发布、发题表单；列上的 `server_default` 仍是 `APPLY`，只给不写这一列的旧版本用）。有人持有一份还在的领取（通过的或等着的，`claims.has_live_claim`）之后，改题和草稿发布都不能再换开始方式（`taskStartModeLocked`），改题页上这一格灰着，读的是任务载荷里的 `participants.live`。数领取和改开始方式不交错：改开始方式先锁住题目那一行（`claims.hold_against_new_claims`，`FOR UPDATE`），每一份新领取在写下之前用 `FOR KEY SHARE` 重读开始方式（`claims.read_mode_for_claim`）；两者互相等，新领取彼此不等，也不和数名额的 `hold_places`（`FOR NO KEY UPDATE`）相撞。整道题的 `deadline` 过了，不收新的领取（`taskClosedForStart`，`claims.refuse_claim_after_deadline`）。开始看 `claims.refuse_start_after_deadline`：`DIRECT` 的题 `deadline` 是所有人的，过了也不再开始（`taskClosedForStart`）；`APPLY` 的题 `deadline` 是申请截止，只挡申请，出题人在它之后照样能通过，通过了的领取在自己的截止之前照样开始。一份通过了的领取自己的截止过了，不再开始（`claimDeadlinePassedForStart`）；已有项目的照常「继续做」。题目列表按 `lifecycle` 分组时，整道题截止了、看的人自己还有一份通过了且截止没过的领取（`APPLY` 的题，或 `DIRECT` 的题截止没过而只是名额满了），这道题对他算开着（`TaskRepository.still_open_to`），不收进「已截止」；前端同一条判据是 `utils/tasks.ts` 的 `openToMe`，标题旁和列表那一行据此不写「已截止」。

领一道自己（或自己的团队）已经领着的题不是错：`TaskMembershipService.create_membership` 答的就是那一份领取（等着通过的、已经通过的都算），所以「再试一次」拿到的是同一份；被拒的那份不算领着，可以重新申请；重新申请之后，被拒的那份就不能再被批准或撤回拒绝（`claims.has_newer_claim`，`claimHasNewerApplication`），出题人处理新的那份；这一查之前先拿同一把按成员的领取锁（`try_lock_claim`），正在写的新申请拿不到就回可重试的 409（`claimBeingChanged`），所以两边不会都成。名额满了（通过的份数到了 `participant_limit`），`APPLY` 的题也不再收新的申请（`taskPlacesFull`），已经在等的不动。整条领题路径有先后：路由里第一件写库的事就是用 `pg_try_advisory_xact_lock` 试着拿下这个成员（人或团队）在这道题上的领取（`TaskMembershipRepository.try_lock_claim`，到提交为止），拿不到就是同一个人或同一个团队的另一次领取正在进行，当场回一句可以重试的 409（`claimInProgress`，`ClaimInProgressError`，界面照服务端那句话说），不等它。这把锁不锁题目那一行：别人的领取写的行都引用题目那一行，锁它会让所有人排队；别的团队、别的题目的领取碰不到这把锁。`DIRECT` 的题领取当场通过，通过时要看的那几条（`_check_approvable`：名额、队伍人数、实名）在这里看；设了人数上限时，数名额和写下这份通过是一步，先用 `hold_places` 锁住题目那一行（`FOR NO KEY UPDATE`，到提交为止），出题人在参与者页签里的「批准」走的也是这一条。数据库里还没有约束挡住同一个成员在同一道题上有两份还在的领取（以前没有这把锁时并发留下的，见 #4824）；按成员读领取的地方（`TaskMembershipRepository.get_by_task_and_member`，`get_user_membership` 也走它）有重复时挑一份、不报错：先挑已通过的，再挑最新的，人和团队的 id 分开看。

「开始」就是用这道题新建项目，这是从题目建项目唯一的一条路，入口是 `app/api/task_claims.start_claim`：领题的几条路由（个人的、团队的，和 `POST /tasks/{id}/participants` 替自己领的那一种）在领取已通过时调它，也就是 `DIRECT` 的题当场、`APPLY` 的题通过之后领题的人再领一次时。出题人替别人报上（`POST /tasks/{id}/participants?member=`）只写领取，不建项目，那个人自己来开始。批准（两条 `PATCH` 参与者的路由）不建项目；领取在等的时候早先版本就建好的项目，批准时把题目的资源给它（`task_claims.release_to_claim_project` → `ProjectService.activate_participation`），之后从「继续做」进去。题目页对通过了却还没有项目的领取给「开始做这道题」，点了先弹开始的确认框，按「开始做」才发请求。导师在开始的那一刻问：要建项目而题目有导师时，请求要带 `agreeMentors`（`mentorConsentRequired`），这一刻题目上的导师记在领取上（`TaskMembership.agreed_mentors`），项目加入的就是这几位，之后才加到题目上的人不算；申请时不问。`DIRECT` 的新领取当场就开始，所以这一问放在写领取之前（`consent_before_a_direct_start`），不留一份占着名额却没有项目的领取。等着通过的领取没有项目。`start_claim` 先把调用方写下的东西（领取、占下的名额、同意的导师）连同个人领取的那个人的个人团队一起提交，再去建仓库：建仓库期间不占着题目那一行，也不占着正在建的团队，同一份领取的第二次请求在 `ProjectService.for_participation` 的那把锁（`ProjectRepository.try_lock_claim`，按这个团队在这道题上）上拿不到，当场回同样可以重试的 409，不排在它后面等。项目建在领取的那一方名下：个人的建在本人名下，团队的建在那个团队下，记在开始它的那位队员名下。拿到锁之后再看一次这个团队是不是已经有了项目，有就用它；没有就读这道题此刻的起始代码是资料库里哪一行、题目第几版（`TaskTerms.starter`），用一个随机的项目 id 在 Forgejo 上建好账号和仓库（`forge.prepare_repository`，有起始代码时是空仓库），建项目、登记仓库（`forge.bind_repository`），有起始代码时再记一行 `project_starter_pushes`（`pending`）。项目 id 随机、猜不到：由题目和团队推出来的 id 会撞上这个团队搬走、交出去的旧项目，也能被别人抢先用 `POST /projects` 占住。题目页的「继续做」（`myProject`）打开的是已经通过的领取已有的项目（`task_claims.project_of_claim`，只查不建）；还在等通过的领取不给，哪怕早先版本已经给它建了项目，页面只显示在等。`POST /projects` 不再接题目：请求体里带 `external_task_id` 的（部署前打开的旧页面）一律 400（`projectFromTaskByStarting`），不建项目。

后台任务「starter code pushes」（`task.starter_jobs.push_due`，每 10 秒）一次领一行，租约提交之后才动手，干活时不占事务。它先问仓库本身已经发生了什么（`ls-remote`，Forgejo API 的 `empty` 在推送之后一阵子才更新），再看压缩包：

- `main` 已经存在，而且上面有这一行记下的那个提交（`commit_sha`，`merge-base --is-ancestor`，拉 `main` 的历史时不取文件内容）：上一次推成了、回答没回来，记 `done`，不管压缩包后来怎样。
- `main` 已经存在，却没有那个提交（有写权限的人先推了别的）：起始代码没有放进去，记 `failed`（`starterMainTaken`），不说放好了。
- 还没有 `main`：按记下的那一行取字节（之后出题人换了压缩包，已经建好项目的人拿到的仍是建项目那一刻的那份；删掉了就是 `starterRemovedBeforePush`），以平台身份（`Cheese <starter@users.invalid>`）、建项目那一刻的时间做出提交，先把提交号记进 `commit_sha`，再不强制地推成 `main`，凭据放在请求头里（同 `forge_migration.push`）。

`done` 只在起始代码那个提交确实在 `main` 上时才记。压缩包本身成不了提交（读到一半坏了、出题人删了）或 `main` 被占，当场记 `failed`，`error` 是那句原因，不再重试；代码仓库服务一时没有回应，过一会儿再试（30 秒起、翻倍、最多 30 分钟），试满 8 次记 `failed`（`starterPushGaveUp`）。`GET /projects/{id}` 的 `starter` 说第几版、`pending` / `done` / `failed` 和原因，项目总览和 agent 的「题目」一段据此说起始代码在放、放好了，还是没放成。放好或没放成时，任务在项目推送上发一帧 `project`（`agent/project_feed.py`，由 `app/periodic_jobs.py` 接上），开着的页面重读项目那一行；推送没到时，页面在 `pending` 期间每 20 秒自己再读一次。没有起始代码的题，建仓库和从前一样由 Forgejo 初始化 `main`。GitHub 仓库的项目不建 Forgejo 仓库，也就没有起始代码。

## 边界与坑 {#traps}

- 「待回答」压过队友报的「在做」：它停在那个问题上等回答，列表上显示「芝士在做」正是让人不来看的那句话。
- 待回答只读问题上的回答、跳过记录：旁聊或提问 AI 继续发言都不结题。各任务视图共用寻址，包含多人提问、未点名时的任务参与者，并扣除已跳过的人。`awaits_me` 保留提需求人等关注提醒，个人称谓读 `needs_my_action`：只有当前实际被问、被指定审阅或需要处理问题的人，才写成「待你…」。显示单个人名的 `waiting_on` 不能代表多个被问者。
- `Task.model` / `effort` 今天**只有卡片渲染读，没有任何接口写**，任务会话的执行路径也不读它们。卡上显示哪个模型从 `usage` 里这条任务最后一行算出来（`presentation.card_model`），不存一列。
- 上一次检查点之后的改动只在那个沙箱里：沙箱换掉时它们就没有了；那次检查点的同步失败了也一样——现场里那条失败记录是唯一的信号。
