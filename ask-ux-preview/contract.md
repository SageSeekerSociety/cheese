# 提问作答完整流程：实现步骤 / 真实契约 / 读写 owner / 必要反例（v3）

基线 head `21a94a90`（契约行号实查），文档 v2。
批准来源：用户答复「采用完整方案（推荐）」，基线 `5135b0cb` 的 20 页 PDF `c751255126…`。八项目标全保留，多人作答本次另定、不偷加。

**v2 相对 v1 改了什么**（v1 已由 wangchangxin 审过，这里只列 delta）：
五点实现决策全部采用并加上他给的前提；补进 A–E 五条遗漏（已批动作不能只列标题、`allow_other` 必须持久化、授权只信 `actor.handle`、落库后唤醒失败的补送、P1 不做「只后端先上」）；多题生成与批次提交的 caller/载荷写死；`answer_log` 加 `kind` 以免伪造合法项；补两条可靠性反例。

---

## 一、已定的实现决策（wangchangxin 2026-09-30）

| # | 决策 | 前提 |
|---|---|---|
| 1 | `options` 对象化，所有 caller 同改，不留 `string[]` 分支 | — |
| 2 | 不新建 group 后端实体 | 题组**一次创建、固定顺序与总数**；归组键后来收准为 `(topic_id, asked_by, group_id)` 三元组（见 4.2）；**不许凭临时已有 blocks 算总进度** |
| 3 | 草稿只在本地 | 键必须含**账号身份**；换账号读不到、也提交不了上一位的草稿 |
| 4 | `answer_log` 末版生效，删 `answered`/`answered_by` | **同批数据迁移保全历史答案与署名**；旧作答时间未知就**留空不伪造**；走正常迁移 pipeline |
| 5 | `AskFlow` 按真实职责拆，保住已批界面与行为 | **不登记新豁免，不放宽文件守卫** |

---

## 二、真实契约（现状，逐行核过）

### 2.1 建问题 —— `POST /topics/{topic_id}/ask`（`topics_messages.py:229-307`）

| 项 | 现状 |
|---|---|
| 入参 | `{question: str, options: str[]}`，`question` 非空，`2 ≤ len(options) ≤ 4` |
| 落库 | `meta = {"options": [...], "asked": ...}` |
| `asked` | `str \| null`。发起那一轮的人的 handle；平台轮次 `None`；轮次没记时退到 `last_summoner` |
| 通知 | `notify_question`（`announce.py:145`）投给 `asked`；`asked=None` 谁也不通知 |
| 广播 | `assistant_block` |
| CLI | `backend/sandbox/cheese:484-495` 工具 schema `{question, option: string[]}`，`:726-734` 转成 `{question, options}`，**一次只出一题** |

### 2.2 作答 —— `POST /topics/blocks/{id}/answer`（`topics.py:1228-1296`）

| 项 | 现状 |
|---|---|
| 入参 | `{option: str, author: str}`，只有这两个 |
| 校验链 | `option` 非空 → `option in meta.options`（逐字）→ `meta.answered` 没有 |
| 落库 | `meta["answered"]` / `meta["answered_by"]`。`asked` 不清 |
| 顺序 | **先 `db.commit()`，再 `publish`，再 `receive_message`** ← 落库后唤醒失败就永久不接续 |
| 唤醒 | `receive_message` 发 `<@{seat}> {option}`；`seat` = 问题署名者（还在名册上时），否则默认席位 |

### 2.3 读取端（真实 reader 全清单）

| 位置 | 读什么 |
|---|---|
| `frontend/src/lib/blockDisplay.ts:66, 72` | `meta.options` / `meta.answered` / `meta.answered_by` |
| `frontend/src/components/room/RoomMessage.vue:269, 282` | 未答出按钮、已答出「谁选了什么」 |
| `frontend/src/composables/useChatPanel.ts:141-156` | `pickOption` → `answerOptions`（`api.ts:1996`） |
| `frontend/src/components/NeedsYou.vue:161` | 待我处理里 `reason='asked'` 那一节 |
| `frontend/src/views/InboxView.vue:54-63, 105` | 待办行，点进去只跳房间 `{projectId, topicId}` |
| `frontend/src/cx_types.ts:215-231` | `WaitingItem`（`reason: 'reviewer'\|'reporter'\|'asked'`） |
| `backend/app/domain/block/repositories.py:806, 815, 837` | `meta.options` 是否存在、`meta.answered`、`meta.asked` |
| `backend/app/domain/room_task/presentation.py:507, 622` | `awaiting_answer` → 列 `needs_you`、状态「待回答」 |
| `backend/app/api/routes/awaiting.py:120-135` | 待我处理清单 |
| `backend/sandbox/cheese:726-734` | CLI 出题 |
| `frontend/src/views/demo/catalogFixtures.ts` | 预览夹具 |

`Block.meta` 是 `JSON` 列（`block/models.py:296`），所以「迁移」= 改 JSON 行内容，不改表结构。

### 2.4 可靠投递 seam（本次要挂上去的那个）

`backend/app/domain/delivery/agent.py`，模块原话：*The ledger owns intent; the runner owns admission. A transport receipt ends delivery independently of model-work completion. **An interrupted sending attempt is uncertain, never permission to inject the instruction a second time.***

| 函数 | 作用 |
|---|---|
| `record_agent(session, event, *, topic_id, instance_id, content)` | **在生产者的事务里、不做 I/O** 记一条投递意图。`state="pending"`，`dedup_key=dedup_key(event.id, seat)`，`on_conflict_do_nothing` |
| `dispatch_pending(sessions, *, chat, runner)` | 认领 `pending/claimed/sending` 且租约到期的行（`with_for_update(skip_locked=True)`），`runner.submit(..., addressed=addressed_to_agent(seat))` 真正唤醒 |
| `begin_send` / `run_attempt` / `receive_attempt` | 认领、续租、回执结算 |
| 状态 | `pending` / `claimed` / `sending` / `uncertain` / `failed`；中断的 `sending` 变 `uncertain`，**不重放** |
| 常量 | `LEASE_SECONDS=120`、`RETRY_SECONDS=30`、账本 `MAX_ATTEMPTS=5` |

