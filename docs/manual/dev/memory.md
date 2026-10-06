---
title: 记忆
kind: 流程
summary: 芝士的记忆是会话目录里一棵文件树：L1 索引每轮注入、L2 文件自己读、L3 原料不进来。这份文档讲树怎么分层、两个作用域各是谁的、写入与对账在什么时刻发生、上限与超限行为、整理（dream）与旧表迁移怎么跑，以及为什么从「条目池 + 关键词召回」换成这一套。
covers:
  - backend/app/domain/memory/files.py
  - backend/app/domain/memory/files_store.py
  - backend/app/domain/memory/tree.py
  - backend/app/domain/memory/session.py
  - backend/app/domain/memory/instructions.py
  - backend/app/domain/memory/models.py
  - backend/app/domain/memory/dream.py
  - backend/app/domain/memory/dream_prompt.py
  - backend/app/domain/memory/migration.py
  - backend/app/domain/memory/migration_service.py
  - backend/app/domain/memory/reads.py
  - backend/app/api/routes/memory_files.py
  - backend/app/api/routes/admin_memory.py
  - backend/scripts/memory_migration.py
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/harness/prompt.py
  - backend/app/domain/agent/harness/claude_code/runner.py
  - backend/sandbox/cheese
---

# 记忆 {#memory}

芝士的记忆是**会话目录里一棵文件树**：`.cheese/memory/` 下，一个作用域一个目录，一条记忆一个 markdown 文件。数据库是真相，会话里那一份是副本——每一轮输入之前铺下去，这一轮结束之后收回来。

这棵树在**会话机**上，在会话自己的家里（`~/.cheese/memory/`），不在执行机上：runner 在那里对账，而 agent 的文件工具（Read / Write / Edit）平时跑在执行机上，只有碰到这棵树的路径时留在会话机（`remote_execution/proxy.js` 的 `memoryPath`，守卫是 `client.py` 的 `own_memory`）。agent 按 `~/.cheese/memory/...` 写，或者按它 shell 里的 `$HOME`（那是执行机的家）拼出一个绝对路径，落到的都是这一棵。shell 看不到它，所以 `rm` 删不掉一条记忆：删一条就用 Write 把它写成空内容。runner 收树时把写空了的那条（索引除外，空索引就是一份空索引）当成会话删了，照样过批量删除那道闸，并把那个空文件从会话机上清掉（`runner.read_memory`）。

> 讲：分层、两个作用域、写入与对账的时机、上限与权限、整理（dream）、旧表迁移、正文读数。不讲：每个 `type` 该怎么写（那是 `instructions.py` 里那段散文）。

```demo-steps
title: 一轮里记忆怎么流转
note: 右下角「幕后」是会话目录里的记忆文件树；上面那格是真的现场
embed: memory
steps:
  - label: 输入之前：铺好、注入索引
    desc: 平台把库里这一份铺进会话目录，新会话的开场快照里带上 team/MEMORY.md 和本轮说话那个人的 private 索引。正文不注入，芝士要用时自己读。
    link: /dev/memory#scopes
  - label: 被纠正
    desc: 纠正的是做法倾向才值得记；纠正的是某一次的结果，改完就结束。
    link: /dev/memory#write
  - label: 写之前先查重
    desc: 表达习惯进说话人的 private，项目规矩才进 team。同一件事已有文件就改它，不新建副本。
    link: /dev/memory#write
  - label: 写一条：一个文件加索引一行
    desc: 新建一条记忆是新建一个文件、索引里加一行。索引一行不超过 150 字符，正文不超过 1000 字，超了存成 .rejected.md。
    link: /dev/memory#limits
  - label: 这一轮结束：收回会话、写回库
    desc: 会话改过的收回来写进库。两边都改了同一条时平台那一份赢，会话那一版存成旁路的 .conflict.md，并请它重读再写。
    link: /dev/memory#write
  - label: 房间里的那条事件
    desc: 有改动就留一条折叠的灰字事件，不点任何人的名。team 的改动说进「综合」，private 的改动说进那个人的私聊。
    link: /dev/memory#events
  - label: 下一次开场
    desc: 别人刚改的也在铺进来的那一份里。换一个人说话，注入的 private 索引跟着换成他的。
    link: /dev/memory#scopes
```

