# task / room / 文档：现状与关系

> @wangchangxin 问「现在 task、room、文档等等东西的现状和相互之间的关系」。
> 下面每一条都是 2026-08-23 在 main（`3e945077c`）上读代码核实的，不是照设计文档抄的。
> 改造已经**合进 main 了**（#611 → #612 → #613），所以 <&docs/topics/task设计现状.md>
> 里「还没改的」那一节已经过期，以本文为准。

## 一句话

**房间是地方，一件活是这个地方里的一条支线，文档是这个「地点」的当前状态摘要。**
一个地点 = 一个房间 +（可选）一条支线。它有一个 id、一条 git 分支、一个工作区、一份文档。

## 0. 先消歧：本仓库有三个叫 task 的东西

讨论前必须指明是哪个，否则一定说岔。

| 说法 | 是什么 | 代码位置 | 现状 |
|---|---|---|---|
| **`tasks` 表 / room_task** | 现在说的「一件活」——房间里的一条支线 | <&backend/app/domain/room_task/models.py> | **活的，就是本文的主角** |
| **`task` 表 / domain/task** | 老知是社区的作业任务（提交、评审、AI 建议那一套） | <&backend/app/domain/task/models.py> | 还在，路由自动挂载，**与话题体系毫无关系** |
| **`TopicKind.task`** | 「一件活曾经也是一行 topics」 | <&backend/app/domain/topic/models.py> | **历史值**，迁移 `a9f3c7e21b04` 已把这些行全删了；枚举留着只为老库能恢复 |

（旧文档里提到的 `cx_task` 已经不存在了。）

## 1. Room（房间）= `topics` 表

- **是什么**：一个长期存在的**场所**。有名册、有一份实况文档、有对话、有工作区和分支。
- **它不会自己结束**：只有人能归档（#442 决议 1）。合并 PR 只是盖一个
  `accepted_by`/`accepted_at`（「这一次交付完成了」），房间照常活着——因为下一件活通常还在同一个房间里发生。
- **只有两种 kind 还在用**：`root`（= 项目本身）和 `topic`（= 二级房间）。房间之下**不能再建房间**（`_require_room` 直接拒）。
- 归档之后还能继续追加对话，只是工作面冻结。

## 2. Task（一件活）= `tasks` 表，房间里的一条支线

- **是什么**：房间里**一次会结束的工作**。<&backend/app/domain/room_task/models.py>
- **它有而房间没有的**：唯一的主（`owner_handle`，不是名册）、状态（只有 `open`/`closed` 两个值）、
  自己的分支和工作区、**自己的一条对话**（`blocks.task_id` 指向它）、自己的交付戳。
- **它没有而房间有的**：名册、未读游标、归档决策、侧栏里的一行。
  这正是这次改造的全部收益——一件活不再需要付「一个房间」的成本。
  `GET /topics/{支线id}/members` 返回 404，这条有测试钉住。
- **不嵌套**：一件活派出的活，是**同一个房间里**的另一条支线，不是它的子节点。
- **两条路生出一条支线**：
  - `cheese split "标题" --brief ...` → `POST /topics/{id}/split`，从上往下派活；
  - 在房间里点某条消息「升级」→ 那条消息变成一条支线，原位置变成活链接。
    （**私聊里的消息升级出来的是一个真房间**，因为私聊不在话题树里，支线在那儿没人打得开。）

### Place：把两者粘起来的东西

<&backend/app/domain/room_task/place.py> 的 `Place(room, task)`。所有会跑的东西——一轮对话、
一个工作区、一张验收卡、一笔用量——地址都是这一对。

**关键的兼容技巧**：一条支线**沿用了它替代掉的那行 `topics` 的 id**（迁移 `c4a7e91b2d05`）。
所以已经发出去的 `CHEESE_TOKEN`、分支名（`topic/xxxxxxxx`）、工作区路径、会话 token 一个都没失效，
一个 id 仍然能定位一个地点。

