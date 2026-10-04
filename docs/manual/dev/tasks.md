---
title: 任务与工作目录
kind: 参考
summary: 一条活 = 一张任务卡 + 一个分支 + 一个工作目录，以及同一条规则算出来的看板与待处理清单。
covers:
  - backend/app/domain/room_task/
  - backend/app/api/routes/awaiting.py
  - backend/app/api/routes/git_http.py
  - backend/app/domain/delivery/addressing.py
  - backend/app/domain/task/attachment_service.py
  - backend/app/domain/task/visibility_service.py
  - backend/app/api/routes/tasks/
  - backend/sandbox/cheese
---

# 任务与工作目录 {#tasks}

一条活 = 一张任务卡 + 一个分支 + 一个工作目录；看板上的每一格和「待我处理」里的每一行，都是同一份规则算出来的。

> 讲：一条活由哪三件东西组成、状态怎么变、机器上的工作目录怎么来、同步与备份、看板的列和短语。不讲：交付链路本身（分支→PR→采纳合并，见[任务 → 分支 → PR → 验收合并](/dev/delivery)），验收卡与合并态（见[验收与采纳](/dev/accept)），看板读的其余几块（见[看板](/dev/boards)）。

## 三件东西 {#anatomy}

`cheese_task` 开一条活时造出第一件，另外两件是**算出来的名字**，不是另开的记录：

| 东西 | 在哪 | 谁造的 |
|---|---|---|
| 任务卡 | `tasks` 表一行（`room_task/models.py` 的 `Task`） | `TaskService.open_thread` |
| 分支 | `task/<id 的前 8 个 hex>`（`Task.branch_name`） | 同一个函数，从 id 算 |
| 工作目录 | 执行机上 `$CHEESE_WORKTREE_ROOT/<task_id>`（默认 `/work/<task_id>`） | `cheese worktree`，在机器上建 |

`workspace_name` 是同一个 id 的另一种写法（`task_<8 位 hex>`），给人看的名字。三个名字都从卡的 id 推出来，没有第二个来源；`thread_label.py` 对这个形状有一句说明：存一列就等于同一件事有两份声明，而它们对不上的那天没有任何地方能说出哪份是对的。

一条活**只属于一个房间**（`Task.room_id` 指向 `topics`），活不嵌套——「这条活的活」是同一个房间里的另一条活。卡上另外两组人：`owner_handle` 是谁的活，`reviewer_handle` 是谁说它可以落地；验收人在**派活那一刻**解析并写死（显式 `--reviewer`，否则取项目设置 `branch_protection.default_reviewer`，见 `project/protection.py`），不在递卡时回头读设置——设置是会变的政策，而「这条活交给了谁」是那一刻的事实。

## 状态只有两个 {#status}

`TaskStatus` 是 `open` / `closed`，只有这两档，因为只有这两档是可观察的：一条活要么在做，要么不做了。更细的词（阻塞 / 评审中 / …）会是一个没人写、也没人照着它做事的词。

三个时间戳各说各的事，不互相顶替：

| 列 | 什么时候写 | 写完之后 |
|---|---|---|
| `accepted_at` | 采纳（那次合并成功） | 交付标记，和 `status` 无关；批准被撤销也不抹掉 |
| `delivered_head` | 同上，记下合进去的那一版 | 与 `accepted_at` 一起构成「已交付」 |
| `closed_at` | `cheese_close_task`，或采纳后的自动关闭 | `status` 变 `closed` 的**时刻**，独立于「是不是 closed」 |

所以有两条容易读错的组合：一条活可以**已交付却还开着**（有人继续往同一条分支推），也可以**关掉却什么都没交付**（显式放弃）。看板把「已交付」排在最前面，正是为了前一种（`presentation.task_presentation` 的第一条规矩）。

## 线程标识 {#thread-label}

开卡时平台能报出这条活的线程标识 `work-<32 位 hex>`（`thread_label.thread_label`，从卡 id 算）。agent 起子 agent 时把它原样写进那段 prompt，骨架让它出现在这条子线程的每一个事件上，平台照着它把事件归到卡（`TaskService.open_by_thread_label`，每个事件问一次，走主键读）。

归属是**读出来的，不是报上来的绑定**：一个从没报过的子线程，它的事件仍然带着标识。前缀 `work-` 必须在——骨架自己起的内部子 agent 带的是它自己的词（`general-purpose` 这种），没有前缀就分不出两者，会去认领别人的卡。标识在文本里按形状挑（`label_in_text`，取第一个）：简报里顺带提到别的卡是常事，而形状卡到全长 hex——认错一张卡比认不出更糟。