人那侧的账本在 `delivery/ledger.py`：`Ledger.deliver` = `record`（去重键唯一）再 `send`，`resend_unsent_deliveries` 定时补发，`dedup_key(event_id, handle)`。

#### 2.4.1 这条链实际怎么走（逐行核过，不是借来的承诺）

`dispatch_pending` 认领后调 `runner.submit(..., delivery_id=..., recipient_instance_id=...)`（`delivery/agent.py:186-195`）。`submit` 把 `delivery_id` 传给 `run_attempt` 续租（`runtime.py:866-869`），一轮内部再由 `chat.py:4610-4615` 调 `begin_send` 把 `claimed → sending`，并**先把 prompt 登记进 `_pending_receipts`**（注释：*Staging a prompt on a booting machine is not receiver input. Register before send so a fast native receipt cannot race it.*）。

**回执**是收方真读到 prompt 时才落的：`chat.py:1320-1360` 拿 `_pending_receipts` 里的 `(prompt_text, block_ids, turn_id)` 与实际收到的 prompt 做**相等或前缀**匹配，匹配上才在同一事务里做 `BlockRepository.mark_consumed(block_ids, turn_id)` + `receive_attempt(session, turn_id, now)`。`chat.py:1332-1334` 明说这两件事分工：👀 记号是尽力而为的，**`mark_consumed` 才是「下一轮不再重发」的功能边界**。

所以「消费回执」**存在**，形态就是 `mark_consumed` + `receive_attempt`，触发条件是 prompt 文本匹配。**`_pending_receipts` 是进程内存**（`self._pending_receipts.setdefault(topic_id, []).append(...)`，`chat.py:4619-4621`）。

**`submit` 不落时间线**。`_run` 的入参注释写着 *Human message already persisted by `receive_message`*（`runtime.py:2140-2141`），而 `post_user_message` 只在 `runtime.py:240` 的 `receive_message` 里调。走 `record_agent` 的投递，`begin_send` 登记的 `block_ids` 是**空列表**（`chat.py:4620`）——即它只喂 prompt，不产生可见的对话行。

**回执匹配今天只有文本、没有投递身份**，两处要在 P1 里修（wangchangxin 2026-09-30 核过同一份代码）：

- 命中条件只有 **topic + 文本相等/前缀**（`chat.py:1320-1331` 的 docstring：*the receipt matches on equality or on carrying our text as its prefix*）。两批相同题面/相同答案、两个席位在同一个 topic 里，就会把**第一条文本的回执记到另一笔 delivery 上**。
- `pending.remove(entry)` 在 `mark_consumed` / `receive_attempt` / `commit` **之前**（`chat.py:1339-1340` 摘除，`:1343-1355` 才落库）。那三步抛异常时外层 `except` 只 `logger.exception`（`:1356-1359`），候选已经摘掉 —— **这份内存候选就丢了**，回执再迟到也没有对象可对。注释里 *a failed stamp just replays* 只对「摘除之前就失败」成立。

**忙/闲**：`submit` 的 docstring 明写 *Turns on the same topic serialize on ChatService's per-topic lock (so a second submit queues behind the first)*（`runtime.py:816-817`）——**排队，不是丢弃**。`_consume_message` 再按 `(topic_id, recipient_handle)` 开一条 per-recipient 锁（`runtime.py:928-930`），并先 `await self._wait_to_start()`。同一席位的两条消息顺序化，不同席位互不阻塞。

#### 2.4.2 三种故障窗口的证据（**不承诺「所有崩溃都恰好一次/永不不接续」**）

| 窗口 | 落库状态与证据 | 结果 |
|---|---|---|
| **A. `pending` 前后**（生产者事务已提交，尚未被认领） | 行在 `state='pending'`、`sent_at` NULL。意图在**生产者事务里**写成，进程死了也还在 | `dispatch_pending` 会认领补送。**不丢**。重投靠 `dedup_key` 的 `on_conflict_do_nothing` 不重复记账 |
| **B. `claimed`/`sending` 前后**（已联系或将要联系收方，回执没回来） | `run_attempt` 的 `finally`：`claimed` → 退回 `pending` + `retry_at`（`agent.py:272-279`）；`sending` → **`uncertain`**，`last_error="Receiver result was not confirmed; automatic replay is withheld"`（`agent.py:280-285`）。进程整个没了则租约到期，`dispatch_pending` 见 `sending` 一律改 `uncertain`（`agent.py:129-131`） | **`uncertain` 不自动再注入**。模块原话就是这一句 |
| **C. 迟到的真实回执** | `receive_attempt` 认 `sending` **和** `uncertain` 两种（`agent.py:330`），一条真回执就能结算成 `received`，并把 `TimedDelivery.delivered_at` 补上 | 可能迟到、也可能永远不来 |

**`uncertain` 今天没有对账/反馈路径**：`uncertain` 这个词在 `delivery/agent.py` 之外只出现在三处无关的地方（`machine/warm.py:497`、`skill_library/chat_detail.md:35`、`sandbox/cheese:216` 的 `request_id` 描述），**没有任何查询入口、没有对账任务、没有向人反馈的通道**。这是要补的（见 4.6.2），补之前**不许说「已接续」**，也**不许盲重放**。

反例 R34–R36 钉这三扇窗；R37 钉 `uncertain` 的对账与反馈，并钉「不许谎报已接续」。