## 3. 文档 = 一个 `blocks` 行（kind=doc），**按地点**存

- 实况文档不是单独的表，就是这个地点的一个 block，带版本号（`doc_version`），改的时候要带
  `expected_version`，冲突就拒。
- **两级文档，都真实存在**：
  - **房间的文档** = 共享的全景图（`task_id IS NULL`）；
  - **每条支线自己的文档** = 它的任务简报，然后是这件活干到哪了。
  两者靠 `task_id` 区分——`doc_root` 里那个 `task_id IS NULL` 是承重的，漏了房间的文档会被解析成
  某条支线的简报。
- **一条支线诞生时，它的文档被预置成任务简报**（`_seed_brief_doc`，作者是 `system`）。
- **改文档即指令**：人改了房间文档，会给分身发一条提醒（「现在是第 N 版，先 `cheese doc get` 重读」）。
- 支线的上下文 = **房间实况文档 + 房间最近若干条消息（按字符预算截，截掉了会在 prompt 里说出来）+ 自己的任务简报**。

## 4. 关系图

```
项目 (root topic)
 └─ 房间 topic ──── 名册 / 实况文档 / 主线对话 / 分支 topic-xxxx / 工作区
     ├─ 支线 task A ── 唯一的主 / 简报文档 / 自己的对话 / 自己的分支+工作区
     ├─ 支线 task B
     └─ 支线 task C
```

- **对话**：`GET /topics/{房间}/blocks` 是房间主线，`GET /topics/{房间}/tasks` 是所有支线（各带自己的对话）。
  **两边没有交集，合起来才是这个房间说过的全部话。**
- **侧栏树**（`/children`）只列房间。**支线不在侧栏里**——它在房间时间线上显示成一条「已派出」标记
  （<&frontend/src/lib/splitMarkers.ts>，读时派生，因为 split 不往房间主线写任何 block），点进去就打开那条支线。
- **未读 vs 活跃**：房间的 `last_activity_at` **算**支线（房间里有活在跑就是活的），
  未读计数**不算**（否则每条支线说句话都把房间标红，红点变噪音）。两处相邻、写法相似、结论相反，是故意的。

## 4.5 任务在 UI 上到底怎么显示（2026-08-23 核实）

一句话：**支线在房间的时间线上有一条标记，但它自己没有任何界面** —— 点进去打不开。

### 看得见的三处

1. **房间时间线上的「已派出」分隔线**（<&frontend/src/components/DispatchedMarker.vue>）：
   一条细线，写着「⑂ 已派出《标题》· 这部分正在进行 / 这部分已完成」。
   刻意做成分隔线而不是气泡——它标的是时间线上的一个转折点（往下这段时间里，这件事在别处做），不是谁说了句话。
   位置和内容**没有存在库里**，是读时用支线的 `room_id + created_at` 现算的
   （<&frontend/src/lib/splitMarkers.ts>；因为 `split` 不往房间主线写任何 block，不这么做房间里就完全无痕）。
2. **被升级的那条消息上的小链接**：`upgraded_to_task_id` 在 ChatPanel 的气泡和 PanelDoc 的文档节点两处都渲染成「已升级为话题」。
3. **就这些。** 没有任务列表面板、没有看板、没有卡片。

### 看不见的地方

- **侧栏树里没有支线。** 侧栏读的是 `GET /topics?project_id=`，而这个接口只查 `topics` 表。
  **实测（本项目，2026-08-23）：169 行，`kind` 全是 `topic`(168) + `root`(1)，一个支线都没有。**
  `TopicSidebar.vue` 里还留着 `task: '任务'` / `subtopic: '分身'` 两个 kind 标签，是改造前的残留，现在永远取不到。
- **没有「这个房间有几件活在跑」的汇总视图**。要看全部支线只能调 `GET /topics/{房间}/tasks`，
  目前只有 ChatPanel 为了画标记而调它（而且 `limit: 1`，每条支线只取最新一条 block）。

