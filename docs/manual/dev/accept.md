---
title: 验收与采纳
kind: 参考
summary: 验收卡的状态机与状态码、合并态判定、采纳即合并、人工放行和孤儿卡收敛。
covers:
  - backend/app/domain/review/models.py
  - backend/app/domain/review/notes.py
  - backend/app/domain/review/merge_state.py
  - backend/app/domain/review/archive.py
  - backend/app/domain/review/gate_sweep.py
  - backend/app/domain/review/forge.py
  - backend/app/domain/review/services/service.py
  - backend/app/api/routes/accept.py
  - backend/app/domain/project/protection.py
---

# 验收与采纳 {#accept}

一张验收卡就是平台对**一个 PR** 的看法：它递给某一个具体的人，那个人点下采纳的那一刻，平台就当场调托管平台的合并接口。

> 讲：卡本身——状态机、状态码、合并态怎么算出来、采纳与人工放行各检查什么、孤儿卡谁来收、房间里说什么。不讲：交付链路每一步的顺序（见[任务 → 分支 → PR → 验收合并](/dev/delivery)），Forgejo / GitHub 两个实现和令牌（见[代码托管](/dev/forge)），这位交付属于清单上哪一项产物（见[资料库与产物](/dev/library)）。

## 一张卡是什么 {#card}

卡钉在**一个房间**上（`AcceptCard.topic_id`）：给一条活递的卡记下 `task_id`，给房间本身递的卡留 NULL。卡上写着递给**哪一个**人（`reviewer_handle`，不是广播），以及这次交付更新了清单上哪一项产物（`artifact_id`）。

硬规矩：协作模式（`AiMode.collaborative`）下 AI 不能采纳或批准自己的改动，必须由人操作（`_forbid_ai`）。判据认的是整个 `cheese-` handle 命名空间而不是 `cheese` 这个字符串——每个房间的芝士以 `cheese-<hex>` 的身份行动，只比字符串会让它们直接走过去。

## 状态机 {#status}

`AcceptStatus`（`review/models.py`）的每一个值，以及谁把它写进去：

| 状态 | 意思 | 谁写的 |
|---|---|---|
| `pending` | 递到验收人手上，等他决定 | `create_card` |
| `pending_gate` | 平台质量检查在跑（历史态） | 已无人写；退役前留下的行还在 |
| `accepted` | 已采纳（合并成功） | `accept` / 轮询器看到外部合并 |
| `rejected` | 人退回了 | `reject` |
| `revoked` | 作废（人工，或 PR 在 GitHub 上关闭且没有合并时由平台自动），或话题归档时被收敛 | `void` / 轮询器 / `archive.py` |
| `conflict` | 采纳时撞合并冲突，芝士被派去解，解完由人重试采纳 | `accept` 的冲突分支 |
| `gate_failed` / `gate_blocked` | 检查红了 / 检查根本没跑成（历史态） | 已无人写；行还在，要照常渲染 |
| `pr_open` | 已死的在途态，按先例保留 | 无 |

`gate_*` 三种和 `pending_gate` 今天都**没有生产者**——机器闸门在「采纳即合并」里退役了，卡是 PR 的一个视图，由那个 PR 上真实的 CI 说话（`review/gate.py` 只剩一个超时常量和一句恒为「没有」的 `in_flight_card_ids()`）。留在枚举里是为了历史行能读、能画。

## 状态码与那句话 {#notes}

`note` 和 `note_code` 是**两列**：`note` 是给人看的一句话，`note_code` 是给代码看的状态。它们过去是一个字段，判断靠 `note.startswith(带 emoji 的前缀)`——于是改一句文案就能改掉一次判断，而没有任何东西会红。现在判断读码，渲染读码算出来的级别（`note_level`），文案怎么写都不影响任何一方。

`NoteCode` 只收「被读到、或需要渲染成红」的状态；纯交代（合并成功了、被驳回了）的码是 `None`，级别按 info 走。按句子发码会让枚举变成文案表的影子，而影子会和本体走散。

`notes.py` 的三个函数是**唯一写入口**（别直接赋值 `card.note`，那正是让文案和状态分家的动作）：

| 函数 | 做什么 |
|---|---|
| `record(card, code, text)` | 卡进入一个新状态：文案和码一起落 |
| `annotate(card, text)` | 在现有 note 前面加一句，**不动码**（补一句说明，不是新状态） |
| `clear(card)` | 卡上没有要说的了 |

哪些码算「停住了」（`_STUCK`，决定画不画红）是一张表，新增的码**必须**在这里表态：漏掉就默认 info，和「还在等检查」长得一模一样——`🌿 分支分叉` 和 `🚪 PR 被关` 都在旧的 emoji 数组里踩过这个坑。

## 合并态怎么算 {#merge-state}

`merge_state.compute_merge_state` 是纯函数、无 I/O：输入是托管平台那侧的信号（`mergeable_state`、`mergeable`、check runs、改动文件清单、与基线的 ancestry）和项目的分支保护参数，输出一个 `MergeVerdict`——`state` 是卡上那个词，`reasons` 是它的依据。