---

## 三、契约缺口 G1–G9

| # | 缺口 | 证据 |
|---|---|---|
| G1 | 没有 `note`，自由输入写不进去 | `answer_options` 只读 `body["option"]` |
| G2 | 自动补的「以上都不是」过不了逐字校验 | `option not in options` |
| G3 | 已答 422，没有更正路径 | `if meta.get("answered"): raise` |
| G4 | 无版本号、无幂等键、无持久存储 | 并发后写被拒但前一次已落库；重试拿 422 |
| G5 | 回执单值装不下「原答案 + 更正」 | `answered`/`answered_by` 各一个 |
| G6 | 待办行不指到题 | `WaitingItem` 无 `blockId` |
| G7 | 多题没有实体，也没有一次建一组的入口 | CLI 一次一题 |
| G8 | 草稿无持久化，且没有账号隔离 | `outbox` 是内存 `Ref` |
| G9 | **落库后唤醒失败永久不接续** | `db.commit()` 在 `receive_message` 之前，而重试只返回旧答案 |

---

## 四、新契约

### 4.1 `meta` 形状

```ts
meta = {
  options: { text: string; explain?: string }[],  // 提问方给的，2-3 项（见 4.4）
  asked: string | null,          // 不变：在等谁
  allow_other: boolean,          // 作答许可，**建题时写入 meta 持久化**（见 4.4）
  reject_option: boolean,        // 界面自动补「以上都不是」；不在 options 里
  ask_group?: {
    id: string,                  // 同一批题共用；**组员与顺序据 id 保存/查验**
    members: string[],           // 本组全部 block_id，建题那一刻定死、按 index 顺序
    index: number,               // 固定顺序，0 起
    total: number,               // 固定总数（== members.length）
    asked_by: string | null,     // 原出题席位
  },
  answer_log: {                  // 有序，末项是当前生效的那一版
    v: number,                   // 从 1 起，初答 v=1（`expect_version` 初答给 0）
    kind: 'option' | 'note' | 'reject',  // 不伪造合法项（见 4.4）
    option: string | null,       // kind='option' 时是 options[].text；其余为 null
    note: string | null,
    by: string,                  // resolver.resolve 出来的 actor.handle
    at: string | null,           // ISO-8601；迁移来的旧答案时间未知 → null，不伪造
    client_op_id: string,        // 幂等键，**持久存在这里**
  }[],
  group_settle?: {               // 批次提交的结果，同值复制到组内每一块（见 4.5）
    v: number,                   // settle 自己的版本，从 1 起
    at: string | null,           // 迁移来的 → null，不伪造
    by: string,
    answered: string[],          // block_id
    later: string[],             // 明确稍后
    unanswered: string[],        // 明确未答
    payload_hash: string,        // 三列表 + 各项 client_op_id 的稳定摘要
    client_op_id: string,        // 整批的幂等键，**持久存在这里**
    delivery_event_id: string,   // 这一批那条独立唤醒的 event_id，可对账
  },
}
```

`answered` / `answered_by` **删掉**。`options` 从 `string[]` 升对象数组。所有 reader/writer **同一批原子切换**（见 4.7），不留 compat 双路。

### 4.2 建问题

```jsonc
POST /topics/{topic_id}/ask
{
  "questions": [                       // 1-8 题，一次给全
    {
      "question": "这次的作业按哪种方式收？",
      "options": [
        { "text": "课程平台收文件", "explain": "统一入口，助教一次收齐" },
        { "text": "发到课程邮箱",   "explain": "适合大文件" }
      ],
      "allow_other": true,
      "reject_option": true
    }
  ],
  "ask_group": "…"                     // 可选；不给则服务端生成并在响应里回
}
```

**一次建一组，一个事务写完全部块**：顺序就是数组顺序，总数就是数组长度，两者在建题那一刻定死并写进每块的 `ask_group`。**组员按 id 存进 `ask_group.members`**，之后一切校验与总进度都据这份 id 清单，**不从「时间线上已有哪些块」倒推组员**，所以中途有人插一条消息、或者另一批题落地，都不会把总进度算错。

归组键 = **`(topic_id, asked_by, group_id)` 三元组**。只用「同 topic + 同席位」会把同一席位连出的两批题并成一组；只用 `group_id` 则跨 topic/跨席位可能撞。三个一起才是唯一的一组。

### 4.3 多题的 caller 与「一次生成真实多题」

| 问题 | 答案 |
|---|---|
| 怎样一次生成真实多题 | `cheese_ask` 的入参从一题改成一组：`questions: [...]`（1–8）。一题就是组大小 1，同一条路。工具 schema（`backend/sandbox/cheese:484-495`）和实现（`:726-734`）一起改 |
| 谁提供 group 完整清单 | **出题的那个 agent，就在这一次 `cheese_ask` 调用里给全**。服务端只负责原子落库、固定顺序与总数。不接受「先建两题、过一会儿再补第三题」——那正是会让总进度算错的做法 |
| 最终/部分提交交给执行者什么 | 见 4.5。一次提交把「交了哪几题、哪几题明确未答、哪几题标了稍后」一次说清，唤醒文案照实写，**不得称整组全答** |

### 4.4 作答 / 更正

```jsonc
POST /topics/blocks/{id}/answer
{
  "kind": "option" | "note" | "reject",
  "option": "课程平台收文件",   // 仅 kind='option'
  "note": "另外请开一个补交通道", // 可与 option 同给（选项 + 解释），≤2000 字
  "author": "wangchangxin",     // 只作 fallback 寻址，不参与授权
  "expect_version": 0,          // 必带。初答 0，之后是 answer_log[-1].v
  "client_op_id": "…"           // 必带，持久存在 answer_log 里
}
```