## 分层 {#layers}

三层，只有第一层进上下文：

| 层 | 是什么 | 怎么进到模型眼前 |
|---|---|---|
| L1 索引 | 每个作用域一份 `MEMORY.md`，一行一条记忆：`- [标题](文件.md) — 一句钩子` | **每轮注入**（`memory_index()` → `build_system_prompt`） |
| L2 记忆文件 | `<短名>.md`，带 `name` / `description` / `type` 三个 frontmatter 字段 | 注入了索引，正文让 agent **自己去读**（它有 Read） |
| L3 原料 | 这个会话里说过的话、跑过的命令、工具结果 | 不进记忆。它们是记忆的**来源**，不是记忆 |

`MEMORY.md` 本身没有 frontmatter，永远不写正文——它是目录，不是文件（`parse_index` / `fit_index`，`files.py`）。

**这一段只对会对账的骨架注入。** 说明书（`MEMORY_INSTRUCTIONS`）进系统提示词、L1 进开场快照，条件都是 `keeps_memory=True`（`build_system_prompt` 和 `build_session_opening` 各收一份），由调用方按当前骨架的事实传入（注册表的 `Harness.keeps_memory`，经 `session_host.host.keeps_memory` 读：Claude Code 是 `True`，codex、pi 是 `False`）。说明书写的是「写进 `~/.cheese/memory/`，下一轮平台那一份里有它」，而 codex、pi 没有这条回路——照它写下的文件永远同步不回来，agent 却以为自己在写项目记忆。

四类记忆（`type`）：`user`（这个人是谁、懂什么）、`feedback`（活该怎么干，含被认可的判断）、`project`（项目里正在进行的事，代码里读不出来的）、`reference`（外部系统的入口）。各自的 `scope` 规矩、正文怎么组织（feedback / project 要写 `**Why:**` 和 `**How to apply:**`）、什么**不**该写——整段在 `instructions.py` 的 `MEMORY_INSTRUCTIONS` 里，照搬 Claude Code 2.1.283 的 memory 段翻成中文，**原样进系统提示词**。

## 两个作用域 {#scopes}

| 作用域 | 前缀 | 是谁的 | 看得见的人 |
|---|---|---|---|
| team | `team/` | 这个项目所有人和所有芝士共看共写 | 项目成员 |
| private | `private/<handle>/` | **人 × 项目**：某一个人在这个项目里的那一份 | 本人 + 项目管理员 |

注入的规矩：**`team/MEMORY.md` 加本轮说话那个人的 `private/<handle>/MEMORY.md`**，不是所有人的（`files_store.memory_index`，`speaker_handles` 里只进 `names_a_person()` 认的人）。项目里的人可以很多，索引是每一轮都要付的预算，付在一个没说话的人的偏好上是白付。

这句话不是权限，是预算：`private` 的可见性是另一道闸，在 API 上（见下）。

## 写入、更新、删除 {#write}

**agent 自己写。** 它用手里那套文件工具（Write / Edit）改 `.cheese/memory/` 下的文件——新建一条记忆是新建一个文件加索引里一行，改一条是改那个文件（**先查重**：同一件事别新建副本），不成立的那条直接改掉或删掉，不写「更正：」。写入的规矩在系统提示词里（`MEMORY_INSTRUCTIONS`），不在工具的参数里：一条记忆的形状（文件名、frontmatter、索引行、≤150 字符）用一段散文说得清，用一个 `cheese_remember(fact, core)` 说不清——这正是换这套机制的原因（那个工具
已经撤掉了）。

**平台侧的对账两个时刻，一次一件事**（`ChatService._sync_memory`，挂在 harness 的两个钩子上）：