### ⚠️ 已确认的 bug：点开一条支线 = 「这个话题不存在」

点「已派出《X》」→ `emit('open-topic', taskId)` → `router.push({name:'workspace-topic', params:{topicId: 支线id}})`。
然后 <&frontend/src/views/workspace/TopicView.vue:49>：

```ts
const selectedTopic = computed(() => store.topics.find((t) => t.id === props.topicId) ?? null)
```

`store.topics` 只由 `listTopics()`（= 只有房间）填充；唯一往里 `push` 的地方是「新建房间」，
`refreshTopicRow` 也只 `if (i >= 0)` 就地更新、不新增。**所以支线 id 永远不在里面** →
`selectedTopic` 为 null → 渲染 <&frontend/src/views/workspace/TopicView.vue:270-275> 那个分支：

> 这个话题不存在 / 它可能已被删除，或不属于这个项目

同一条路还有第二个入口：消息升级完成后 `handleUpgradeMessage` 直接 `openTopic(upgraded.id)`，一样打不开。

**后端是好的**：`GET /topics/{支线id}` 会正确解析成一个地点并返回 TaskOut（`room_id`/`owner_handle`/`status`），
`/blocks`、`/doc` 也都认支线 id。**缺的只是前端这一步：`selectedTopic` 从一张只装房间的列表里找。**

**为什么没被测出来**：<&frontend/src/components/__tests__/ChatPanelDispatched.test.ts> 只断言
「点标题 → emit('open-topic', 'sub-1')」，正好停在组件边界上；e2e 里 `split`/`已派出` 零覆盖。

修的时候要注意两件事（漏了不会报错）：
- 头部拿到的是 **TaskOut 不是 TopicOut**（没有 `kind`、没有 `TopicStatus`、没有名册），
  `TopicHeader` 现在按 `Topic` 取值，直接塞会渲染出一堆空。
- 侧栏高亮走 `ancestorPathIds(props.topics, selectedTopicId)`，支线不在树里 → 打开支线时侧栏什么都不亮，
  人会不知道自己在哪个房间里。

## 4.6 追加：支线不只是「打不开」，是**根本看不见**（@wangchangxin 2026-08-23 指出）

我 §4.5 只说了「点进去打不开」，那是低估了。更根本的问题是：**「这个房间里有哪些活」在界面上没有入口。**

实测：`listRoomTasks`（`GET /topics/{房间}/tasks`）全仓库**只有一个非测试调用点** ——
`ChatPanel.vue:1012`，而且传的是 `limit: 1`，纯粹是为了算时间线上那条派生标记的位置。
**没有任何列表视图。** 所以一个房间里有几件活在跑、谁在做、做到哪了，人完全看不到；
唯一的线索是滚动房间时间线，碰巧滚到派出去的那一刻，看到一条「已派出《X》」的细线——而它还点不开。

### 这条追加要求已经用 `cheese tell` 发给支线「支线在界面上能打开」了

要做到三件事：

1. **房间里要有一个能列出全部支线的地方。**
   建议放进 `WorkPanel` 的 tab 栏（现在是 chat / doc / 施工现场 / 改动 / 预览 五个，加第六个「任务」）——
   数据接口现成，而且它和文档、施工现场并列是说得通的：三者都是「这个房间当前状态」的一面。
   每条至少要有：标题、主（`owner_handle`）、状态（进行中/已完成）、最后活动时间；点一条打开它。
   **具体放哪由做的人定**，但「房间里必须有一个地方能看到全部支线」是硬要求。

2. **侧栏树里仍然不要给支线单独一行。**
   那正是这次改造要消除的成本——一条支线不该付一个房间的价钱（名册、未读游标、归档决策、侧栏一行）。
   房间那一行可以有个「N 件活在跑」的角标，让人知道值得点进去，但**不要展开成树**。