处理顺序（**鉴权在任何查重/返回之前**；「幂等在版本拒绝前」说的是幂等与版本的关系，不是幂等与鉴权的关系）：

1. `resolver.resolve(fallback_handle=body["author"], …)` 取 `actor`，`authorize_topic(actor, …)`。**授权比较一律用 `actor.handle`**；`body.author` 不参与授权（`auth.py:80` 起 `ActorResolver`，其 docstring 明写 *Legacy authorship fallback does not authenticate*）。这一步不过就 401/403，**在它之前不查重、不读 `answer_log`、不返回任何题面状态**。
2. **幂等查重在版本拒绝之前**：同 `(block_id, actor.handle, client_op_id)` 已存在 →
   - payload 与已存那一版**相同** → 200 返回那一版，不追加、不唤醒
   - payload **不同** → 409「同一个 client_op_id 换了内容」
3. `kind='option'` 时 `option` 必须在 `options[].text` 里，否则 422「不在选项里」。`kind='note'` / `'reject'` 时 `option` 必须为空 —— **不伪造合法项**。
4. `kind='note'` 需要 `allow_other`（读建题 meta，不是读请求）；没有就 422「这道题不接受自由输入」。
5. `note` 与 `kind='option'` 可同给（选项 + 补充）。
6. 已有 `answer_log` 走**更正**：
   - `actor.handle != answer_log[-1].by` → 422「只有原答者能更正」
   - `expect_version != answer_log[-1].v` → 409
   - 旧版本留着，追加 `{v: expect_version + 1, …}`
7. **CAS / 行锁**：`SELECT … FOR UPDATE` 锁住这一块，再校验

   ```
   len(answer_log) == expect_version
   ```

   —— **旧日志长度等于 `expect_version`**。初答 `expect_version=0`、`answer_log` 空（长度 0）→ 0 == 0 成立 → 写第 1 版（`v=1`）。第一次更正 `expect_version=1`、长度 1 → 成立 → 写 `v=2`。

   **不是** `len == expect_version + 1` —— 那个式子在初答时要求 `0 == 1`，会把第一次作答整个拒掉。落败的那个拿 409。锁与校验必须在**同一个事务**里，否则「锁了再读到旧值」不成立。
8. **同事务**里做三件事：写 `answer_log`、落时间线那条 `<@{seat}> …` 消息块（**只落这一份回答文本**，见 4.6.1）、`record_agent(event_id, content=唤醒文案)` 记投递意图。
9. `db.commit()`。
10. 之后才 `publish`（`block_updated` + 新消息块）。广播是尽力而为，状态已经落库，前端可以重取。

`event_id` 用 `event_id_for(type, f"{block_id}:{v}")` —— **只取决于 block 和版本**，所以同版本重算是同一个 id、`record_agent` 的 `on_conflict_do_nothing` 保证不重复记账；不同版本是不同的 event，更正的唤醒不会被第一版的去重键吞掉。

**状态码跟仓库现有的类走**（`app/core/errors.py`）：内容不合法用 `ValidationError` → **422**（今天「不在选项里」「已由 X 选过」就是它，`test_answer_validates_option_and_single_shot` 钉的也是 422）；版本与并发冲突用 `ConflictError` → **409**。不用 400（那是 `BadRequestError`，本仓这条链路上没用过），也不用 403（那是 `ForbiddenError`，说的是「你不是这个话题的成员」，和「你在房间里但不能改别人的答案」不是一回事）。

`seat` 仍按现状：问题署名者（还在名册上时），否则房间默认席位。**`asked=None` 维持现有范围**，不新定多人规则。

### 4.5 批次提交

```jsonc
POST /topics/asks/{group_id}/settle
{
  "answered":   [{ "block_id": "…", "kind": "option", "option": "…", "note": "…", "client_op_id": "…" }],
  "later":      [{ "block_id": "…", "client_op_id": "…" }],   // 明确稍后
  "unanswered": [{ "block_id": "…", "client_op_id": "…" }],   // 明确未答
  "author": "…",
  "expect_version": 0,
  "client_op_id": "…"          // 整批的幂等键
}
```

- 三个列表**合起来必须恰好覆盖组内全部 `block_id`**，多一个少一个都 422 —— 这就是「一次表达所交题与明确未答/稍后题」。校验按 4.2 存下的 `ask_group.members` 逐个比：**三个集合各自无重复、彼此无交集、并集恰等于 `members`**（顺序不必一致）。
- `answered` 里的每一项按 4.4 的规则逐个写 `answer_log`（各自幂等键）。
- `group_settle` 写到组内**每一块**上（同值冗余），任何一块都能自己说清「本组 2/3 已交」，不用 join。
- **唤醒文案照实写**：`3 题里交了 2 题，1 题标了稍后（第 3 题）。` 而不是「整组都答完了」。文案由 settle 的三个列表拼，不从 `answer_log` 数量倒推。
- `later` / `unanswered` **不写 `answer_log`**（没答就是没答），只进 `group_settle`。
- 唤醒是**一条**独立投递：`event_id = event_id_for(type, f"group_settle:{group_id}:{v}")`，与 per-block 的 `event_id_for(type, f"{block_id}:{v}")` **分开**。**内部给每题写答案时不各发一条 agent delivery** —— 那是 n+1 次唤醒。一个 settle 只醒一次。
- p6「还有未答 → 回去补 / 照样交」：settle 允许 `unanswered` 非空（= 照样交，但明确标出没答的），也允许整组还没 settle 时用户点「回去补」。UI 两种都出。

**settle 自己也要能重试查重**（不只 per-block 那一层）：