1. **输入之前**：把数据库这一份铺进会话目录。agent 一睁眼读到的就是平台现在这一份，别人刚改的也在里面。
2. **这一轮结束之后**：会话改过的收回来，写进 `memory_files`，并在房间里留一条折叠事件（带 diff）。

两次做的是同一件事，因为对账**幂等**：谁比谁新不靠调用点记，靠会话机上那份基线（`runner.MEMORY_BASELINE`，`$HOME/.cheese/memory/.baseline.json`）。**基线和这棵树同生同死**：它俩在一个目录里，会话的家被重建（`resource_cleanup` 会删掉它）时一起没了，于是「磁盘空、基线满」这个状态不会出现——真出现的话，读起来就是「这个会话把整棵树删光了」，而平台上那份团队记忆会被整批删掉、没有历史可以恢复。就算基线还在，也还有一道保险：一次对账里某个作用域要删的条数超过一半、而且超过 3 条时，取消这次删除、原样铺回平台的版本，并把拦下的路径写进 runner 日志（`tree.BULK_DELETE_RATIO` / `BULK_DELETE_MIN`，`TreeSync.held`）。批量删除是个信号，不是一步操作。

合成哪一份的规矩在 `tree.sync_tree`（纯函数，两侧跑的是同一段代码），一句话：**平台这一份赢冲突**。三种情形——会话没动过 → 用平台那一份；平台没动过 → 用会话那一份；两边都动了 → 平台赢，会话那一版**存成旁路文件**（`<名字>.conflict.md`，同一个目录，`runner._keep_refused`）并在现场记一条运行记录、给写它的 agent 留一句「重读再写」（见[改动记在哪](#events)）。旁路文件不在树里、不进索引、也不会被同步回库（`read_memory` 只收过得去 `check_scoped_path` 的 `.md`，那个名字带点，过不去），它只是留给写的人重读自己那一版的东西。**删除只在自己点过名的作用域里认**：这一轮没轮到的 `private` 会被从会话目录里收走，那是「收走」，不是「删掉」。

**正文没变就不写。** 每一次写入都推高 `version`，而版本号是冲突判据——每轮把整棵树推高一版，等于把这个判据作废（`session.apply_tree` 逐条比正文）。

## 上限与超限 {#limits}

**单条有上限，超了拒绝**（`files.limit_breach`）：

| 什么 | 上限 | 量法 |
|---|---|---|
| 一条记忆的正文（frontmatter 之后） | 1000 字（`BODY_MAX`） | 整条；改一条已经超长的记忆，要改到上限以内 |
| 索引里的一行 | 150 字符（`INDEX_LINE_MAX`） | 只量这一版新写的行；别人早先留下的一行长的不挡这一次 |

写的人手上就有这一条，当场就改得短，所以这两条在写入时拦。拦在两处：会话那一侧对账时（`tree.sync_tree` → `TreeSync.rejected`），那一版不收、平台那一版留着，会话写的那一版存成旁路文件 `<名字>.rejected.md`（`files.rejected_path`）；数据库那一侧 `MemoryFileStore.write` 再查一次（`MemoryFileLimit`，接口上是 422），挡住比这一版旧的会话和直接调接口的写入。必须在对账里拦，不能只在数据库拦：对账一旦收下，基线就记成了会话那一版，下一次对账会把平台的旧版静默地铺回磁盘。

**总长没有写入闸。** L1 索引 **200 行 / 25 KB**（`INDEX_MAX_LINES`、`INDEX_MAX_BYTES`）是注入预算：超了照样写，注入时按行截断，并在注入块里回一句警告（`fit_index` → `MemoryIndex.warnings` → `memory_block`）。删哪一条要看整个作用域，写的人手上没有这份信息，所以取舍交给整理。

- 截断发生在**读**的时候，所以警告跟着索引一起进上下文：读到一段短的索引却不知道它短了的人，会去改错地方。写入端（`/memory/files`）也会跑一次 `fit_index`，把它当 `warning` 还回去。
- 格式问题只回 `warning` 不拒绝：一条记忆文件的 frontmatter 读不出来（`name` 不是 kebab-case、缺 `name` / `description` / `type`、没有正文），和 `description` 超过 150 字符。这两种文件照写，只是把警告带回去（`api/routes/memory_files.py`）。

```demo-memory
title: 上限与超限：拖出来看
note: 左边拖，右边立刻说这一版会怎样。数字是构建时从 files.py 读出来的，不是抄在页面上的。
source: memory-limits
limits: INDEX_MAX_LINES, INDEX_MAX_BYTES, INDEX_LINE_MAX, BODY_MAX
```

三种上限量的是三件不同的事：索引整份超了只是**注入时截断**（写入照收），索引里新写的一行超了、正文超了才是**拒收**。上面每一个数都来自 `backend/app/domain/memory/files.py`，页面里那套算术（`src/memory-limits.mjs`）每次构建都和真的 `fit_index` / `limit_breach` 对一遍：对不上，构建失败。

## 权限 {#permissions}

`/memory/files`（`api/routes/memory_files.py`）：

| 操作 | 谁可以 |
|---|---|
| 读 / 写 / 删 `team` | 项目成员 |
| 读 `private/<handle>` | 本人；项目管理员也看得见（`MemberService.manages`） |
| 写 / 删 `private/<handle>` | **只有本人**，管理员也不行 |

别人的 `private` 答 **403**（不是 404）：「这里有一条，但不是你的」。**读得到的范围不等于写得动的范围**（`_readable` / `_writable`）：管理员看得见是为了出事时能查（比如说有人把密钥写进去了），不是为了替谁改——给他一支笔，那一条记忆就同时有了两个主人，而「这是谁的判断」正是 private 这一层唯一要保住的东西。路径最长 200 字符（`files.PATH_MAX`，和 `memory_files.path` 那一列的宽度一样），超了答 422。写入带 `version`，对不上就 409 并把当前那一版一起还回去——冲突是拒绝，不是合并：两版散文自动合并的唯一结果是两句互相矛盾的话安静地并排躺着。两个同时起手的新建同一个路径也会撞在唯一约束上，那一路同样答 409（对面赢了的证据就是那条约束）。

界面上的记忆面板（「项目文档 → 记忆」，`GET /memory`，`api/routes/memory.py`）读的就是这棵树：项目共享的那一份，加上读的人自己那一份。它是一条**给人读**的路——正文按能读的样子给（frontmatter 那三行剥掉），修剪走 `MemoryFileStore.forget`，连同索引里指着它的那一行一起删（只删文件的话，下一轮注入的索引里会挂着一条指向不存在文件的指针）。

## 改动记在哪 {#events}

每次真的改了东西，记成一条运行记录，**不进对话、不点任何人的名**（`memory_changed`，`platform_notices.memory_changed_notice`，`run_record`）：

- `team/` 的改动 → **写它的那段对话**的现场；
- `private/<handle>/` 的改动 → **那个人的私聊**的现场（没有就现开一间）；
- 整理（dream）改了 team 的哪几条 → 项目根房间「综合」的现场。

一条记忆是 agent 写下的一份观察，没有人欠它一个动作，所以它只在现场里，改动本身收进 `meta.detail`（统一 diff，按路径分段、每段上限 200 行）。两棵树分开记，因为读它们的人不是一批：把某个人的 private diff 记进一间多人的房间，等于把一个人的偏好广播给房间里的人。

写记忆的那个 agent 读不到运行记录——它在会话机上，它看到的世界就是那棵树。所以**有被平台盖回去的版本时，还要单独说给写它的 agent 一句 `agent_notice`**（`platform_notices.memory_conflict_notice`），落在**这次对账的那间房**、不露面（`in_room: false`）：`agent_notice` 只随 `blocks` 进本房间下一轮的 prompt，运行记录不进 prompt（`queries._say_memory_change`）。那句话：点名哪几条被盖了、它写的那一版在哪个 `.conflict.md` 里、请重读再写。不说，它下一轮写的还是同一版，而每一轮都会被盖回去。超了单条上限没收的那几条同理，`agent_notice` 里点名哪几条、为什么、没收的那一版在哪个 `.rejected.md` 里（`platform_notices.memory_rejected_notice`）。**这两句里的路径按 agent 那一侧的写法写全**（`~/.cheese/memory/<作用域>/<名字>`，`files.prompt_path`）：只写 `team/x.md`，它的文件工具会把这次读写发去工作机，那里没有记忆树，读回来是「文件不存在」。被盖回去的那一版是删除时没有正文可留（`runner._keep_refused` 跳过空内容），那种情况那句话只说「没有副本」，不指一个文件名。

## 为什么不是「条目池 + 关键词召回」 {#why}

上一版是「记忆 = 数据库里的一个个条目」，按 `<项目>:<agent>:<人>` 分池，core 层每轮注入、fact 层要用 `cheese_recall` 按关键词查。它有四个解不掉的问题：

1. **召回是一次查询，而查询是按需发生的。** 需要它的那一刻，正是 agent 觉得自己已经知道的时候——一条提示词里看起来已经满了，没有人会再去搜。
2. **关键词检索不是语义检索。** 它只能把问题切成词、按覆盖度排；一次空结果读起来和「这条记忆不存在」一模一样，于是 agent 会一头撞上那条记忆本来要拦住的坑。
3. **core / fact 两档是一条连续的判断，而它被交给了写的人。** 结果是要么什么都往 core 里塞（预算爆掉），要么一条都不塞（那一档等于没有）。
4. **写入的形状是一组工具参数**（`fact` / `core` / `everyone`）。参数能表达的只有「一句话」，而一条好记忆需要的是四种类型、正文结构、查重、什么不该写——这些只能是一段规矩加一个能写文件的手。

所以照 Claude Code 2.1.283 换成了现在这套：**索引进上下文，正文在文件里，检索变成「看得见的列表 + 自己读」**。文件树跟会话走、跟项目走，谁写的、什么时候写的、改了哪一版都留在 diff 里。

同一个方向上的收尾：`cheese_recall` 命令撤了，`/projects/{id}/memory/search` 那条路由撤了，`memory/pools.py` 删了，关键词切分（`memory/keywords.py`）只剩一个调用方——写记忆之前拿最重的几个词去检出目录里查一遍（`memory/redundant.py`）。旧表 `memory_entries` 不 drop：个人主页上「芝士对我的认识」那一栏读的还是它，而记忆面板已经改读这棵树。

## 整理（dream） {#dream}

人不会记得去整理记忆，所以整理自己发生。**触发判据是这个项目最近花了多少**——记忆是会话的副产品，最近写得越多，它越可能已经乱到值得梳一遍。两个条件都满足才跑：

| 条件 | 默认 | 在哪配 |
|---|---|---|
| 自上次整理以来累计的 `resource_usage.output_tokens` | 10,000,000 | `project.settings["memory_dream"]["threshold_output_tokens"]` |
| 距上次整理至少 | 4 小时 | `project.settings["memory_dream"]["min_interval_hours"]` |

两个数写进项目设置而不是散在代码里，因为它们量的是**这个项目**的节奏：一个一天到晚在跑的代码项目和一个一周动两次的文档项目，同一个数没有意义。

- **不算 dream 自己花的。** 聚合按前缀剔掉 `kind = memory_dream` 的用量（`DREAM_KIND`）。网关的用量是延迟落库的，一条晚到的、时间戳落在新窗口里的 dream 用量只有 kind 认得出来；`last_dream_at` 在收尾时推进到整理结束的那一刻，两件事分开做。
- **按项目一把锁。** 锁是 `memory_dream_states.claimed_at` 上的一次比较并交换（`dream.claim`），抢不到就跳过这一次，不排队——下一次巡检很快就到，排队只会让一个项目的整理堆成队列。
- **跑在已有的巡检上**，不新开调度器：`PeriodicRunner` 里那条 `sweep_memory_dreams`（每 `settings.memory_dream_sweep_interval_s`，默认 600 秒）挨个项目问一句该不该，**串行**跑（一次整理是一轮真会话，可能几分钟）。
- **这一轮删得太多就先当它没删。** 一次整理删掉某个作用域一半以上、且超过 3 条，判为「这不像是整理，更像是那棵树出了事」：整轮作废、平台上一条都不少（`dream.removal_refused`，数值和会话侧那次对账共用一份 `tree.BULK_DELETE_*`）。批量删除是个信号，不是一步操作。
- **拒绝也记一次账。** 拒绝执行的那次照样推进 `last_dream_at`：不推进的话下一次巡检立刻再跑一遍，一个坏掉的树会把 token 烧在一遍遍重复的拒绝上。拒绝本身记在 `memory_dream_runs`（`status=refused`），给人看。

**怎么跑。** 派法是 `platform_work` + `send`：跑在这个项目**默认芝士**的会话上，用它自己的模型。读进来的是 team 和每个人的 private 的 L1 索引加 L2 正文、有新增对话的房间的记录与活文档、是代码项目的话还有仓库。工具只有只读的那些，加一只能在记忆目录里写和删的手。提示词照搬 Claude Code 2.1.283 的 dream 段（`strings` 从二进制里取出来，见 `dream_prompt.py`），翻成中文、按芝士的量纲改过：四段（Orient / Gather / Consolidate / Prune-and-index）、团队记忆那一段、以及「拿记忆和 `CLAUDE.md` 对一遍」都在。最后这段只给代码项目，由它自己在仓库里找 agent 会自动读进来的说明文件（`CLAUDE.md`、`AGENTS.md`、`.claude/rules/` 一类），一份都没有就跳过。**两条规矩写死**：private 的内容永远不许升级进 team；和说明文件冲突时只做标注，不改说明文件。

**结果。** 写下去的就是普通的记忆文件，走 `memory_files` 那条路（版本、冲突、房间事件都一样）。收尾时在**「综合」**发一条折叠事件，只列 team 里改动的文件，**不点任何人的名**，然后把判据那个计数器归零。private 的文件名和整理的人写下的那段交代都不进总览：总览全项目都看得见，而那段交代是看着所有人的 private 写的；它们留在 `memory_dream_runs`（`files`、`summary`）。拒绝执行时也一样，总览只说「这一次没做」，拦下的是哪几条记在那一条运行记录里。

同一间房的两场对账排队跑（`ChatService._sync_memory`）：整理那一轮结束时，轮次钩子和整理收尾各要对一次账，交错时后一场读到的是前一场提交之前的数据库，整理算出的「改了哪些」就会是空的。

## 旧表迁移（`memory_entries` → 文件树） {#migration}

换文件式记忆之前，记忆是 `memory_entries` 里一条条独立的句子，按 `<项目>:<agent>` 和 `<项目>:<agent>:<人>` 分池。这次迁移把它们搬进上面那棵树。**这是一次性的，而且只搬一次**：搬完新树就是我们读的那一份。

**来源三处，一条都不许漏：**

1. `memory_entries` 里 `scope = agent_project` 的活条目（那个 agent 在项目里学到的）；
2. 同一张表里 `scope = user` 的池（某个 agent 关于某个人的认识）；
3. 项目总览文档里的「大家都该知道的」和「项目记忆（由记忆整理迁入）」两节。

**去处五选一，每条都有：** `team`（新建一条全项目共读的）、`private/<handle>`（新建一条只属于某人的）、`merge`（并进一条已经存在的记忆）、`suggest`（只建议写进 `CLAUDE.md` / `SKILL.md`，**一个字都不自动改**）、`discard`（不值得变成记忆，但理由要写出来）。去处由模型判（它读得到正文，这是「进 team 还是进某个人的 private」唯一的判据），判完的结论和报告一起**存下来**——人复核的是那一份，落笔时重放它，不问第二次模型。

**先报告，人点头，才写。** 三步，中间那步是人：

| 动作 | 命令 | 做了什么 |
|---|---|---|
| `dry-run` | `uv run python -m scripts.memory_migration --project <项目名或 id>` | 读旧记忆、问模型、存一份计划，**新树一个字都不写**；报告发进项目的根房间「综合」 |
| `approve` | 同一个脚本 `--plan <id> --approve` | 复核人（`settings.memory_migration_reviewer`，一个人）点头 |
| `apply` | 同一个脚本 `--plan <id> --apply` | 按那份计划写进 `memory_files`，一次事务，一条冲突就整次不写 |

接口是同一个东西的另一条路（`/admin/memory/migration/...`，门是 `PlatformAdminDep`），留着是为了复核和落笔能在一个页面上点；`dry-run` 要读整个项目的旧记忆再问一遍模型，几分钟起步，走脚本比走 HTTP 合适。

报告上的每一条是「哪一条旧记忆 → 去哪儿 + 一句话理由」，加上要写的新文件的正文预览。两条硬规矩在 `migration._check` 里，不成立时**整份计划不成立**、不是跳过那一条：

- 从「关于某个人」的池子来的条目，去处不能是 `team`（private 的内容不许升级成全项目的规矩）；
- `user` 这个 `type` 只出现在 private 里。

落笔前还会核一次旧记忆的指纹（`sources_digest`）：复核那几分钟里旧表被谁改过，这份报告描述的就已经不是现在的旧表了，重跑 `dry-run`。`MemoryFileStore.write` 那一关管另一半——复核之后有人改过那棵树，写下去就是覆盖他的改动，那里 409，整次都不写。

**旧表在这条路上是只读的**：搬完不删、不改、不标记，`apply` 对它只有一次 `SELECT`。删表是另一个迁移，等搬完看一阵（30 天）再做。已经搬过的 `source_id` 记在计划里，第二次 `dry-run` 不会再搬一遍。

这次迁移的另一个目标是 **team 的 L1 索引回到 ≤ 120 行**（`migration.TEAM_INDEX_GOAL_LINES`）。那是**目标不是闸**：超了照样出报告，但报告上会红着写出来——压不回去这件事得让人看见。上限本身是 200 行（`INDEX_MAX_LINES`），那是「读不读得到」的线。

## 正文到底被读过几次 {#reads}

索引每轮注入，正文要 agent 自己去读一个文件——这整套机制成立的前提就是**它会去读**。所以有一个数：`Read` 打到 `.cheese/memory/` 下的、不是 `MEMORY.md` 的那些调用，**按项目、按天**（`domain/memory/reads.py`）。

数的是事件块（`blocks.kind = 'event'`，`meta.tool` / `meta.detail`），所以没有新表、没有新迁移，历史是免费的。判据用 `meta.detail`（**未剪裁**的参数原文）而不是 `meta.arg`：后者是给人看的预览，长路径会被剪成 `…/team/x.md`，拿它判目录不准。天按 **UTC** 切（`platform_stats.windows.utc_day`）——单参数的 `date_trunc('day', timestamptz)` 按会话时区切天，而部署的会话时区不一定是 UTC（本机是 `Asia/Shanghai`），那会把每天的边界挪几小时，还会让返回的日期和实际分桶的那条边界差一天。

查：`GET /admin/memory/reads?days=7&project_id=…`（默认七天）。它回答的是「有没有人翻开」，不是「有没有用上」——读了没读懂照样 +1，要回答后者得看别的东西。试点要回答的问题是「一周下来是不是接近 0」；真是 0 的话，下一步是让索引行本身更有信息量，或者把最常要用的几条正文也放进注入预算，不是回去做关键词召回。
