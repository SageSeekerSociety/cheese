> <@wangchangxin> 问「现在 cheese 上 task 是怎么设计的」，随后给了最新设计（2026-08-20 三张图），
> 2026-08-22 拍板：**不拆活，全做在一个 PR 里**（<@wangchangxin>：「你就在这一个PR上做就行了，这样你才能一直跑CI」）。
> 本文档：**现在到哪了** → **设计** → **已改的** → **还没改的** → **顺带挖出来的平台毛病**。

## 一句话

- **改造前**：task 是「一个会终止的房间」——一整行 `topics`，连带名册、未读游标、归档决策、侧栏一行。
- **要改成**：task 是**房间里的一条支线**（thread，不是新 channel）。
- **现在**：改造在 **PR #609** 上进行中（<@wangchangxin> 拍板：不拆活，全做在一个 PR 里，这样 CI 一直在跑）。
  地基抢救回来并跑过 CI；切换（split / upgrade / 搬键 / 迁移）写完并在真库上验过；一轮已经跑在「地点」上；
  结论回流、路由层、用量、前端标记都跟过来了；**上下文装配（房间文档 + 任务简报 + 房间最近消息）已落地并有测试**。
  剩下的是收尾：几个断言旧形状的测试、D1（任务自己交付）、以及清理债。
  **PR 里有一个故意失败的测试挡着自动合并**（<&backend/tests/unit/test_switch_is_still_in_progress.py>），
  改造做完时删掉它，那一刻才是可以合的时刻。

## 一、设计（<@wangchangxin> 2026-08-20）

拆开看子房间提供四样：干净的上下文、隔离的工作区、一份交付、**一个能看能插话的地方**——
前三样本来就归任务，只有第四样需要「房间」，而它要的是**房间里的一条支线**。

```
Room 房间（长期场所，不自动归档）
  ├ 成员名册 / Live Document / 对话
  └ Task 任务（一次工作，自动结束）
      ├ 唯一的主、状态、隔离工作区、一条支线对话
      ├ 上下文 = Live Document + 任务简报 + 最近若干条房间消息
      └ 交付：PR / 验收卡
```

**已定的三条**（我定的，反对随时说）：
1. **D1 走 A**：`branch_name` / `accepted_at` 在 task 上，任务自己交付。理由不是「图上这么画」，而是
   **房间不会结束**（`#442 decision 1`：只有人能归档）**而 PR 必须结束**，把交付挂在一个永不结束的东西上，
   就永远要回答「这房间的 PR 什么时候算完」。`fold_into_room` 那套与之冲突，一并删。**这条还没动手，排在最后。**
2. **任务结束是折叠不是冻结**：房间归档后都还能追加对话，支线更没理由冻——不然改一行就得开新支线，历史被切两半。
3. **「最近若干条房间消息」按字符预算截、不按条数截**，截掉了要在 prompt 里说出来（按条数截会被一条长消息吃光）。

## 二、已经改完的（都在 PR #609 上，本地跑过）

### 迁移 `a9f3c7e21b04`（<&backend/alembic/versions/a9f3c7e21b04_work_is_addressed_as_a_task.py>）

七张表加 `task_id`（`topic_id` 含义收紧成「房间」）；`topic_progress` 和 `webhook_tokens` **换代理主键**
（原来 `PK = topic_id`，而主键不能为 NULL）；`blocks` 加 `upgraded_to_task_id`；复用地基那两条模块级回填语句
补跑；把所有引用从「工作话题」改指 (房间, 支线)；最后 `DELETE FROM topics WHERE kind IN ('task','subtopic')`。

**唯一性用两条部分索引，不是把列加进原来的唯一约束**——NULL 在唯一索引里不相等，一条更宽的索引会让房间那一半
**静默地不再互斥**。

**真库验过**：造了房间 + 一件活 + **一件挂在另一件活底下的嵌套活**（存量里有 19 行是这样），带对话/会话/卡/进度/
webhook/用量/turn/结论卡/名册。结果：嵌套那件的 `room_id` 走到了真正的房间；房间自己的 doc 和消息 `task_id` 为
NULL；工作话题行清空；`strays=0`、`orphans=0`。`downgrade` 也验过，**它故意不重建被删的话题行**（嵌套的活在迁移时
被重新挂到房间上，硬造回去会产生一棵从没存在过的树）。

### 代码

- **`Place(room, task)` + `PlaceResolver`**（<&backend/app/domain/room_task/place.py>）：一个 id 仍然能定位一个地点，
  因为 task 沿用了旧话题行的 id。**所以已经发出去的 `CHEESE_TOKEN`、分支名、工作区路径、会话 resume token 一个都没失效。**
- **`split` → `dispatch_task`**：不建话题行、**不铺名册**（唯一的主就是任务和房间的全部区别）、简报是带 `task_id` 的 doc block。
- **`upgrade` 按位置分岔**：房间里的消息 → 一条支线；私聊里的消息 → 一个房间（私聊不在话题树里，支线在那儿没人打得开）。
- **`_child_kind` 删掉**：它回答「房间底下该建哪种话题」，而底下已经不是话题了。换成 `_require_room`，
  房间之下再建房间直接拒绝。
- `branch_for_topic` → `branch_for_place`（派生值逐字节不变，没有任何分支/工作区/容器路径移动）。
- `agent_sessions` / `agent_turns` / `accept_cards` / `topic_progress` / `webhook_tokens` 按 (房间, 支线) 读写。