- `group_settle` 持久存 `v` / `client_op_id` / `payload_hash`（4.1）。同 `(group_id, client_op_id)` 且 `payload_hash` 相同 → 200 返回原结果，不追加、不唤醒；`payload_hash` 不同 → 409。
- **锁全组按稳定顺序**：按 `ask_group.members` 的 id **升序**逐个 `SELECT … FOR UPDATE`。乱序加锁会让两个并发 settle 各锁一半后互相等，或者更糟地各写一半。
- **一个事务写完整组**：两题分别更正、重复 settle、某一项校验失败 —— **都不许半组落库**。任一项不通过，整个事务回滚，组内一块都不变。所以「第 1 题写进去了、第 2 题被拒」这种半成品状态在库里不可能出现（R30）。
- **已答的题不被 re-settle 覆盖**：`answer_log` 非空的题若出现在 `later`/`unanswered` 里，以 `answer_log` 为准 —— 答案留着、**不从进度里撤掉**，只在 `group_settle` 的文案里照实说「这题先前已答」（R31）。`later` 是「这题我等会儿再说」，不是「把我已经给的答案擦掉」。
- `group_settle.v` 与 per-block 的 `answer_log[].v` **是两套版本号**，互不替代。

### 4.6 更正的唤醒语义

**复用现有寻址，不新造机制、不重放原回答。** 唤醒走 4.4 第 8 步的 `record_agent` → `dispatch_pending` → `runner.submit(…, addressed=addressed_to_agent(seat))`：

- 出题席位**空闲** → 正常唤醒接下一轮（不是「不开新轮」就永不处理）。
- 出题席位**还在跑** → `submit` 的同一话题 per-topic 锁把新轮次**排在后面**（`runtime.py:816-817`），per-recipient 锁再保证同一席位的消息有序（`runtime.py:928-930`）；另有平台既有的安全注入/排队（`chat.notify_running_turn` / `merge_into_running_turn`）。
- 同话题、同席位、不新开任务卡。
- **不重放原回答**：更正是新的一版（新的 `v`、新的 `event_id`），不是把第一版再喂一遍。

#### 4.6.1 退役 direct `receive_message`，回答文本只留一份

今天 `topics.py:1290` 走的是 `broker.receive_message(content=f"<@{seat}> {option}")`，它**一通两用**：既落时间线那条可见的「他选了 X」，又靠 @ 解析把席位叫醒（`runtime.py:204` 起：*Persist one human message now, then deliver it to whoever it named*）。若同时改走 `record_agent`，就会**双唤醒**（`_consume_message` 一路 + `dispatch_pending` 一路），而且落两份文本。

所以切换是**换掉而不是叠加**：

- **回答文本只落一次**，就是 4.4 第 8 步那条 `<@{seat}> …` 时间线块。它必须带得上投递 identity：块的 `meta` 里存 `delivery_event_id`（= 4.4 的 `event_id_for(type, f"{block_id}:{v}")`），这样「这条可见文本」与「那条投递意图」能互相对上（R32）。
- **唤醒只走 `record_agent`**，不再对同一次作答调 `broker.receive_message`。
- 因为 `_run` **不落时间线**（`runtime.py:2140-2141`：*Human message already persisted by `receive_message`*），`record_agent` 那条只喂 prompt、`block_ids` 为空（`chat.py:4620`）——这正是「一份文本 + 一条可关联投递」的分工：文本给人看，投递给席位，两者同一个 `event_id`。
- 保留的 `broker.receive_message` 调用只剩真正的人类发消息入口（`chat.py:235`）。**作答链路上不再有第二条**（R33）。

#### 4.6.2 回执要认投递身份，`uncertain` 的可见不等于恢复

按 2.4.2，`pending`/`claimed` 能补送，`sending` 失回执只到 `uncertain` 且**不自动重放**。契约只承诺：**意图不静默丢失**（生产者事务内落库）。**不承诺所有崩溃都恰好一次**。

**先修两处，回执才配叫凭据**（最小改动，不另开通用投递重构）：

1. 回执核对持久 `NativeInput` 的项目、话题、席位、harness、原生会话、输入及 work 身份；关联 `delivery_id`、`attempt_id`、`event_id`。登记先 commit 再外发，不按文本选候选。回执只结算对应投递、消费和已阅效果，两批同文或同话题不同席位不串账（R40）。
2. 结算事务 commit 后才推进 journal 的 landed 游标。commit 失败保留回执和原身份，不只留日志，不用新输入 UUID 盲发（R41、R42）。accepted 只证 RPC 接受；native echo 只证输入回显，初始批次仍等原 work 完成消费。

登记与结算的锁图（批次段仍待真实 PG 竞争验证）：

```text
新登记：Delivery → NativeInput INSERT/唯一键冲突 → NativeInput 行 → Block[id 升序]
登记重试：Delivery → NativeInput 行 → 核对原登记并返回
native echo：无锁读不可变关联 → Delivery → NativeInput 行 → Block[id 升序]
accepted：无锁读不可变关联 → Delivery → NativeInput 行
完成 work：NativeInput[id 升序] → Block[id 升序] → 消费与持久释放 → commit
```

