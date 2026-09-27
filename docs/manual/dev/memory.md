---
title: 记忆
kind: 流程
summary: 芝士的记忆是会话目录里一棵文件树：L1 索引每轮注入、L2 文件自己读、L3 原料不进来。这份文档讲树怎么分层、两个作用域各是谁的、写入与对账在什么时刻发生、上限与超限行为，以及为什么从「条目池 + 关键词召回」换成这一套。
covers:
  - backend/app/domain/memory/files.py
  - backend/app/domain/memory/files_store.py
  - backend/app/domain/memory/tree.py
  - backend/app/domain/memory/session.py
  - backend/app/domain/memory/instructions.py
  - backend/app/domain/memory/models.py
  - backend/app/api/routes/memory_files.py
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/harness/prompt.py
  - backend/app/domain/agent/harness/claude_code/runner.py
  - backend/sandbox/cheese
---

# 记忆 {#memory}

芝士的记忆是**会话目录里一棵文件树**：`.cheese/memory/` 下，一个作用域一个目录，一条记忆一个 markdown 文件。数据库是真相，会话里那一份是副本——每一轮输入之前铺下去，这一轮结束之后收回来。

这棵树在**会话机**上，在会话自己的家里（`~/.cheese/memory/`），不在执行机上：runner 在那里对账，而 agent 的文件工具（Read / Write / Edit）平时跑在执行机上，只有碰到这棵树的路径时留在会话机（`remote_execution/proxy.js` 的 `memoryPath`，守卫是 `client.py` 的 `own_memory`）。agent 按 `~/.cheese/memory/...` 写，或者按它 shell 里的 `$HOME`（那是执行机的家）拼出一个绝对路径，落到的都是这一棵。shell 看不到它。

> 讲：分层、两个作用域、写入与对账的时机、上限与权限。不讲：整理（dream）和旧表迁移，见后续。

## 分层 {#layers}

三层，只有第一层进上下文：

| 层 | 是什么 | 怎么进到模型眼前 |
|---|---|---|
| L1 索引 | 每个作用域一份 `MEMORY.md`，一行一条记忆：`- [标题](文件.md) — 一句钩子` | **每轮注入**（`memory_index()` → `build_system_prompt`） |
| L2 记忆文件 | `<短名>.md`，带 `name` / `description` / `type` 三个 frontmatter 字段 | 注入了索引，正文让 agent **自己去读**（它有 Read） |
| L3 原料 | 这个会话里说过的话、跑过的命令、工具结果 | 不进记忆。它们是记忆的**来源**，不是记忆 |

`MEMORY.md` 本身没有 frontmatter，永远不写正文——它是目录，不是文件（`parse_index` / `fit_index`，`files.py`）。

**这一段只对会对账的骨架注入。** L1 加说明书（`MEMORY_INSTRUCTIONS`）进系统提示词的条件是 `build_system_prompt(keeps_memory=True)`，由调用方按当前 runtime 的事实传入（`AgentRuntime.keeps_memory`：Claude Code 是 `True`，codex、pi 是 `False`）。说明书写的是「写进 `~/.cheese/memory/`，下一轮平台那一份里有它」，而 codex、pi 没有这条回路——照它写下的文件永远同步不回来，agent 却以为自己在写项目记忆。巡检和一页纸总结那两轮自己传 `False`：它们不是某个人的会话，那两段跟它们做的事无关。

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

合成哪一份的规矩在 `tree.sync_tree`（纯函数，两侧跑的是同一段代码），一句话：**平台这一份赢冲突**。三种情形——会话没动过 → 用平台那一份；平台没动过 → 用会话那一份；两边都动了 → 平台赢，会话那一版**存成旁路文件**（`<名字>.conflict.md`，同一个目录，`runner._keep_refused`）并在房间里说一句「重读再写」。旁路文件不在树里、不进索引、也不会被同步回库（`read_memory` 只收过得去 `check_scoped_path` 的 `.md`，那个名字带点，过不去），它只是留给写的人重读自己那一版的东西。**删除只在自己点过名的作用域里认**：这一轮没轮到的 `private` 会被从会话目录里收走，那是「收走」，不是「删掉」。

**正文没变就不写。** 每一次写入都推高 `version`，而版本号是冲突判据——每轮把整棵树推高一版，等于把这个判据作废（`session.apply_tree` 逐条比正文）。

## 上限与超限 {#limits}

L1 索引：**200 行 / 25 KB**（`INDEX_MAX_LINES`、`INDEX_MAX_BYTES`），超出的部分按行截断，并在注入块里回一句警告（`fit_index` → `MemoryIndex.warnings` → `memory_block`）。