`subagent_id` 是另一件事：那是**谁在做**，只有在容器里诞生的那个 id 开工时才写（`note_worker`），同时盖一次 `last_turn_at`。它不参与归属。

## 工作目录怎么来 {#worktree}

`cheese worktree <task_id>`（`backend/sandbox/cheese`）是机器上唯一建工作目录的地方：

1. `POST /projects/{project_id}/git/tasks/{task_id}`——这一下**才是**把 `author_handle` 写上的地方（`TaskService.record_author`，只认第一个）。派活和房间成员都不证明是谁在干活。房间不对答「这条任务不属于当前房间」，已结束答「这条任务已结束，请创建新任务」。
2. 在 `~/.cheese/repositories/<project>.git` 造/复用一个裸仓，凭证由 `!cheese git-credential` 现取；`fetch --filter=blob:none` 只取提交不取历史 blob（注释里记着实测：同一份仓库整下 195 MB、这样 9.6 MB；一个 169 MB 的 fetch 曾冻住后端 3.7 秒）。历史仍在，`git log` 和与基准的 diff 照常，某个文件的旧内容真被读时才取。
3. `git worktree add` 到 `/work/<task_id>`，起点是远端分支（还没开出来就是基准分支）。
4. 写两个钩子：`post-commit` → `cheese sync`，`prepare-commit-msg` → `cheese git-attribution`。钩子装在裸仓上，同一裸仓的每个工作目录都会跑它们，包括在任务目录旁边用 `git worktree add` 另开的那种；只有带任务标记（`cheese-task.json`）的任务目录才署名、才同步，其余目录照普通 git 提交，不碰任何任务。

两处守卫写在注释里：创建期要拿 `<project>.lock` 串行化（本地子 agent 会并发要工作目录，一个被打断的 clone 会被当成完整仓库）；这台机器上有 `~/.cheese-environment/config.json` 却找不到准备脚本时**直接失败**——静默跳过会交出一个依赖从没装过的工作目录。

## 同步、备份、恢复 {#sync}

`cheese sync` 做两件事，顺序不能换（`_sync_task`）：

1. **备份未提交的文件**：用另一个索引文件（`GIT_INDEX_FILE=…/cheese-snapshot-index`）`read-tree` + `add -A` + `write-tree`，把树写成一个挂在当前 HEAD 上的临时提交（`commit-tree`）。工作树自己的暂存区不受影响。
2. **推 HEAD**（`_deliver_task_branch`）：HEAD 已在本检出上次推送或 fetch 时记下的分支尖（`refs/remotes/origin/<branch>`）里，就不问托管平台、什么都不推：托管平台一时连不上时，问一句只会把它已经有的工作报成「没能推回」，而且每次 sync 都报一遍。否则先读托管平台上这条分支的尖。HEAD 已在其中——同一任务的另一个检出走得更远——就什么都不推；尖在 HEAD 的历史里就普通推送；两边分叉而那个尖是本检出自己在这条分支上有过、后来 amend/rebase 掉的提交，就以它为 lease 替换（`--force-with-lease`），整理本任务的提交（比如移除依赖）本来就是工作的一部分；尖里有本检出从没有过的提交就拒绝，替换会把别人的工作从 PR 上删掉。

任务的 PR 在合并队列里时（任务数据里的 `merge_queued_pr`），托管平台锁着这条分支：有新东西要推就不推，只备份，房间里说清楚这些提交为什么不在 PR 里——重试推不上去，合并后在新任务里交付，或由验收人退回、PR 撤出队列。

任务已结束时只做第 1 步：不推，也不看检出当前在哪个分支——结束后被拿去干别的（切到别的分支、detach）的检出，里面的东西照样进备份。备份失败时 `cheese sync --all` 那一行写成「已结束的任务 X 同步失败」，换工作电脑时平台按它区别对待（见 [turn.md](turn.md)）。机器自己不为已结束的任务往房间发「改动没能推回」：它没有哪一轮的改动丢了，换机时是哪条任务、为什么留在原来那台上，由平台那一条提示说。

备份和推送各自遇到连接被重置或超时，就在同一次 sync 里再试一次；连着两次才算失败——一次重置是网络，连着两次多半是这个请求本身（大小、中间代理的限制），再试只会拖长换机时那两分钟的推送。