- 无关联投递时跳过 Delivery。输入插入和唯一键冲突必须在块锁之前，包含隐式等待；不能留下 Block→NativeInput 边。
- 两笔不同投递共享块时，各持自己的输入行，只按块 id 升序竞争。持块后查询其他输入的持有信息不加输入锁；不会反向等待对方输入行。无块交集的不同席位、话题、项目不设全局锁。
- 登记自己的未提交行不计入重叠检查。争抢失败必须回滚整笔登记，不得 commit 无效持有或外发。同身份重试仅核对，不替换身份或效果。
- accepted、unknown、echo 不能释放初始持有。完成消费只释放对应原 work 的块；其他 work 的消费标记不能替代。可验证未外发的显式释放与完整组效果仍待接线和证据。
- 完成 work 路径不锁投递，持输入锁后只取稳定块锁，不产生 Block→NativeInput 或 NativeInput→Delivery 边。释放集合单调写入，与原 work 消费同事务；后续轮次覆盖块标记不复活旧持有。失败、取消及不完整原生身份不释放。
- 完成事务失败必须向订阅传播，保留 result 和游标以便重试。全部执行器与恢复原执行者仍待接线；本图不是全链无死锁或全目标完成声明。旧已闭合锁测试不重复运行。
- 登记的 `work_id` 是发送时寻址，不是执行完成归属。新回显持久记录 `execution_work_id`；result 携带实际读取的 `completion_input_ids`。完成事务核对集合中每个输入的原生会话、席位、已回显及执行归属，只释放这份集合。
- late steer 若在 W 结束后才被读取，回显仍核原登记 W，但执行归属为 U。W 完成不能等待未读取的输入，也不能释放它；U 的可信输入完成可以释放。纯自发无输入集合、错误及中断不释放。
- 旧 runner 不会产生新盖章，也没有完成记录保留保证。现阶段不得把无盖章结果推进为已完成。发布前须实现并验证协议识别、保全旧日志的读取及原执行者升级接续；不能用重启共享 runner 或按发送时 W 猜归属替代。已被旧保留策略删除的原生记录不能由数据库登记补造完成证据。

**「列出 `uncertain` + 反馈」是可见边界，不是恢复实现。** 三者关系是这样：

- **可查**：`uncertain` 行有查询入口（topic 维度的投递列表，带 `state`/`last_error`/`attempts`/`sent_at`）。这只解决「看不见」，不解决「没送到」。
- **反馈**：超时未解决 → 向出题席位/房间发一条人看得见的提示「这条唤醒没确认送达，需要确认对方收到没有」。这也是提示，不是动作。
- **恢复只有一条路**：真实迟到回执 → `receive_attempt` 认 `uncertain` → `received`（`agent.py:330`）。这条有代码证据。**没有被真实回执推进过的状态，一律如实标「未确认」**，不许说已接续，也不许说已丢失（R37）。把 uncertain 说成「已送达」或「丢了」都是编。

安全 reconcile / 迟到回执的推进必须用**原 runner/hook 的真实数据**证明（真轮次里拿到 receipt 后 `receive_attempt` 落成 `received`）；证明之前它就是未确认。对账是**人/agent 判定 + 真回执**，不是定时盲重放 —— 模块原话：*never permission to inject the instruction a second time*。

### 4.7 原子切换（P1 不做「只后端先上」）

schema 变更 + 数据迁移 + **所有**真实 reader/writer 在**同一批（同一 PR 或同一原子 release）** 切换，不让旧 FE/CLI 坏在半路：

后端 `topics_messages.py`（建题）、`topics.py`（作答）、`block/repositories.py`（待办 SQL）、`delivery/agent.py`（唤醒）、alembic 迁移；
CLI `backend/sandbox/cheese`（工具 schema + 实现）；
前端 `blockDisplay.ts`、`RoomMessage.vue`、`useChatPanel.ts`、`api.ts`、`NeedsYou.vue`、`InboxView.vue`、`cx_types.ts`、`views/demo/catalogFixtures.ts`、`catalogAsk.ts`；
文档 `docs/manual/dev/*.md` 里 `cheese_ask` 那几处。

**不留 compat 双路**：没有「新旧 meta 都认」的分支代码。

### 4.8 数据迁移（决策 4）

alembic 一次数据迁移（`backend/alembic/versions/`，共 235 个，风格见 `f3a8c5d2e917_*.py`：模块 docstring 说明为什么、SQL 内联、downgrade 说明为什么不还原）：

- `meta.options` 的 `string[]` → `{text: string}[]`（无 `explain`）。
- `meta.answered` / `meta.answered_by` → `meta.answer_log = [{v: 1, kind: 'option', option: <原 answered>, note: null, by: <原 answered_by>, at: null, client_op_id: "migrated"}]`。
- **`at` 写 `null`，不伪造作答时间**（旧记录里没有这个信息）。
- `meta.asked` 原样不动。
- 旧块**不补** `allow_other` / `reject_option` / `ask_group` / `group_settle` —— 它们不存在就是不存在，读端按缺省处理（`allow_other` 缺省 false = 老题不接受纯 note；`reject_option` 缺省 false = 老题不自动补）。
- downgrade 说明不还原的理由。

### 4.9 草稿与恢复（决策 3）

- 键必须含**账号身份**：`ask-draft:{userId}:{topicId}:{blockId}`、`ask-pending:{userId}:{topicId}:{blockId}`。换账号读不到上一位的草稿，也提交不了。
- 草稿只在本地不落库。值 `{kind, option, note, at}`。
- 未提交提示：有草稿的题在房间里出一个小标记，说清「还没提交」。
- 提交成功即删。刷新后 pending = 已提交但回执未到，回来即删；挂着的重试（`client_op_id` 保证不重复记）。
- p9「稍后处理可找回」：`later` 进 `group_settle`，待我处理那条链路继续亮（见 4.10）。

### 4.10 稍后找回（复用现有待办入口，不合并项目邀请）

`REASON_ASKED = "asked"` 的注释原话就是「芝士停在一个待确认问题上，只有他能回答」（`delivery/addressing.py:68`）。看板「待回答」列、通知、待办三处共用 `address()` 一份判据，不动。

只补两处：
- `WaitingItem` 加 `blockId`，`InboxView` / `NeedsYou` 点行**定位到那道题**，不是只跳房间。
- 组内多题时 `blockId` 指组里第一道还没答的。

**不把项目邀请合并进来**（那是 `Event.audience` / `machine_owner`，另一条关系，合了分不清「谁该答这道题」）。