- 截断发生在**读**的时候，所以警告跟着索引一起进上下文：读到一段短的索引却不知道它短了的人，会去改错地方。写入端（`/memory/files`）也会跑一次 `fit_index`，把它当 `warning` 还回去。
- **超限的写入照样成功**，只是每次注入都带一句「超出部分读不到，请压缩」——写入被拒绝意味着 agent 的心智模型和磁盘上的东西开始分叉，那比一份太长的索引糟。
- 同样按 `warning` 还回去的还有两个：`MEMORY.md` 里放不下的一条（`name` 不是 kebab-case 等），和 `description` 超过 150 字符。都是提醒，不是拒绝。

## 权限 {#permissions}

`/memory/files`（`api/routes/memory_files.py`）：

| 操作 | 谁可以 |
|---|---|
| 读 / 写 / 删 `team` | 项目成员 |
| 读 `private/<handle>` | 本人；项目管理员也看得见（`MemberService.manages`） |
| 写 / 删 `private/<handle>` | **只有本人**，管理员也不行 |

别人的 `private` 答 **403**（不是 404）：「这里有一条，但不是你的」。**读得到的范围不等于写得动的范围**（`_readable` / `_writable`）：管理员看得见是为了出事时能查（比如说有人把密钥写进去了），不是为了替谁改——给他一支笔，那一条记忆就同时有了两个主人，而「这是谁的判断」正是 private 这一层唯一要保住的东西。路径最长 200 字符（`files.PATH_MAX`，和 `memory_files.path` 那一列的宽度一样），超了答 422。写入带 `version`，对不上就 409 并把当前那一版一起还回去——冲突是拒绝，不是合并：两版散文自动合并的唯一结果是两句互相矛盾的话安静地并排躺着。两个同时起手的新建同一个路径也会撞在唯一约束上，那一路同样答 409（对面赢了的证据就是那条约束）。

界面上还没有记忆面板（这套 API 是为了它先露出来的）。

## 房间里的那条事件 {#events}

每次真的改了东西，说进那棵树自己的房间，**不点任何人的名**（`memory_changed`，`platform_notices.memory_changed_notice`）：

- `team/` 的改动 → **项目总览房间**；
- `private/<handle>/` 的改动 → **那个人的私聊**（没有就现开一间）。

一条记忆是 agent 写下的一份观察，没有人欠它一个动作，所以它是一条灰字事件，事件本身收进 `meta.detail`（统一 diff，按路径分段、每段上限 200 行）。两棵树分开说，因为读它们的人不是一批：把某个人的 private diff 说进总览，等于把一个人的偏好广播给整个项目。

写记忆的那个 agent 读不到这条灰字事件——它在会话机上，它看到的世界就是那棵树。所以**有被平台盖回去的版本时，那条通知还带一句 `agent_notice`**（`platform_notices.memory_conflict_notice`）：点名哪几条被盖了、它写的那一版在哪个 `.conflict.md` 里、请重读再写。不说，它下一轮写的还是同一版，而每一轮都会被盖回去。

## 为什么不是「条目池 + 关键词召回」 {#why}

上一版是「记忆 = 数据库里的一个个条目」，按 `<项目>:<agent>:<人>` 分池，core 层每轮注入、fact 层要用 `cheese_recall` 按关键词查。它有四个解不掉的问题：

1. **召回是一次查询，而查询是按需发生的。** 需要它的那一刻，正是 agent 觉得自己已经知道的时候——一条提示词里看起来已经满了，没有人会再去搜。
2. **关键词检索不是语义检索。** 它只能把问题切成词、按覆盖度排；一次空结果读起来和「这条记忆不存在」一模一样，于是 agent 会一头撞上那条记忆本来要拦住的坑。
3. **core / fact 两档是一条连续的判断，而它被交给了写的人。** 结果是要么什么都往 core 里塞（预算爆掉），要么一条都不塞（那一档等于没有）。
4. **写入的形状是一组工具参数**（`fact` / `core` / `everyone`）。参数能表达的只有「一句话」，而一条好记忆需要的是四种类型、正文结构、查重、什么不该写——这些只能是一段规矩加一个能写文件的手。

所以照 Claude Code 2.1.283 换成了现在这套：**索引进上下文，正文在文件里，检索变成「看得见的列表 + 自己读」**。文件树跟会话走、跟项目走，谁写的、什么时候写的、改了哪一版都留在 diff 里。

同一个方向上的收尾：`cheese_recall` 命令撤了，`/projects/{id}/memory/search` 那条路由撤了，`memory/pools.py` 删了，关键词切分（`memory/keywords.py`）只剩一个调用方——写记忆之前拿最重的几个词去检出目录里查一遍（`memory/redundant.py`）。旧表 `memory_entries` 不 drop，界面上还在读它。

## 整理与迁移 {#later}

记忆整理（dream）与旧表迁移，见后续。