3. **打开一条支线时要有一条回房间的路**（面包屑或返回）。这和原验收标准第 4 条是同一件事，一起做。

### 一个漏了不会报错、只会变慢的点

`GET /topics/{房间}/tasks` 的 `limit` 参数管的是**每条支线各带回几条 block**，不是带回几条支线。
**不传 `limit` 会把每条支线的全部对话历史都拉回来**——接口自己的 docstring 警告过这件事
（原话：一个长期房间已经攒了接近两百条支线，而它不敢自己定一个默认值，因为一个瞎定的数字会静默截断）。
列表视图不需要 block，传 `limit: 1`。

## 4.7 追加（@wangchangxin 2026-08-24）：可视化才是重点，边栏 + task 绑定 PR

原话：「现在 task 不可见，thread 不可见，你也要 fix 一下，比如你可以加一个边栏，
每个 task 绑定一个 PR 或者 thread 之类的，之前的设计方案里面应该有提到，**你主要是让他可视化**。」

他重贴的目标形状：

```
Room 房间（长期场所，不自动归档）
  ├ 成员名册 / Live Document / 对话
  └ Task 任务（一次工作，自动结束）
      ├ 唯一的主、状态、隔离工作区、一条支线对话
      ├ 上下文 = Live Document + 任务简报 + 最近若干条房间消息
      └ 交付：PR / 验收卡
```

**界面要把这棵树呈现出来。** 这条推翻我 §4.6 第 2 点里「侧栏树不给支线单独一行」的顾虑——
那个顾虑针对的是**数据层**的成本（名册、未读游标、归档决策），而这些成本已经在改造里去掉了，
`tasks` 表一行本来就不带它们。**在侧栏里显示，不会把那些成本加回来。** 按他说的做。

### 做的时候要用到的接口事实（我核过，别重查）

- 房间的全部支线：`GET /topics/{房间id}/tasks`。`limit` 管的是**每条支线各带回几条 block**，
  列表视图传 `limit: 1`（不传会把每条支线的全部历史都拉回来）。
- 一条支线的验收卡 / PR：`GET /topics/{支线id}/accept-card`。
  它**按地点过滤**（`review/repositories.py:66` 的 `list_for_topic`：房间只看自己的、
  支线只看自己的），所以拿房间 id 查**不会**返回支线的卡。
  返回里有 `pr_number` / `pr_url`（`review/schemas.py:84-85`）。
- **N+1 警告**：照上面两条直接实现，一个有 N 条支线的房间要发 N+1 个请求。
  可以接受（先能看见），但更好的做法是把每条支线的卡/PR 摘要并进 `/tasks` 的返回里，
  或加一个按房间批量查的接口。**做哪种由做的人定，但要在结论里说明选了哪种、为什么。**
- 支线的分支名在 `tasks.branch_name`（真写的），房间的 `topics.branch_name` 是死列、永远 NULL。

## 5. 交付：一个房间一条分支一个 PR

这是最容易搞反的一条。设计图上曾经画的是「一件活自己开 PR」（D1 走 A），**最后没走这条**，因为
本项目 CI 队列约 1.7 小时，一件活一个 PR 会把 PR 数量按活翻上去。

现在实际是：

1. 支线干完活 → `cheese conclude` 回流结论 → 房间里开一张**结论卡**；
2. 结论卡**默认采信**（父话题一轮结束、或 30 分钟没人管，就自动采信）；
3. **采信的那一刻，这条支线分支上的提交被并进房间的分支**（`fold_into_room`，
   <&backend/app/domain/conclusion/services.py>）；
4. 攒够了，**房间**递验收卡 → 开 PR → CI → 合并。

三种情况不会合、但都会在房间里说一句、不会默默算了：房间正在等 CI（`pr_open`，现在合会把 CI 打回起点）、
房间工作区有人在改、两条活改到同一处（冲突，要人解）。