### 4.11 已批动作 → 实现落点（wangchangxin A）

| 已批行为 | 出处 | 实现落点 | 阶段 |
|---|---|---|---|
| 还有未答 → 回去补 / 照样交 | p6 | settle 允许 `unanswered` 非空 + UI 两个动作 | P2 |
| 失败重试不重复问确认 | p7 | `client_op_id` 幂等；重试直接重发同一 op | P2 |
| 稍后处理可找回 | p9 | `later` 进 `group_settle` + 待办 `blockId` 定位 | P3 |
| 按旧回答已开工的更正提示 | p18 | 更正时若出题席位已按旧答开工，回执与提示明说「已按原答案开工，更正会接下一轮」 | P4 |
| 批次提交一次说清交/未答/稍后 | — | 4.5 | P2 |
| 唤醒不误称整组全答 | — | 4.5 文案 | P2 |
| 草稿 / 刷新恢复 | — | 4.9 | P3 |
| 总进度、多题切换 | — | `ask_group` + 前端算 | P2 |

---

## 五、读写 owner

| 谁 | 读 | 写 |
|---|---|---|
| 建问题 `topics_messages.py:229` | 轮次发起人 / `last_summoner` | `meta.options` / `asked` / `allow_other` / `reject_option` / `ask_group` |
| 作答 `topics.py:1228` | `meta.options` / `allow_other` / `answer_log` | `meta.answer_log`（追加） |
| 批次提交 `topics.py`（新） | `meta.ask_group` / `answer_log` | `meta.answer_log` / `meta.group_settle` |
| 通知 `announce.py:145` | `meta.asked` | — |
| 待办判据 `repositories.py:806, 815, 837` | `meta.options` 存在性 / `answer_log` / `asked` | — |
| 看板 `presentation.py:507, 622` | `awaiting_answer` 布尔 | — |
| 投递意图 `delivery/agent.py:44` | — | `Delivery` 行（`state=pending`） |
| 投递执行 `delivery/agent.py:105` | `Delivery` 行 | 状态机 |
| 投递回执 `chat.py:1320` → `receive_attempt` | `_pending_receipts` 的 prompt 文本 | `Delivery.state='received'` + `mark_consumed` |
| `uncertain` 对账（新） | `Delivery` 行 `state='uncertain'` | 只经真实回执 → `received`；不自动改状态 |
| 唤醒寻址 `runtime.py:204` | 消息正文里的 `@` | —（**作答链退役**，见 4.6.1） |
| 前端渲染 `blockDisplay.ts:66, 72` | `meta.options` / `answer_log` | — |
| 前端动作 `useChatPanel.ts:141` | — | 调 API |
| 前端待办 `InboxView.vue` / `NeedsYou.vue` | `reason='asked'` + `blockId` | — |
| 前端草稿 | localStorage（含 userId） | 同左 |
| CLI `backend/sandbox/cheese:726` | — | 出题请求 |

`meta` 的写入方严格分两类：**建问题端写一次后不再动**（`options`/`asked`/`allow_other`/`reject_option`/`ask_group`），**作答与 settle 写**（`answer_log`/`group_settle`）。

---

## 六、必要反例（每条都要有会红的测试）