| state | 什么时候 |
|---|---|
| `clean` | 没有任何东西拦着 |
| `unstable` | 有检查没过，但**没有一个是必跑的**（必跑名单整份放行过，所以这句话是已知的，不是猜的） |
| `blocked` | 必跑检查红了 / 缺席 / 还在跑；或 PR 还是 draft |
| `behind` | `strict` 且落后基线（`behind` 和 `diverged` 都算落后） |
| `dirty` | 与目标分支冲突 |
| `unknown` | GitHub 还没算完，或根本没有信号 |

两条大规矩：**托管平台自己能判定的听它的**（绑了 GitHub 且它自己开了分支保护 → `github_enforces=True`，状态原样透传，平台一个字不重算）；**判定不了的平台补位**。缺席不是通过——一个必跑检查还没报到的和还在跑的一样算 `blocked`（#465/#468 的教训：看见的都绿不等于测试跑过）。冲突排在最前：PR 上有冲突时 CI 说什么都不重要。

`ReasonKind` 是 reason 的机器可读分类（`merge_state.py` 的 `ReasonKind`）：

| kind | 下一步在谁手上（`whose_move`） |
|---|---|
| `conflict` | agent（芝士解冲突） |
| `required_check_failed` / `check_failed` | agent（芝士修） |
| `required_check_missing` / `ci_running` | ci（等，超宽限期才转人） |
| `behind_base` | platform（平台 update-branch） |
| `draft` | agent（活还没做完） |
| `github_verdict` | 看是什么词；透传模式下就是平台的原话 |
| `no_obstacle` | human（可以合了） |
| `no_signal` | platform（下一轮自然收敛） |
| `dependency` | 依赖的任务还没落地 |

必跑名单的路径域语义：带路径的条目只对碰了那些路径的改动生效；拿不到 diff（`changed_paths=None`）时**保守处理**——照样算必需，并把回退如实写进 reason。glob 是 GitHub Actions `paths:` 那套的子集（`**` 跨目录、`*` 不跨目录），`fnmatch` 不能用：它的 `*` 会跨 `/`，等于把阀关掉。未绑托管平台的项目走 `local_merge_state`：唯一的信号是「与基线是否冲突」。

## 采纳 = 合并 {#accept-is-merge}

`AcceptService.accept` 依次检查，任何一条不过就整笔事务回滚、什么都不合：

1. 卡的**状态**（`pending` 首次、`conflict` 冲突解完后重试；终态和 `gate_*` 一律「审阅已结束」）。
2. **只有被指定审阅的那个人**能采纳（`decided_by` 来自已验证的调用者身份，绝不取请求体里的值）。
3. **合的是人看到的那个 commit**（`_seen_head`）：请求带 `head_sha`（浏览器渲染卡面时卡上那一版），必须仍等于卡上的 `pr_head_sha`。不一致说明轮询器在「渲染」到「点击」之间把卡刷到了新提交，点下去合的会是一段**没有人看过**的代码（轮询器每 60 秒一跳，这个窗口天天都在）。卡面从没显示过任何版本时（刚递上来、轮询器还没镜像）不是「没有版本可以过时」，而是「还不知道要合什么」：先把当前 head 镜像到卡上，请人重新看一眼。读不到 head 也**绝不放行**——放行的前提是人看过某一版。
4. 话题没归档，调用者不是 AI，机构协议满足。
5. **批准数够**（见下）。

合并调用本身也带这个 sha：GitHub 的 sha 参数会在点击那一刻再拦一次漂移（409 → 刷新卡、请人重看）。放行与自动合布防走的都是同一道闸。

平台自己的默认是：托管平台没开保护时，只有 `clean` / `unstable` 才合，非绿**拒绝采纳**并把状态和原因写进响应；GitHub 自己开了保护的项目直接调 API（405 就是被拦住，平台一个字不重算）。采纳成功即记交付、关闭任务。

## 人工放行 {#merge-anyway}

红着合有时候是对的（CI 抽风、与本次改动无关的既有失败、赶时间的热修）。不能接受的从来不是红着合，而是**没有人做过这个决定**。所以有 `POST /accept-cards/{id}/merge-anyway` 这条出口：**默认拒绝、显式放行**，平台自己永远不走它。

- 谁能点：项目的 `branch_protection.override_handles`；没配置时是项目所有者 + 团队的所有者和管理员（`MemberService.manages`）。
- 芝士被 `_forbid_ai` 挡在外面，和 accept / approve / void 同一条线；路由**故意不进** `app/main.py` 的 `_CHEESE_WRITE_PATHS`（那是给芝士的白名单）。
- 记什么：谁、什么时候、**当时的检查到底是什么状态**（现读一次；读不到就如实写「读不到检查状态」，凭据坏了不该把人锁在门外）、以及人自己写的理由。读到全绿时不说「明知未全绿」：那是往历史里写一条从没发生过的决定（PR #520 真这么记过一条）。
- 放行**放的是规则，不是眼睛**：它和采纳一样要声明「我看的是哪一版」。

## 多人批准 {#approvals}