`accepted_by`/`accepted_at` 这两个戳**在 task 上也有一份**（一件活自己的交付记录），
但**开 PR 的是房间**。`branch_for_place` 对房间和支线都给分支名，只是支线的分支是折进去、不单独开 PR。

## 5.5 已拍板：房间的用量算总账（@wangchangxin，2026-08-23）

选的是「**算进来：房间显示总账，支线单看也能看**」。

核下来这件事比预想的小：`resource_usage` 同时有 `topic_id`（房间）和 `task_id`（支线），
一条支线的用量行存的是 `topic_id=房间 + task_id=支线`。而 `UsageRepository.for_topic` 是
`_agg(ResourceUsage.topic_id, 房间id)` —— **它本来就把支线的花费一起加进去了，「总账」这一半已经成立**。

真正缺的是**「支线单看也能看」**：`GET /topics/{id}/usage` 和 `/transcript` 开头是
`TopicService(db).get_or_404(topic_id)`，拿支线 id 直接 404（旁边的 `/blocks`、`/doc` 都已经改成解析 Place 了，
只有这两条没跟上）。所以要做的是：
- 两条路由改成 `place_or_404`，鉴权照旧落在房间上；
- 支线 → 按 (`topic_id`=房间, `task_id`=支线) 聚合；房间 → 维持现状（`topic_id`=房间，天然含支线）；
- 前端 `TopicHeader` 的用量弹层拿支线 id 调 `getTopicUsage` 时不再 404。

## 6. 已知没做完 / 缺口（截至 2026-08-23）

1. **用量和记录只对房间回答**：`GET /topics/{id}/usage` 和 `/transcript` 拿支线 id 会 404
   （它们还是 `get_or_404(topic)`）。存储层早就按 (房间, 支线) 存了，卡住的是一个**产品判断**：
   房间的用量该不该包含它派出去的支线花的钱？**这条要人拍板**，#612 的作者明确没有擅自决定。
2. **文档过期**：<&docs/topics/task设计现状.md>「三、还没改的」和「三·五、要拍板的 D1」都已经落地了，
   <&docs/topics/room与task设计与审批流程.md> 里「task 有三个含义」那张表也过期（`cx_task` 没了、
   `TopicKind.task` 已成历史值）。按 CLAUDE.md「设计落地就在同一个 PR 里删掉被推翻的句子」，这两份该清。
3. **一件活没有依赖关系**：设计里提过 task 之间的依赖，代码里**故意没有**——空的关联表和活的关联表，
   对下一个读代码的人长得一模一样。
4. **`branch_name` 在 `topics` 上是死列**：没有任何代码写过它，每一行都是 NULL（分支名是派生的）。
   task 上的 `branch_name` 才是真写的。

## 7. 现在：全部收回房间，串行做（@wangchangxin 2026-08-24：「还是你这边串行吧，其他地方我看不到」）

先前派出去四条支线。@wangchangxin 指出**支线在界面上看不见**，所以后续改成在房间里串行做。
四条支线全部叫停，产出用 git 直接合进房间分支 `topic/b795cab5`。

### 为什么不能等它们 `conclude`

**支线在机制上回流不了。** 支线用自己的 per-turn token 调 `return_conclusion` 会 403
（详见 §7.1），所以四条支线到现在状态全是 `open`、一张结论卡都没有。
`fold_into_room` 那条正路走不通，只能我手工 merge。

### 已合进房间分支的（35 个文件，+2649 −726）

| 来源 | 内容 | 量 |
|---|---|---|
| `topic/dec0b271` | 支线自己的 token 能打到支线自己的路由（修 §7.1 那个 403） | 3 文件 |
| `topic/acf795a0` | 清掉被改造推翻的三份设计文档 + 四处 `docs/llm-gateway.md` 死引用 | 7 文件 |
| `topic/c07af2a1` | **可视化**：边栏里房间下挂它的活、每行带 PR；支线能打开；新增 `PanelTasks.vue` | 26 文件 |

