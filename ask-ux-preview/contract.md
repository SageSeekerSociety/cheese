# 提问作答完整流程：实现步骤 / 真实契约 / 读写 owner / 必要反例

基线 head `21a94a90`。行号都是这一个 head 上的，改了就重查。
批准来源：用户答复「采用完整方案（推荐）」，基线 `5135b0cb` 的 20 页 PDF `c751255126…`。
八项目标全保留，多人作答本次另定、不偷加。

这份东西先给你审，审过才动代码。

---

## 一、真实契约（现状，已逐行核过）

### 1.1 建问题 —— `POST /topics/{topic_id}/ask`
`backend/app/api/routes/topics_messages.py:229-307`

| 项 | 现状 |
|---|---|
| 入参 | `{question: str, options: str[]}`，`question` 非空，`2 ≤ len(options) ≤ 4` |
| 落库 | `meta = {"options": [...], "asked": ...}` |
| `asked` | `str \| null`。发起那一轮的人的 handle；平台轮次是 `None`；轮次没记时退到 `last_summoner` |
| 通知 | `notify_question`（`announce.py:145`）投递给 `asked`；`asked=None` 时 `address(Event(asked=None))` 谁也不通知 |
| 广播 | `assistant_block` |

### 1.2 作答 —— `POST /topics/blocks/{id}/answer`
`backend/app/api/routes/topics.py:1228-1296`

| 项 | 现状 |
|---|---|
| 入参 | `{option: str, author: str}`，只有这两个字段 |
| 校验链 | `option` 非空 → `option in meta.options`（**逐字比对**）→ `meta.answered` 没有 |
| 落库 | `meta["answered"] = option`；`meta["answered_by"] = author`。**`asked` 不清** |
| 广播 | `block_updated` |
| 唤醒 | `receive_message` 发一条 `<@{seat}> {option}`。`seat` = 问题的署名者（还在名册上时），否则房间默认席位 |

### 1.3 读取端

| 位置 | 读什么 |
|---|---|
| `frontend/src/lib/blockDisplay.ts:66` `askOptions` | `meta.options` 是不是非空数组 |
| `frontend/src/lib/blockDisplay.ts:72` `askAnswered` | `meta.answered` / `meta.answered_by` |
| `frontend/src/components/room/RoomMessage.vue:269-286` | 未答出按钮、已答出「谁选了什么」 |
| `frontend/src/composables/useChatPanel.ts:141-156` `pickOption` | 调 `answerOptions`（`api.ts:1996`），成功 `timeline.replace`，失败弹 `e.message`，`askBusy` 清掉让按钮松开 |
| `backend/app/domain/block/repositories.py:810-855` | `meta.asked` + `meta.answered`，外加「题问出后被问的人打过字就不算在等」 |
| `backend/app/domain/room_task/presentation.py:507, 622` | `awaiting_answer` → 看板列 `needs_you`、状态句「待回答」 |
| `backend/app/api/routes/awaiting.py:120-135` | 「待我处理」清单，`reason='asked'` |
| `frontend/src/views/InboxView.vue:54-63, 105` | 渲染那一行，点进去只跳到房间 `{projectId, topicId}` |

---

## 二、契约缺口（这是要补的，不是 bug）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | **没有 `note`**。自由输入写不进去 | `answer_options` 只读 `body["option"]` |
| G2 | **自动补的「以上都不是」过不了校验** | `option not in options` 是逐字比对，补进来的字不在 `meta.options` 里 |
| G3 | **已答 = 400，没有更正路径** | `if meta.get("answered"): raise ValidationError(...)` |
| G4 | **没有版本号，没有幂等键** | 并发两次提交，后写的被拒但前面那次已经落库；重试同一次提交拿到的是 400 而不是幂等成功 |
| G5 | **回执是单值，装不下「原答案 + 更正」** | `answered` / `answered_by` 各只有一个 |
| G6 | **待办行不指到题** | `WaitingItem` 没有 `blockId`，`InboxView` 只跳房间 |
| G7 | **多题没有实体** | `cheese_ask` 一次一题，`meta` 是每块自己的 |
| G8 | **草稿没有持久化** | `useChatComposer` 的 `outbox` 是内存 `Ref`；全前端只有 theme / token / user / sidebarWidth 用 localStorage |

---

## 三、新契约（提案）

### 3.1 `meta` 形状

```ts
meta = {
  options: { text: string; explain?: string; reject?: boolean }[],  // 2-4 项，reject 至多 1
  asked: string | null,          // 不变：在等谁
  ask_group?: string,            // 同一批题的批次 id，多题成组用；不给就是单题
  answer_log: {                  // 有序，末项是当前生效的那一版
    v: number,                   // 从 1 起
    option: string | null,       // 选的哪一项的 text；纯自由输入时为 null
    note: string | null,
    by: string,
    at: string,                  // ISO-8601
  }[],
}
```