`approvals_required`（`settings["approvals_required"]`，默认 1）要求这么多个**不同的人**批准之后采纳才真的合并。这一票由 `AcceptApproval` 表记录，唯一约束是 `(card_id, approver_handle)`——同一个人投几次都是一票，`approve` 幂等。**采纳本身就是采纳人的那一票**（所以默认 1 时未配置的项目行为不变）；票不够时抛「还需 N 人批准（x/y）」，整笔回滚。

票是历史：撤销采纳**不清**票。可投票的时机是卡还活着的时候（含 `pending_gate` 与冲突重试）。

## 孤儿卡谁收 {#orphans}

`pending_gate` 这个状态**没有任何出口**：accept / reject / revoke / reassign 四条对它全是拒绝，`create_card` 又因为它拒绝再建新卡——坏掉的不是一张卡，是**整个话题**再也递不出验收卡。三条自动收敛 + 一条人工出口：

| 谁 | 什么时候 | 结果 |
|---|---|---|
| 话题归档（`archive.close_cards_for_archived_topic`） | 一进 archived | 全部非终态卡（`pending` / `pending_gate` / `conflict`）→ `revoked`，幂等 |
| 扫底（`gate_sweep`） | 启动一次 + 周期一次 | 超龄的 `pending_gate` 判死 → `gate_failed` |
| 人工作废（`AcceptService.void`） | 随时 | 卡 → `revoked`（**进终态，不是放行**） |
| 轮询器（`_void_closed_pr_card`） | 看到卡的 PR 关闭且没有合并 | 卡 → `revoked`，note 和房间里写明 PR 已关闭；要继续交付就重新递卡 |

归档时骑着**未合并 PR** 的卡另有一句：平台停止跟进，但**不替任何人去关那个 PR**。替别人关掉一个外部可见的 PR，方向是反的——PR 开着是惰性的，关掉却可能丢掉一段人本来打算手动合并的工作。所以选择是「停止一切自动跟进，把 PR 原样留在托管平台上，并留痕」：note 写清楚，房间里按结论 14 落一条 `accept_stopped`（卡的事落那张卡；为房间递的卡没有卡可落，落项目总览）。

扫底要防误杀，两条防线都必须在：**计时留余量**（`GATE_TIMEOUT_S` 600 秒 + `GATE_STALE_GRACE_S` 600 秒，工作区准备和排队都在这段里），以及**在跑的不碰**（`gate.in_flight_card_ids()` 回答「这张卡的闸门还在本进程里跑吗」；重启后这张表是空的，正好让启动扫底放心判死）。判死时 note 和输出都带前缀 `GATE_ABANDONED_PREFIX`——「闸门没跑完」和「检查没通过」都落在 `gate_failed` 上，状态列区分不了，但对芝士意味着相反的下一步：没跑完是**原样重递**，没通过是**去修代码**。读卡的代码请认这个前缀，不要靠猜输出是不是空的。

## 房间里的那一行 {#events}

卡的每一种结局在房间里都有一行（驳回、作废、改描述、合了、卡住了），唯独等待的**开始**曾经没有——而验收卡本身钉在对话末尾、不随时间线往上滚，翻历史也找不到它是什么时候递上来的。

`_announce_filed` 补上这一条：内容用第三人称（「《报告》已提交，待 XX 审阅」——一屋子人都看得见它，而「待你验收」只对其中一个人成立），改动主题进折叠区，并**通知两个人**：验收人（这件事现在在他手上）和提需求的人（他等的东西有结果了），同一个人只收一条。托管平台上另有一条 `accept_ready`，说的是另一件事——检查全绿、可以当场合并。

驳回（`card_rejected`）不只写一行：它同时把理由**放进那条活的 nudge**，连同「先 `cd "$(cheese worktree <id>)"`，照着理由改，改完重新递卡（驳回不阻塞重递）」一起 —— 理由必须过去，否则芝士只知道被退了、不知道退在哪，只能猜着重做一遍。原任务已关闭时改由新任务承接。

## 边界与坑 {#traps}

- 合并态是**纯函数**，但采纳现场**重算一次**而不是读卡上的镜像：镜像可能已经过了一个轮询间隔。卡上的 `merge_state`（`{"state","who","reasons","head_sha","checked_at","since"}`）只用来显示——任务列表靠它把等采纳的卡分到不同列。
- 轮询器每跳把 verdict 镜像到卡上；`since` 是「这个 (state, head) 组合从什么时候成立」，给「必跑检查迟迟没报到、超过宽限转人」当时钟——宽限期要时钟，所以不在纯函数里。
- `rebase_count` 给平台的自动换基封顶。曾经是数 `note` 里 `⟲` 的个数，而 `note` 每被覆写一次就清空——计数器归零，上限永远够不着，平台无限换基下去。
- `nudge_state` 是 PR 回流的去重账本，**必须活过后端重启**：签名只活在进程里的话，一次重启就把所有在飞的 PR 重新叫一遍，而 CI 一个字都没变。
- `void` 和 `merge-anyway` 都「故意不在写白名单里」，但**「不加白名单」本身拦不住任何东西**：没列进去的写路由压根不过那个中间件，症状是静默放行而不是 401。真正拦住芝士的是路由上的登录校验加服务里的 `_forbid_ai`。