合 `c07af2a1` 时在 `routes/topics.py` 撞了 7 处冲突——两条支线各自修了同一个 403 bug。
取了把它抽成 `_actor_in_place(resolver, place)` 辅助函数的那一版（注释写得更清楚，
且 16 处调用点统一），核过无残留。

### 合进来的行为（读它写的测试，不是读提交信息）

边栏 `TopicSidebarThreads.test.ts`：一件活在树上有自己的一行 · 它骑的那个 PR 就写在这一行上 ·
还没递卡的活不硬造交付状态 · 做完的活不跟在跑的长一个样。

打开支线 `TopicViewThread.test.ts`：侧栏列表里没有它照样打得开 · 头部说得出它属于哪个房间 ·
房间还是照旧从列表拿不多打请求 · id 真不存在时才说不存在 · 取的过程中不先闪一下「不存在」·
支线不记已读。

### 还没合的

`topic/18d1fceb`（重做文档声明规范）**在 origin 上没有任何分支** —— 它刚开工就被叫停，
手上是空的。**那份活要在房间里从头做。**

## 7.1 支线回流通道全断：7 条路由对支线 403（已修，在房间分支上）

支线用自己的 per-turn token 调这 7 条会被拒（「这个 token 属于别的话题」）：
`get_topic` · `list_topic_blocks` · `list_topic_docs` · `get_topic_progress` · `get_topic_doc` ·
**`tell_topic`** · **`return_conclusion`** —— 后两条正是支线仅有的两条回报通道，
也就是 `cheese tell` 和 `cheese conclude` 全断。反方向没事（房间 → 支线正常）。

**根因**：`ActorResolver._reject_out_of_scope_token`（`backend/app/api/auth.py:292`）
拿 token 的 `t` claim 比对传进来的 `topic_id`。支线的 token 带的是**支线自己的 id**，
而这 7 条路由传的是 `place.room_id`，两者不等。房间不受影响，因为房间的 `place.id == room_id`
——**所以这个 bug 只在支线上显形，而支线正是没人测的那一半。**

正确写法：`resolve` 传 `place.id`（身份/作用域），`authorize_topic` 传 `place.room_id`（名册）。
修复已合进房间分支，带 `backend/tests/integration/test_cli_works_in_a_thread.py` 钉住。

## 8. ⚠️ 两个挡路的平台问题（都不在上面两条支线范围里）

### 8.1 ~~质量闸门指着一个不存在的脚本~~ —— **我说错了，已收回**

我 2026-08-23 说过「闸门指着不存在的 `check.sh`，现在谁都递不出验收卡」。**这句是错的。**

2026-08-24 读码核实：**前置机器闸门已随 #296「采纳即合并」退役**——
`run_check_command` 和它的 runner 都已删除，卡**永远不会**生成 `pending_gate` 态
（`backend/app/api/routes/accept.py:70`：「the old machine-gate dispatch is retired
(cards are never born `pending_gate` any more)」；`review/services.py:3184` 再说一遍）。
`check_command` 的值还存着、`GET /quality-gate` 照样返回它，但**没有任何东西会去跑它**，
所以它指着一个不存在的脚本毫无后果。

递卡现在直接用平台 App 的 installation token 开 PR，由 GitHub 上真实 CI 决定绿不绿。
**没有任何东西挡着递卡。** 两条支线已收到更正。

### 8.2 main 上有三个空合并，其中一个的内容彻底丢了

查 main 最近 20 个提交，**三个是零文件改动的空合并**：