**`answered` / `answered_by` 删掉**，所有 reader 改读 `answer_log.at(-1)`。不做长期 compat 转发，新旧一起改（`blockDisplay.askAnswered`、`repositories._awaiting_an_answer`、catalog fixtures 一次改完）。

`options` 从 `string[]` 升成对象数组，同理：**建问题端点和所有 caller 同时改**，`askOptions` 改返回 `{text, explain, reject}[]`。

### 3.2 建问题 —— `POST /topics/{topic_id}/ask`

```jsonc
{
  "question": "这次的作业按哪种方式收？",
  "options": [
    { "text": "课程平台收文件", "explain": "统一入口，助教一次收齐" },
    { "text": "发到课程邮箱",   "explain": "适合大文件" }
  ],
  "allow_other": true,     // 可以只写 note 不选项
  "reject_option": true,   // 自动补一项「以上都不是」，标 reject
  "ask_group": "…"         // 可选；同一批题给同一个
}
```

`allow_other` 和 `reject_option` **是两个开关，不合成一个**：
- `allow_other` = 允许「只写自由输入」这种回答（`option` 可为 `null`）。
- `reject_option` = 选项里多一项文字「以上都不是」，它是**合法选项**，不是被校验拒绝的项。

多题不新建后端「题目组」实体：多题就是时间线上多个 ask 块，靠 `ask_group` 归组，切换和总进度由前端算。理由是 `answer` 的语义本来就是 per-block 的，多一个实体就要多一套「部分作答怎么算」的规则，和现有 `Block` 的独立性打架。

### 3.3 作答 / 更正 —— `POST /topics/blocks/{id}/answer`

```jsonc
{
  "option": "课程平台收文件",   // 可为 null（allow_other 时）
  "note": "另外请开一个补交通道", // 可为 null，≤2000 字
  "author": "wangchangxin",
  "expect_version": 1,          // 并发控制；不给就跳过校验（不推荐）
  "client_op_id": "…"           // 幂等键
}
```

处理顺序：

1. `option` 与 `note` 都空 → 400。
2. `option` 非空且不在 `options[].text` 里 → 400「不在选项里」。`reject` 项的 text 是合法的。
3. 已有 `answer_log` 时走**更正**，三条闸门：
   - `author != answer_log[-1].by` → 400「只有原答者能更正」 ← 跨用户授权边界
   - `expect_version != answer_log[-1].v` → 409，并发安全
   - 同 `client_op_id` 已存在 → 幂等返回那一版，**不追加、不重复唤醒**
4. 追加 `{v: len+1, option, note, by, at}` 进 `answer_log`（**旧版本留着**）。
5. 发布 `block_updated`。
6. 唤醒：`receive_message` 发 `<@{seat}> {option 或 note}`；更正时发 `<@{seat}> 更正：…`。

### 3.4 更正的唤醒语义（按你上一轮的更正写准）

**复用现有 `receive_message`，不新造机制、不重放原回答。**

- 出题席位**空闲** → 正常唤醒，接下一轮（不是「为了不开新轮而永不处理」）。
- 出题席位**还在跑** → 走平台既有的安全注入/排队（`chat.notify_running_turn` / `merge_into_running_turn`）接新指令。
- 边界保持：同话题、同席位、不新开任务卡。

依据：`answer_options` 今天的唤醒就是 `receive_message`，而 `runtime.receive_message`（`runtime.py:204`）→ `post_user_message` 里本来就分「在跑就合进那一轮 / 空闲就起一轮」两叉。更正走同一条路，只换正文。

### 3.5 草稿与恢复

**草稿只在本地，不落库。** 草稿是用户还没提交的想法，推到服务端就等于多一套可见性与权限规则，而这一项要的只是「刷新不丢」。

- 存 `localStorage`，键 `ask-draft:{topicId}:{blockId}`，值 `{option, note, at}`。
- 未提交提示：有草稿的题在房间里出一个小标记，说清「还没提交」。
- 提交成功即删草稿。
- 「刷新后 pending」= 已提交但 `block_updated` 还没回来的那些，也落 `localStorage`（`ask-pending:{topicId}:{blockId}`），回来即删；刷新后仍挂着的就重试（幂等键保证不重复记）。

### 3.6 稍后找回（复用现有待办入口）

**不新建入口。** 「待我处理」那条链路已经是这件事的正确位置，`REASON_ASKED = "asked"` 的注释原话就是「芝士停在一个待确认问题上，只有他能回答」（`delivery/addressing.py:68`）。

只补一个缺口：`WaitingItem` 加 `blockId`，`InboxView` 点行跳到那道题（房间 + 定位到 block），而不是只跳房间。看板的「待回答」列、通知、待办三处共用 `address()` 那一份判据，不动。