备份不是每次都往对象存储打——`_backup_task_snapshot` 先看 `rev-list --count <snapshot> ^<base>`，是 0 说明每个对象都已经在托管平台上，就地返回；再问一次后端这条任务最新那份备份（`GET …/snapshots/latest`），它的 head 和文件树跟现在一样，也不再发——快照提交每次现做，不问这一句，没动过的已结束任务每次 sync 都会重新打包上传一遍，换机时的推送就等着它们。问不通、还没有备份、本机已经没有那份快照，都照常上传。叠在另一条任务分支上的任务，底座分支合并后会被删掉（这里 fetch `--prune` 也跟着删），这时改成扣掉 `--remotes=origin`：托管平台任何一条分支上见过的提交都不进备份。真要备份的，进 `refs/cheese/snapshots/<task_id>`、打成 bundle、随 `PUT /projects/{id}/git/tasks/{id}/snapshots/{sha}` 交给后端（`room_task/snapshots.py` 的 `save`），落进**私有** bucket（`task-snapshots/<project>/<task>/<sha>/<digest>.bundle`），单次上限 512 MiB（超了回「请将大文件移入附件存储」），服务端按 sha256 复核 digest、并校验它真是个 git bundle。同一份内容再交一次不会再存一遍；它要是已经不是最新那份（文件改动过又改了回去），就新记一行指向原来那个对象，让它重新成为最新——否则恢复出来的是那次已经撤掉的改动，下一轮 sync 也会因为「最新」对不上而每次都重传。bundle 每次现打，上传完不论成败都删：底座会前进，留下一份被拒的原样再发，只会每轮都被拒。

同步失败**会在房间里说一句**（`_report_sync_failure`）：一轮结束时改动还在机器上，和一轮成功长得一模一样——这正是「一次被拒的推送被读成了一个完成的回合」的由来，直到机器被回收、改动跟着没了。成功不发消息，那会是训练人跳过它的噪音。

`cheese recover <task_id>` 把最近一次备份恢复到 `~/.cheese/recovered/<task>-<snapshot 前 12 位>`，**不动原工作目录和评审分支**；bundle 的 sha256 对不上就一个文件都不恢复。备份缺的历史（bundle 头里的前置提交）按提交 id 从托管平台取，不经底座分支：叠放任务的底座合并后就被删了，而一台从没检出过这条任务的机器除了提交 id 没有别的可取。合并过的 PR 在托管平台上留着它的头，这些提交还在。

## push-fix {#push-fix}

`cheese push-fix [--task <id>] [--drop-dependency]` 先跑一次 sync，再调 `POST /topics/{topic}/tasks/{task}/push-fix`，把新提交刷到这条活**已有的**那个 PR 上并刷新验收状态；没有可推的东西就打印原因，不报错。`--drop-dependency` 用在「已经整理并验证是独立改动」之后：把现有 PR 改到项目默认分支，清掉任务依赖和旧批准。

一个任务只有一个 PR（`Task.pr_number`），改验收卡不会新开 PR，`push-fix` 推的还是同一个。

## 看板与待处理清单是同一份规则 {#awaiting}

两处显示状态**都不存库，读的时候从已有事实算**，而且只在后端算一次（`room_task/presentation.py`）。这条逻辑以前在前端是两份 TypeScript，同一句规矩写两遍就会走散；而 CLI、通知和以后的任何客户端问的是同一个问题，它们读不到那两份。这一层是纯函数：没有 I/O，不碰 session，不读时钟——「现在几点」是参数。

列回答的不是「进行到哪一步」，是「**该谁动**」：

| 列 | 意思 | 允许出现的短语 |
|---|---|---|
| `building` 施工中 | 还没递出交付 | 运行中、已动工、待开工、已交回、空闲（房间）、草稿（房间）、失联 |
| `delivering` 交付中 | 下一步在平台 / 芝士手上 | 检查运行中、等待检查、修复检查、解决冲突、平台更新分支 |
| `needs_you` 待处理 | 下一步在人手上 | 检查未通过、待审阅、已退回、待回答 |
| `done` 已完成 | 已采纳，或已关闭且没交付 | 已采纳、已关闭 |
| `archived` 已归档 | 房间才有；活不归档 | 已归档 |

同一个客观事实会因为「谁负责下一步」落在不同列：CI 红了但平台已经派芝士去修是 `delivering`（显示「修复检查」）；芝士推不上去、那个红没人能清掉就是 `needs_you`（显示「检查未通过」）。列**从短语推出来**（`_show`：每个短语是它那一列专属枚举的成员，列由成员的类型查表得到），所以「显示了一句不属于本列的话」在结构上写不出来。

