# 状态派生下沉后端

## 目标

把「一条活/一个房间现在是什么状态」这条派生逻辑，从前端搬到后端，做成一个没有副作用的**纯函数**，并让读接口直接返回结果。

之前这条逻辑在前端有两份（`lib/topicState.ts` 算房间、`lib/taskRing.ts` 算一条活），两份各写了一遍「在跑压过验收卡」这条规矩，其中一份的注释还指着另一份 —— 而 CLI、通知、以后任何客户端问的是同一个问题，却谁也读不到那两份 TypeScript。

## 现在到哪了

**后端这一半写完了**，在分支 `topic/44abcfb6` 上（已 push）。前端那条支线已经把 `taskRing.ts` 删掉、改成读 `presentation`，契约逐字对得上。

- 新模块 <&backend/app/domain/room_task/presentation.py> —— 纯函数：没有 I/O、不碰 session、不读时钟（「现在几点」是参数）。两个入口 `task_presentation` / `room_presentation`。
- 新批查询 `TaskRepository.last_block_at_for_tasks` —— 看板的心跳（见下面「失联」）。
- 四条读接口带上 `presentation`：`GET /projects/{id}/tasks`、`GET /topics`、`GET /topics/{id}`（房间和活两种形态都带）、`GET /topics/{id}/tasks`。
- 测试：<&backend/tests/unit/test_presentation.py>（36 条，表驱动，每一列每一句话各一例 + 两条优先级规矩）、<&backend/tests/integration/test_presentation_endpoints.py>（4 条，钉「四条接口给的是同一格」）。

## 契约（和 <&frontend/src/cx_types.ts> 逐字对齐）

```json
"presentation": { "column": "building|delivering|needs_you|done|archived",
                  "display_status": "<可直接显示的中文>" }
```

**列的判据是「该谁动」，不是「进行到哪一步」。** 同一个客观事实会因为下一步归谁而落在不同列：CI 红了，平台已经派芝士去修就是 `delivering`（「修复检查」），芝士推不上去、那个红没人能清就是 `needs_you`（「检查未通过」）。

| 列 | 产出的短语 |
|---|---|
| `building` 施工中 | 运行中 / 排队中 / 空闲 / 草稿 / **失联** |
| `delivering` 交付中 | 检查运行中 / 等待检查 / 修复检查 / 解决冲突 / 等待合并 |
| `needs_you` 等你 | 检查未通过 / 等待验收 / 交付被退回 |
| `done` 已完成 | 已采纳 / 已收工 |
| `archived` 已归档 | 已归档 |

## 几个关键决定

**「每一列只能说属于自己的话」是结构保证的，不是注释。** 列是**从短语推出来的**：每个短语是它那一列专属枚举（`Building` / `Delivering` / …）的成员，列由成员的类型查出来（`_show`）。没有任何一处代码分别挑一个列和一个短语 —— 显示一句不属于本列的话在结构上写不出来。

**词表里不留点不亮的值。** 一条测试（`test_every_phrase_is_reachable`）钉住「词表里有几句，就得有几个事实能点亮它」：加词而不加事实，它会红。这是「等待回答」被砍掉的那条规矩的另一半。

**「失联」读的是真心跳，不是 `last_turn_at`。** `Task.last_turn_at` 只在一轮**开始**时盖一次、跑起来不再刷新，所以它回答不了「这一轮现在还在动吗」。真正持续的信号是 block：一轮里每一步都落一个。在一条真实的活上实测 130 个 block —— **轮内间隔中位数 8 秒、p90 34 秒**（最大间隔 7193 秒是两轮之间的空档，那时本来就是 idle）。

所以 `LOST_SIGNAL_AFTER = 10 分钟`：是 p90 的十几倍，一段安静的工具活动撑不到它；而一条真的停住的活 10 分钟就现形，不用等两小时。取的是「最后一个 block」和「这一轮什么时候开的」里**晚的那个** —— 一条刚开跑还没说话的活靠后者兜底。

代价：热路由上多一次批查询。**不贵** —— `ix_blocks_task_id_created_at` 就是为 `(task_id, created_at)` 建的部分索引，这是一次按索引取每组最大值，和侧栏每次都要跑的 `last_activity_for_topics` 同一个成本量级。

**这个数刻意不等于 `GHOST_RESIDENCY_AFTER`（2 小时）。** 那一步会**放掉别人的槽位**，早一步是破坏性的；这里只是在屏幕上说一句话，说早了改回来就是了。两种代价不一样，就不该是同一个数。

**「停住了」的码不重列一遍。** <&backend/app/domain/review/notes.py> 已经维护着那张表（`_STUCK`），它的注释也记过漏列的代价。新增的停住码会自动落到「等你 / 交付被退回」，而不是安静地被算成「还在等检查」。

## 没做的（和代价）

**「等待回答」这一格没做，而且刻意没进词表。** 它需要「这个地点有一条没人回答的 `cheese ask`」这个事实，而 `cheese ask` 写的只是一个 `BlockKind.message` 加 `meta={"options": [...]}` —— 既没有独立的表，也没有可查的 alert 行，只能扫 JSON，热路由上不能干。

- **要做得付的代价**：`blocks` 上一个部分索引（`meta ? 'options' AND NOT meta ? 'answered'`）+ 一次迁移。
- **不做的代价**（已知并接受）：芝士问了问题在等人时，这条活显示成 `building / 空闲`。这是错的，但错得不难看，比在契约里留一个永远不亮的值强。

## 一条顺带量到的、不属于本次改动的事

实测：本房间四条支线（含当时正在跑的这一条）在 `GET /projects/{id}/tasks` 里 `residency` **全是 `idle`**，而其中至少一条当时确实在跑。如果这不是一次偶然，那意味着 `residency` 在一轮进行中并不可靠地是 `running` —— 而「失联」挂在 `residency == running` 上，那它就点不亮。

这是 residency 记账那边的事，不在本次改动范围内。记在这里，是因为它会让「失联」看起来做了却不生效。