**不把项目邀请合并进来** —— 项目邀请是另一条关系（`Event.audience` / `machine_owner`），语义不同，合了就分不清「谁该答这道题」。

---

## 四、读写 owner

| 谁 | 读 | 写 |
|---|---|---|
| 建问题 `topics_messages.py:229` | 轮次发起人 / `last_summoner` | `meta.options` / `asked` / `ask_group` |
| 作答 `topics.py:1228` | `meta.options` / `answer_log` | `meta.answer_log`（追加） |
| 通知 `announce.py:145` | `meta.asked` | — |
| 待办判据 `repositories.py:810` | `meta.asked` + `meta.answer_log` | — |
| 看板 `presentation.py:507, 622` | `awaiting_answer` 布尔 | — |
| 唤醒 `runtime.py:204` | 消息正文里的 `@` | — |
| 前端渲染 `blockDisplay.ts:66` | `meta.options` / `answer_log` | — |
| 前端动作 `useChatPanel.ts:141` | — | 调 API |
| 前端待办 `InboxView.vue:54` | `reason='asked'` | — |
| 前端草稿（新增） | localStorage | localStorage |

一句话：**`meta` 只有 `answer_options` 一个写入方**（建问题端写 `options`/`asked`/`ask_group`，之后不再动它们）；`asked` 由谁写清、谁不清，是 §1.1 / §3.1 那条。

---

## 五、必要反例（每条都要有会红的测试）

| # | 输入 | 必须 |
|---|---|---|
| R1 | `option` 空且 `note` 空 | 400 |
| R2 | `option` 不在 `options[].text` 里 | 400「不在选项里」 |
| R3 | 点 `reject` 项（「以上都不是」） | **成功**，不是 R2 |
| R4 | A 答完，B 想更正 | 400「只有原答者能更正」 |
| R5 | A 并发两次提交，`expect_version` 都是 1 | 第二次 409 |
| R6 | A 用同 `client_op_id` 重发 | 200，`answer_log` 长度不变，**不重复唤醒** |
| R7 | `options` 少于 2 项或多于 4 项 | 400（现状 `2 ≤ len ≤ 4`） |
| R8 | 平台轮次问的题（`asked=None`） | 谁都能答；更正仍限原答者；`notify_question` 谁也不通知 |
| R9 | 已答的题再点按钮 | 前端不出按钮（现状 `!askAnswered`）；直接打 API 走更正路径 |
| R10 | A 更正两次 | `answer_log` 三版都在，末项生效，回执显示「更正为「X」（原选「Y」）」 |
| R11 | 出题席位在跑时更正 | 注入/排队接新指令，**不重放原回答** |
| R12 | 草稿写了、刷新、不提交 | 草稿回来，且**不标已答** |
| R13 | 提交成功后刷新 | 回执在，草稿没了 |
| R14 | `WaitingItem` 里 `reason='asked'` 的行 | 能点到那道题本身，不只是房间 |

---

## 六、实现步骤（四段，每段独立可验）

| 段 | 做什么 | 覆盖目标 | 真实链路证据 | 未验 |
|---|---|---|---|---|
| **A 契约层** | `meta.options` 升对象数组、`answer_log`、`note`、reject 项、更正三闸门、幂等键。只后端 + 后端集成测试，无 UI 变化 | 5、6 | 后端集成测试对真库跑 R1–R11 | UI 未接 |
| **B 多题与解释** | 建问题端 `allow_other`/`reject_option`/`ask_group`；前端多题切换 + 总进度 + 选项解释 + 自由输入，接真实 API | 1、2 | 真 API 调用链（建题 → 作答 → 回执） | 刷新恢复未做 |
| **C 草稿与找回** | 草稿 localStorage、未提交提示、pending 重试、`WaitingItem.blockId` + 定位 | 3、4、7 | 真浏览器 reload 后草稿在、不标已答 | 跨设备不保（本地存储，如实说） |
| **D 归属与接续** | 更正后仍归原执行者、忙时注入/排队、空闲正常唤醒 | 8 | 真轮次：空闲起一轮 / 在跑注入，两条各留证据 | — |

每段收口：固定 head、覆盖了哪几项、哪些未验，中文 PDF 只在**用户可见**的修订上出。

---

## 七、这几处要你拍板

1. **`options` 升对象数组**要不要按上面这么做（所有 caller 同时改，不留 `string[]` 分支）？
2. **多题不新建后端实体**，靠 `ask_group` 归组、前端算进度 —— 认不认？
3. **草稿只在本地**，跨设备不保 —— 认不认？
4. **`answered`/`answered_by` 删掉**改读 `answer_log.at(-1)`，四处 reader 一起改 —— 认不认？
5. `AskFlow.vue` 1077 行超 `frontend/src` 的 1000 上限（分支上原有的，不是这次合 main 造成的）：**拆**还是**登记豁免**？

其余按 §6 顺序推进，不再问。