「待我处理」那份清单（`api/routes/awaiting.py` + `room_task/awaiting.py`）读的是**同一个** `task_presentation` / `room_presentation`，差别只有范围：看板是一个项目内全部，清单是跨项目、且点到我的那些。它不能从通知表拼出来——通知是一条条事件记录，卡被驳回、作废或别人先处理掉了，那条记录还躺着，而从它身上读不出已经不作数了；一份「现在还没处理完的有哪些」必须从当下的事实重算。

收件人判据也只有一份（`delivery/addressing.py` 的 `address()`，通知投递读的也是它），五种关系：验收人（卡是递给他的）、提需求的人（他等的东西有了结果）、被问的那个人（芝士停在待确认问题上，只有他能回答）、设备的主人（有 agent 开始在他登记的机器上干活）、公告的读者（空间发了一条公告，发布那一刻空间里除作者以外的每个人）。一件事从「平台手上」转入「参与者手上」的那一刻通知那一个人，一次；平台手上的事（含「已完成」「已归档」）谁都不通知。

## 题目附件 {#task-attachments}

活旁边还有这一类附件：出题时带在题目上的材料（`domain/task/attachment_service.py`）。三条判据各自一句话：

| 判据 | 谁 |
|---|---|
| 谁能传、谁能删 | 出题人本人，或这块板的所有者 / 管理员（`may_teach_task`） |
| 谁能看到清单 | 看得见这道题的人 |
| 谁能下载 | 出题人 / 板管理员 / **已经领取的人**（`is_participant`） |

清单绝不比题目本身更保密——看不见材料就无法判断要不要领；下载比「看得见」窄一格、比「管得了」宽一格——领取者拿不到材料就没法做题。清单接口（`GET /tasks/{id}/attachments`）先过题目详情那三道闸（`_ensure_task_readable`），不是服务里那条更宽的 `can_view_task`：后者在题目没开可见范围时对任何登录用户都放行，会让未审批（403）和超出板上限（404）的题把材料清单交出去——文件名常常就是答案，「有没有清单」本身就是那道题的探针。返回里带 `canDownload` 标志，前端据此显示「下载」还是「领取这道题之后才能下载」。

写侧三道校验收在 `ensure_attachable` 里，因为**附件 id 是可猜的连续整数**：必须存在、必须是本人传的、且还没挂在别处——不校验的话，任何登录用户都能把别人上传的文件（例如别人交作业附的材料）挂到自己的题目上公开出去。建题时一次挂上（`POST /tasks` 的 `attachmentIds`）而不是先建题再补传：后者会在两步之间留一道缝，别人先看到一道没有材料的题，作者还得回去补。

取消挂载是软删关联行，存储上的对象留着——同一个文件可能还被别处引用（交作业、素材库走同一张 `attachment` 表），而对象删掉没有回头路。

## 边界与坑 {#traps}

- 「失联」只在**没人知道那个分身还在不在**的时候说话，退回时间戳，宽限期 10 分钟（`LOST_SIGNAL_AFTER`，实测轮内 block 间隔中位数 8 秒、p90 34 秒）。知道的有两处：跑轮次的进程（`ChatService.worker_live`）说它收工了就是收工了——那是缺席的证据，立刻算失联；说它还在做就是在跑。进程那处会忘，忘的三种情形都和分身死没死无关（后端重启、房间换一个会话、分身交回一次话），所以还有第二处：这条活**开工那一轮还开不开**（`Task.execution_turn_id`，见 `agent.liveness`）——关掉区间的是孤儿扫描，它知道容器真的死了没有。两处都答不出来才退回时间戳。一个埋头跑四十分钟长命令、一条 block 都不落的分身是正常干法——安静不是证据，缺席才是。
- 「待回答」是唯一会**中断运行**的一格，它压过「运行中」——看板显示「运行中」正是让人不来看的那句话。
- `has_progress`（「已动工」的判据）只读平台看得见的两个痕迹：`pr_number` 非空或 `author_handle` 非空。主 agent 自己动手那条路平台看不见过程（提交直接推去托管平台、工作树在沙箱里自己开），所以这一位只够把「做了一半停着」和「还没人碰过」分开，不够说明「正在做」。
- `Task.model` / `effort` 今天**只有卡片渲染读，没有任何接口写**：平台还没有「派活」这条路径，一条活是房间会话里的子 agent，由 agent 自己起。卡上显示哪个模型也从 `usage` 里这条活最后一行算出来（`presentation.card_model`），不存一列。
- 备份与工作目录都绑在**那一台机器**上：机器被回收而同步没成功，改动就没有了——房间里那句失败通知是唯一的信号。