| # | 输入 | 必须 |
|---|---|---|
| R1 | `kind` 与内容都空 | 422 |
| R2 | `kind='option'` 但 `option` 不在 `options[].text` | 422「不在选项里」 |
| R3 | `kind='reject'`（界面上的「以上都不是」） | 成功；`answer_log.kind='reject'`、`option=null` |
| R4 | `kind='note'` 但建题 `allow_other=false` | 422「这道题不接受自由输入」 |
| R5 | `kind='note'` 且 `allow_other=true` | 成功；`option=null`，**不伪造合法项** |
| R6 | A 答完，B 想更正 | 422「只有原答者能更正」（比较 `actor.handle`） |
| R7 | A 并发两次，`expect_version` 都是 0 | 只出一版；落败那次 409 |
| R8 | 同 `client_op_id` 同 payload 重发 | 200，`answer_log` 长度不变，**不重复记账、不重复唤醒** |
| R9 | 同 `client_op_id` 换 payload | 409「同一个 client_op_id 换了内容」 |
| R10 | `expect_version` 与 `answer_log[-1].v` 不符 | 409 |
| R11 | `body.author` 写成别人的名字（有凭据时） | 仍按 `actor.handle` 记录与授权，`body.author` 无效 |
| R12 | 提问方给 1 项或 4 项 | 422（`2 ≤ 提问方选项 ≤ 3`，界面自动补的那项不计入） |
| R13 | `kind='reject'` 或纯 note | **不**把「以上都不是」写进 `options`，**不**造出一条假的 `options[].text` |
| R14 | 平台轮次问的题（`asked=None`） | 谁都能答；更正仍限原答者；谁也不通知 |
| R15 | 已答的题再点按钮 | 前端不出按钮；直接打 API 走更正路径 |
| R16 | A 更正两次 | `answer_log` 三版都在、末项生效；回执「更正为「X」（原选「Y」）」 |
| R17 | **提交成功但 HTTP 丢回包**（客户端没收到响应） | 客户端同 `client_op_id` 重试 → 200 返回已存那一版；`answer_log` 与 `Delivery` 都不新增 |
| R18 | **postcommit 通知故障**（`answer_log` 已提交、唤醒那步抛异常） | `Delivery` 行已在同事务里落成 `pending`，意图不丢；之后由 `dispatch_pending` 补送。**不承诺恰好一次**：`sending` 期间失回执只到 `uncertain` 且不自动重放（R35），对账见 4.6.2 |
| R19 | 出题席位在跑时更正 | 注入/排队接新指令，**不重放原回答** |
| R20 | 出题席位空闲时更正 | 正常唤醒接下一轮 |
| R21 | settle 的三个列表没覆盖全组 / 多出一个 | 422 |
| R22 | settle 后唤醒文案 | 明写「交了 X 题、稍后 Y 题、未答 Z 题」，**不得**称整组全答 |
| R23 | `later` 的题 | 不写 `answer_log`；待我处理里能按 `blockId` 找回 |
| R24 | 迁移前的旧答案 | `answer_log` 一条、`at=null`、`by` 是原 `answered_by`、`option` 是原 `answered`；**没有伪造的时间** |
| R25 | 草稿写了、换账号、刷新 | 上一位的草稿**读不到也提交不了** |
| R26 | 草稿写了、同账号刷新、不提交 | 草稿回来，且**不标已答** |
| R27 | 提交成功后刷新 | 回执在，草稿没了 |
| R28 | `reason='asked'` 的待办行 | 能点到那道题本身 |
| R29 | 一批题中途插进别的消息 / 另一批题 | 总进度不变（顺序与总数在建题那一刻定死） |
| R30 | settle 里第 1 题合法、第 2 题校验失败 | **整事务回滚**，组内一块都不变（不许半组落库） |
| R31 | 某题已答，settle 又把它放进 `later` | 答案留着、**不从进度撤掉**；文案照实说「这题先前已答」 |
| R32 | 回答文本与投递能不能对上 | 那条时间线块的 `meta.delivery_event_id` == `event_id_for(type, f"{block_id}:{v}")` |
| R33 | 作答一次的唤醒条数 / 文本份数 | **一条** agent 投递、**一份**回答文本；`broker.receive_message` 在这条链上不出现 |
| R34 | 窗口 A：`pending` 未认领时进程死了 | 行仍 `pending`/`sent_at` NULL，`dispatch_pending` 补送；同 `dedup_key` 重投不重复记账 |
| R35 | 窗口 B：`begin_send` 之后、回执之前进程死了 | 行变 `uncertain`，**不自动再注入**；`last_error` 是「Receiver result was not confirmed」 |
| R36 | 窗口 C：迟到的真实回执 | `receive_attempt` 认 `uncertain` → `received`，并补 `TimedDelivery.delivered_at` |
| R37 | 有 `uncertain` 行时 | 查询入口能看到它、超时向人反馈 —— 但**可见 ≠ 恢复**；恢复只认真实迟到回执（`receive_attempt`）；**未确认不许说已接续、也不许说已丢失** |
| R38 | settle 同 `client_op_id` 同 `payload_hash` 重发 | 200 返回原结果，`group_settle` 与 `Delivery` 都不新增 |
| R39 | 两个 settle 并发（同 `group_id` 不同 `client_op_id`） | 按 `members` id 升序加锁，只出一版；落败那个 409 |
| R40 | 两批相同题面/相同答案、两个席位同 topic | 回执按 `delivery_id` 归属，第一条文本的回执**不**记到另一笔 delivery 上 |
| R41 | 回执命中但 DB `mark_consumed`/`receive_attempt`/`commit` 失败 | `pending.remove` 在 commit **之后**，候选还在、可再对账；**不只留 logger** |
| R42 | native receipt 到了、commit 失败 | **不**把已读文本重新盲 inject；凭据仍在，下一条真实回执能把它推成 `received` |
| R43 | 状态没被真实回执推进过 | 如实标「未确认」；不许用「已列出 + 已反馈」当作恢复完成 |

---

## 七、实现步骤（四段）

| 段 | 做什么 | 覆盖目标 | 真实链路证据 | 未验 |
|---|---|---|---|---|
| **P1 原子 schema 切换 + 后端契约** | alembic 迁移、`answer_log`+`kind`、`note`/reject、`allow_other` 持久化、更正三闸门、`client_op_id` 持久化、CAS（`len(answer_log) == expect_version`）、`record_agent` 同事务记投递意图、**退役作答链上的 direct `receive_message`**（4.6.1）、`uncertain` 可查/对账/反馈（4.6.2）；**同批改完 §4.7 全部 reader/writer** | 5、6 | 后端集成测试对真库跑 R1–R20、R24、R32–R37（三种投递故障窗 + 真 runner 链），**发布前验** | 多题/草稿未接 |
| **P2 多题生成与批次提交** | `cheese_ask` 改成一次一组、建题端原子建组、`settle`、唤醒文案、总进度、p6/p7 | 1、2、5（部分） | 真 API 链（建一组 → 逐题/批量作答 → settle → 回执） | 刷新恢复未做 |
| **P3 草稿与找回** | 含 userId 的本地草稿、未提交提示、pending 重试、`WaitingItem.blockId` 定位、p9 | 3、4、7 | 真浏览器 reload 后草稿在、不标已答、换账号隔离 | 跨设备不保（如实说） |
| **P4 归属与接续 + 已批交互补齐** | 更正后归原执行者、忙时注入/排队、空闲正常唤醒、p18 更正提示、`AskFlow` 按职责拆 | 8 | 真轮次两条各留证据（空闲起一轮 / 在跑注入） | — |

每段收口报：固定 head、八项覆盖表、未验项。用户可见的修订才出中文 PDF。假服务 12/12、挂载 133 **不当**真实后端证据。

---

## 八、仍然不做的

- 多人作答（wangchangxin：本次另定，不偷加）。
- `AskFlow` 不登记新豁免、不放宽 `.claude/scripts/check-file-sizes.py` 的守卫。
- 不改生产/权限；dev 只走正常 CI/CD，无热改、无压测、不借他人 token。
- 不新建内部任务卡、不递验收、不等采纳；正常小 PR + Required + merge queue。
- `ask-ux-preview/` 里已批的 20 页 PDF 与预览成果原样保全。