| 提交 | 是什么 | 内容找回来了吗 |
|---|---|---|
| `97b53d673` (#611) | feat(tasks): make a thread a place an agent can work in | ✅ 被 `3a5c86211`(#612, 118 文件) 重新交付 |
| `ffec6799a` | feat(tasks): give work a thread in a room | ✅ 同上 |
| `f38c03282` (#610) | docs: make every document declare whether it is current or a record | ❌ **从未补回** |

**#610 整份没了**。它要做的事——给每份文档加上「我是现状 / 我是记录」的声明、加一个 `check-docs.py`
来判定、把 149 份无索引文档收进索引——**一行都不在 main 里**。

证据：`check-docs.py` 不存在；`docs/llm-gateway.md` 不存在，而这四处仍在引用它：
`backend/app/api/deps.py:101`、`backend/app/core/config.py:100`、
`backend/app/domain/agent/chat.py:1353`、`backend/app/domain/agent/gateway.py:1`（模块 docstring 第一行）。

**体检命令**（值得定期跑）：
```
for c in $(git log --format=%h -20); do echo "$c $(git diff --name-only $c^ $c | wc -l)"; done
```

**要 @wangchangxin 定的**：#610 那一整套要不要重做？（清那 4 处引用已经派给「清过期设计文档」那条支线了，
但「每份文档声明自己是现状还是记录」+ check-docs.py 是一整个 PR 的量，我没擅自派。）

## 9. 这些改动怎么走到 main（@wangchangxin 追问「那你改的有什么用」）

这个问题是对的——我上一轮派完活就停了，没说产出怎么落地。补上，这条链每一环都得有人负责：

1. 支线干完 → `cheese conclude` 回流结论 → 房间里开一张结论卡；
2. 结论卡**默认采信**（房间这一轮结束、或 30 分钟没人管就自动采信）；
3. 采信那一刻 `fold_into_room` 把支线分支上的提交折进**房间分支** `topic/b795cab5`；
4. **← 这一步归我**：房间递验收卡（`cheese accept-request`）。递卡即开 PR（App token，立刻），CI 跑起来；
5. **← 这一步归人**：@wangchangxin 在验收卡上采纳，PR 合并进 main。

**第 4 步我之前没承诺过，那正是「改了有什么用」的答案**——不递卡，两条支线的产出就折进
`topic/b795cab5` 然后停在那里，永远到不了 GitHub（平台只在卡处于 `pr_open` 的轮询里才代推）。
现在明确：两条支线的结论都回流并折进来之后，**由我递卡**。

**递卡后必须做的一件事**（见 §8.2，最近 20 个提交里三个空合并）：
用 `gh api repos/{owner}/{repo}/pulls/{n}` 确认 `changed_files` 不是 0，**再叫人采纳**。
PR 在 GitHub 上显示已合并，不等于内容进了 main。

## 待办

- [x] 用量/记录要不要把支线算进房间 —— **@wangchangxin 2026-08-23 拍板：算进来**（见 §5.5）。
- [~] 修「点开支线 = 这个话题不存在」+ `/usage` `/transcript` 解析 Place —— **已派给支线「支线在界面上能打开」**。
- [~] **让支线看得见**（房间里的任务列表，§4.6）—— 已用 `cheese tell` 追加给同一条支线。
- [~] 清过期设计文档 + 4 处死引用 —— **已派给支线「清过期设计文档」**。
- [x] ~~质量闸门挡着递卡~~ —— **我核错了，已收回**（§8.1）：机器闸门早已退役，没有任何东西挡着递卡。
- [ ] **两条支线回流后由我递验收卡**（§9），递完核 `changed_files != 0` 再交给 @wangchangxin 采纳。
- [x] **可视化（边栏 + task 绑 PR）**（§4.7）—— 已合进房间分支，见 §7。
- [x] **支线 403 修复**（§7.1）—— 已合进房间分支。
- [x] **清过期文档 + 4 处死引用** —— 已合进房间分支。
- [ ] **跑完测试 → 递验收卡开 PR → 核 changed_files != 0 → 交 @wangchangxin 采纳**。
- [ ] **#610 重做**（§8.2）—— 支线被叫停时手上是空的，要在房间里从头做。规格用 PR 正文。