**一个权衡**：`blocks.add` 有 36 个调用点，没让它们全改成传一对，而是让它自己解析「地点 id」。
理由是**漏传不会报错**——只会把支线的消息写进房间主时间线，谁都看得见、就那条支线看不见。
为避免每写一个 block 查一次库，一轮开始把解析结果记在按 id 比对的 ContextVar 里（记错只会失效，不会指向别处）。

### 测试

`test_split_authz.py` 7 个全绿：`POST /topics/{id}/split` 出来的是支线，`owner_handle` 对、`room_id` 对、
**`GET /topics/{支线id}/members` 返回 404**、房间名册没动。新增
`test_a_thread_does_not_get_a_roster_of_its_own` 钉住这条。

## 二·五、后来补上的（都在 PR #609 上）

- **一轮跑在「地点」上**：`chat.py` 三处按 id 取 Topic 的地方改成解析 `Place`。切换前，split 出一条支线之后
  分身的开工轮次会直接死掉（`Topic not found`）。
- **上下文装配**：支线的 prompt = 房间实况文档 + 房间最近若干条消息 + 自己的任务简报。
  按**字符预算**截不按条数（一条长消息会吃光按条数的窗口），**截掉了要说出来**，
  并明说「房间主线不是你这条支线的对话，要让房间知道用结论回流」。房间自己跑的一轮不重复注入。
  测试见 <&backend/tests/unit/test_thread_context.py>。
- **结论回流**：消息和文档 section 落在**房间主线**（`task_id=None`）——回流的全部意义就是让房间看见；
  结论卡两端都是房间 + 一个支线键；`need-evidence` 叫醒的是**支线**不是房间。
- **级联归档整套删掉**：工作不嵌套，一条支线没有后代，它防的场面在结构上不存在了。
  采信从「归档」变成「收起」（`status=closed`），**收起不冻结**，支线照常能追加对话。
- **路由层**：`/blocks`、`/doc`、`/docs`、`/progress`、`/return-conclusion`、`GET /topics/{id}` 都解析地点；
  鉴权永远落在房间上（支线没有名册）；分页游标要连支线一起比，否则翻页会静默串线。
- **用量**：`resource_usage` 直接吃地点 id 会违反外键（响的）；订阅入账的 work index 拿地点 id 匹配 block
  会一条都匹配不上、让那条支线的用量静默变成无法归属（不响的）。两处都改了。
- **前端**：`splitMarkers` 改读 `GET /topics/{id}/tasks`；`upgraded_to_task_id` 在 `ChatPanel` 和 `PanelDoc`
  两处都渲染；组件测试与 spec 都按新行为重写。

## 三、还没改的

1. **剩下的测试**：`test_conclusion_cards` 还有 2 个（定时扫描那两条）、`test_project_tree` 1 个，
   以及全量跑之后才会暴露的其它文件。
2. **D1 落地 + 清理债**：删 `fold_into_room` / `sweep_room_merges` / `TopicKind.task`、以及所有还在写
   「task 是一个会终止的房间」的散文。
3. **最后一步**：删掉 <&backend/tests/unit/test_switch_is_still_in_progress.py>。

**两个漏了不会报错、只会变难用的点**（已记，改的时候要各钉一条测试）：
- `topic/repositories.py:39` 的 `last_activity_at` **要**算上支线（房间里有活在跑就是活的），
  而 `:251` 那条未读计数**不能**算（每条支线说句话就把房间标未读，红点立刻变噪音）。两处相邻、写法相似、结论相反。
- `doc_root` 必须带 `task_id IS NULL`，否则房间的实况文档会解析成某条支线的简报。

## 四、顺带挖出来的平台毛病（都不在这个 PR 范围里）

1. **`cheese-sync` 钩子漏推**（<&backend/app/domain/agent/harness/claude_code/device_launch.py>）：
   判据是「有没有未提交改动」而不是「有没有未推送的提交」，`git diff --cached --quiet && exit 0` 挡在 push 前面。
   **分身自己 commit 过就永不推送——越守规矩丢得越干净。** PR #608 整整 1010 行就是这么丢的
   （`additions=0` 合进 main）。已有的 <&backend/tests/unit/test_device_sync_reports_failure.py> 两个用例
   都只造「有未提交改动」的仓库，所以这条路从没被测到。
2. **后端自己那棵 worktree 会把分支倒推**：开 PR 时从它那棵树打快照，`_catch_up_with_branch` 遇到该树有未提交文件
   （比如贴进话题的图片）就拒绝跟上，于是快照从旧树打、把分支倒推回你推的提交之前。函数自己的 docstring 早写明了这个
   后果，只是没人对那个 `False` 做处理。**解法**：把平台自己那个快照提交 merge 进来，让分支重新成为它的后代。
3. **`PrPollRunner`（60 秒一轮）没在转**：15:46 全绿到 16:11 一次都没推进；手动打
   `POST /admin/scheduler/poll-open-prs` 立刻就推。那个接口 30.5 秒返回 `{"cards_checked":4}`，
   而 `cheese api` 的读超时正好 30 秒，**所以它每次都在终点线前一步被客户端掐断，看起来像挂了——它没挂**。
   现在每次推代码我手动踢一次。

**已核实是虚惊的**：`4ecbeea2` 和 `b5074534` 两个工作区不存在滞留的活（前者一个提交都没落下，
后者那个 `feat(memory)` 已随 #589 进 main，内容逐字节相同）。**真正丢过活的只有 `94dad87f` 一个。**
