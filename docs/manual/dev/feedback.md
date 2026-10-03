---
title: 反馈
kind: 参考
summary: 平台级收件箱：提案卡、提交、作者与提交者、可见性并集、状态时间线与后台分诊。
covers:
  - backend/app/domain/feedback/
  - backend/app/api/routes/feedback.py
  - backend/app/api/routes/feedback_proposals.py
  - backend/app/api/routes/admin_feedback.py
  - backend/sandbox/cheese
---

# 反馈 {#feedback}

一个平台级的收件箱：人在这里提 bug 和建议，芝士在这里举手，管理员在这里分诊。

> 讲：两条入口、可见性并集、状态怎么走、栏位为什么不构成分区。不讲：反馈中心的界面怎么点，见使用文档[反馈中心](/feedback#feedback)；未读与投递账本见[通知](/dev/notifications#one-table)；管理看板的口径见[看板](/dev/boards#platform-stats)。

## 两条入口，作者和提交者是两个人 {#authorship}

行落在 `Feedback`（`backend/app/domain/feedback/models.py`）。主键是 uuid，但页面上印的编号是另一列 `display_no`：一个 Postgres 序列，形状是 `FB-1042`。分成两列是因为 uuid 不能排序、也不能给人念，而编号又必须是稳定的、一次分配不再变的。

一条反馈有**两个**都算「是我提的」的 handle：

| 列 | 谁 | 什么时候填 |
| --- | --- | --- |
| `author_handle` / `author_is_agent` | 真正发现问题的那个（直接提交时是本人，提案卡路径上是那个 agent） | 总是 |
| `submitted_by_handle` | 按下发送的人 | 只有提案卡路径 |

两列而不是一列，是为了让「芝士提的反馈里有多少真的被人发出去了」答得出来。`author_user_id` / `submitted_by_user_id` 是给 join 和计数用的副本，handle-only 族凭据（cheesex 会话令牌没有整数 id）下为 NULL。

`session_id`、`environment`、`logs`、`what_happened`、`repro`、`evidence` 都是**快照**，不是外键：反馈说的是平台，它比提它的房间活得久，所以 `topic_id` / `project_id` 是 `ON DELETE SET NULL`。删除是软删（`deleted_at`，连带评论）。

两条入口：

- `POST /feedback`（`api/routes/feedback.py`）—— 人直接提交。请求体里**没有** `topic_id` / `project_id`，人也填不出来。
- `POST /topics/{topic_id}/feedback-proposals/{block_id}/accept`（`api/routes/feedback_proposals.py`）—— 人在提案卡上按「提交反馈」。

## 提案：芝士举手，人决定 {#proposals}

`cheese_feedback_propose`（`backend/sandbox/cheese`）打的落点是 `POST /topics/{topic_id}/feedback-proposals`，落的**不是**一条反馈，而是话题里的一条消息块（`Block`），payload 塞在 `Block.meta.feedback_proposal` 里 —— 那就是人看到的那张卡。`proposal_meta` 把正文连同 `fingerprint` 一起写进卡里，指纹跟着卡走，`dismiss` 端点于是不必从可能已被前端重排过的文本里重算它。

**agent 不能自己发布**（`FeedbackService.create`）：命中 `IdentityService.is_agent` 就直接 `ForbiddenError`，报错文案点名 `cheese_feedback_propose`，给模型一个下一步而不是死胡同。同一个判断收掉了另一扇门 —— agent 去发送一张已有的卡，仍是 agent 在发布自己，只是分两步走。作者从卡上取（`Block.author`），提交者取验证过的调用者，客户端两样都说了不算。

卡本身不落表：它是消息，历史就是历史。落表的只有「不用」——`FeedbackProposalDismissal`（`topic_id` + `fingerprint`）。原型里「不用」只把组件状态置成 dismissed，刷新就回来；服务端不落一行的话，同一个问题每轮都会再问一遍，而它会让人对整张卡产生免疫。「这张卡已经发过了」也不落表，它是卡自己的状态，写在卡自己的 `meta` 上（`ACCEPTED_FEEDBACK_KEY`）跟着卡走。

## 三道限流 {#limits}

`ProposalService.check` 里三道彼此独立的限流，各挡一种不同的刷屏。**三道全回 412**：状态码不同只会教调用方「等一会再试」，而这三件事的正确反应都是**别做这件事**。判上一次比判配额更早回答，否则一个被拒过的问题会在配额用完之后伪装成临时拒绝，明天再提一遍。

| 顺序 | 限流 | 判据 | 记忆存在哪 |
| --- | --- | --- | --- |
| 1 | 拒绝要有记忆 | 指纹在 `FeedbackProposalDismissal` 里 | 表 |
| 2 | 指纹去重 | 24 小时内同一个指纹提过 | 话题里的消息块 |
| 3 | 每日配额 | 24 小时内这个话题的卡达到 `settings.feedback_proposals_per_topic_per_day`（默认 2） | 同上 |

指纹是 `what_happened` + `repro` **归一化**（折空白、去标点、折大小写）后的 sha256 前 32 位十六进制。这两段为空时才退回去哈希 `title` + `problem`：两个都空的提案会共用空串的哈希，于是同一个话题里的第二张会被「这个问题刚提过」拒掉。指纹**挡不住换一种说法的重提**，那需要嵌入模型；日报配额是它漏掉的那些的兜底 —— 这层限制就这么强，写在这里免得有人把它读得更强。

「一天」是滚动的 24 小时，不是自然日：自然日午夜清零，紧挨午夜的两分钟里可以提两条一样的。

三道全是「先读后写」，所以 `check` 第一件事是拿话题级事务锁（`pg_advisory_xact_lock`，键 `feedback-proposal:{topic_id}`），持到路由 commit。没有用 `(topic_id, fingerprint)` 唯一索引：指纹不是永久身份，24 小时窗口过了同一个问题可以正当地再提，唯一约束会把那一次变成 500。

## 谁能看见 {#visibility}

一个判据，`FeedbackService.may_see`，三档并集：

| 档 | 条件 |
| --- | --- |
| 公开 | `visibility == public` **且** `security == False` |
| 管理员 | `is_admin`：反馈管理员名单 `FEEDBACK_TRIAGE_HANDLES`（`FeedbackService.is_admin`），不是平台管理员，见[管理员](/dev/admins#feedback-admins) |
| 我提的 | `handle` ∈ (`author_handle`, `submitted_by_handle`) |
| 提出它的那个房间 | 提出那一刻在那个房间的名册里，**且**今天还读得到那个房间 |

看不见的 id 回 **404 而不是 403**（`visible_row`）：403 等于确认了这条私密反馈存在，而那正是提它的人要求别声张的东西。

房间那一档有两个条件，缺一不可。名册按**反馈提出的那一刻**取（`created_at <= Feedback.created_at`），不是按现在：按现在取的话，把谁加进房间就等于把这个房间历史上每一条私密反馈一并交给他。另一句是「今天还读得到吗」（`may_read_topic`），少了它这扇门撤不回来 —— 退项目只删 `ProjectMember` 一行，被移出的人在各房间的名册行原地不动。授权键只有一个，就是 `topic_id`，项目也从它解出来，不读 `row.project_id`。

`security` 是 `private` 的**细分**，不是第二个轴：它由管理员在分诊时置上，只在读时收窄公开那一臂，**不收窄房间那一臂**（那个房间的人本来就看过）。管理员**没有**把私密改成公开的入口 —— 那是唯一一个本人无法撤销的改动。

「我的反馈」走 `visible_to`（一个**超集** WHERE），再由 `list_mine` 逐行过 `may_see`：`may_read_topic` 是四张表上的四个断言，拼进 SQL 就是第二份会漂的规则。

## 状态与时间线 {#status}

`STATUS_LADDER` 是 已收录 → 处理中 → 已修复 → 已上线（`received` / `in_progress` / `resolved` / `deployed`）。后两级从任何状态都能到，也允许退回（resolved → in_progress）：反馈确实会被重新打开，而一个禁止退回的状态集会被人用「再提一条重复的」绕开，代价是丢掉那段让反馈有用的历史。

梯子之外还有第五个状态 `declined`（不修复）：看过了，不改，原因写在评论里（有意的设计、不在范围内、平台侧复现不出来）。它是另一种结局，不是「已上线」之后的一级，所以不在 `STATUS_LADDER` 里：提交者那根时间线画到它就停，不再画「已修复 / 已上线」。它在 `CLOSED_STATUSES` 里，和修复、上线一样不再接受支持、bug 会沉底、计进「已完成」。管理端的状态选项和前后顺序用 meta 的 `statuses`（梯子四级加 `declined`），所以它排在最后，设成它之后和「已上线」一样不能在界面上退回。状态列是 VARCHAR（`_enum`），加这个值不需要迁移。

`set_status` 是**唯一**写 `status` 的方法，它和 `append_timeline` 共用调用方的事务 —— 没有一条路由能写状态却不写历史。同状态重复提交是空操作，不是第二条历史：相隔一秒的两条一模一样的记录读起来像历史出了 bug。`patch_admin` 只收 `priority` / `assignee_handle` / `security`，**不收** `visibility`、也不收 `status`。

中间两级平台自己记，因为几乎没人手动去按：指派给某人、或有人自己[领取](#claim)的那一刻记「处理中」（指派走 `patch_admin`，`by_handle` 是指派的管理员，`note` 写指派给了谁；领取的 `by_handle` 是领的人，`note` 是「已领取」），修它的 PR 合并的那一刻记「已修复」；没人指派过的，同时按那个 PR 打开的时间补记「处理中」（见[后面那一节](#shipped)）。几处都走 `FeedbackService.advance`，它只往前走：已经到了或过了那一级的不动，`declined` 也不动，所以自动的一步不会推翻人做过的决定。手动设置照旧可用。

`security` 变化会补一条时间线（状态不变）：把一条标成安全问题是一次路由决定，提它的人应当看得见，而那也是它停止对同事可见的一刻。

评论是**两层**：`parent_id` 永远指向顶层评论，回复的回复被重新挂到祖父上（和原型 `stores/feedback.ts::addComment` 一样）。`reply_to_handle` 是为了补上折叠丢掉的那一点 —— 折叠之后浏览器分不清这条在回楼主还是在回楼里的另一条，所以服务端在写入时，从它加载的那一行记下被点「回复」的手柄，客户端不许自己编一个名字。只有被回复的那条本身也是回复时才写。

支持是 `FeedbackSupport`，唯一约束 `(feedback_id, author_handle)` 让重复点是空操作；已办完的（`CLOSED_STATUSES` = resolved + deployed + declined）不再接受支持，回 412 而不是 403（客户端该做的是重新读一遍这条，不是别问了）。

## 领取：一条反馈只有一个人在修 {#claim}

领取就是把 `assignee_handle` 写成自己，和管理员的指派是同一列：两列会各说一个「谁在管它」，而领取本来就是「指派给我自己」。所以领取不需要迁移，「我的反馈」里「指派给我的」那一臂也自动包括领到的。

| 入口 | 作用 |
| --- | --- |
| `POST /feedback/{ref}/claim` | 领取。`ref` 是 `FB-12` 或 uuid；回整条详情 |
| `DELETE /feedback/{ref}/claim` | 放弃。持有人自己，或反馈管理员 |

领取存在只为一件事：**别让两个人修同一个问题**。所以第二个人的领取必须失败，回 409（`feedbackClaimedByOther`），原话里点名持有人，`error.data.holder` 也带着他——他下一步是去找那个人，不是换个说法再领一次。判「有没有人领着」之前先 `SELECT … FOR UPDATE` 锁住这一行（`claims.lock`）：两个同时到的领取，后到的那个在锁上等先到的提交，再读到它写下的名字。不锁的话两个都读到「没人」，后写的悄悄盖掉先到的，两个人都以为是自己在修。持有人自己再领一次是空操作。

谁能领（`FeedbackService.may_claim`）：看得见这条（看不见的照旧 404），并且是反馈管理员，或者在做这个平台本身——判据和开发文档同一个，`settings.docs_dev_repositories` 里那个仓库所在的项目（`docs_site.library.platform_projects`）。人看他能不能进其中一个项目；agent 带上 `?topic=<房间>`（见[下一节](#agent-reads)），看房间的项目。agent 用的是 `cheese_feedback_claim` / `cheese_feedback_release` 两样工具（`backend/sandbox/cheese`），领不到时把原因原样转给它，并告诉它不要修。

领取走 `advance` 记「处理中」，所以已经修好、上线、不修复的，领了也不动它的状态。放弃只清掉持有人，不退回状态，也不写时间线：「处理中」记的是有人动过它，放弃之后这件事也还是发生过。详情上的 `can_claim` / `can_release` 由服务端用同一处判据算好，详情页右栏「处理人」那一格照它画「领取」「放弃」两个按钮。

## agent 读反馈中心 {#agent-reads}

agent 的凭据只认一个房间：不说房间的 `/feedback/*` 请求会被拒（「This credential is restricted to one room」）。所以它要读的几条路由都收 `?topic=<房间>`，和领取一样在那个房间里认人（`_in_room`：`authorize_topic`，`enforce=True`，不在那个房间里就 403）：

| 入口 | 作用 | agent 的工具 |
| --- | --- | --- |
| `GET /feedback` | 公开列表，栏位和 `q` / `status` / `kind` / `author` / `since` 筛选和页面同一份 | `cheese_feedback_list` |
| `GET /feedback/{ref}` | 一条的详情：正文、时间线、第一页评论、`can_claim`。`ref` 是 `FB-12` 或 uuid | `cheese_feedback_get` |
| `GET /feedback/{id}/comments` | 往下翻评论 | `cheese_feedback_get` 在评论多于一页时接着取 |

带了房间，就只认做平台本身的项目里的房间（`FeedbackService.require_platform_room`，和领取同一个判据 `claims.is_platform_project`）；别的房间回 403（`feedbackReadPlatformRoomsOnly`）。反馈中心装的是平台本身的活，在别的项目里的 agent 一条也领不了，读到的只会是它不该动手的东西，所以直接告诉它为什么读不到。

读到哪些，用的还是 `may_see`，handle 是 agent 自己的。agent 永远拿不到管理员那一臂（`_is_admin` 对带 agent 绑定的 handle 降级），所以它看见的就是项目里一个**不是反馈管理员的成员**看见的：公开的；它自己提过的（提案卡路径上作者是它）；提出时它在那个房间的名册里、今天还读得到那个房间的私密反馈。别人提的私密反馈、管理员标成安全问题的，它和那个成员一样是 404。列表只有公开的那一臂，私密的那几条只能拿编号读。

写操作除了领取和放弃都不收房间：agent 不在反馈中心里评论、支持或删除。

## 修复合并、上线时自动改成「已修复」「已上线」 {#shipped}

修一条反馈的提交，在提交信息里单独写一行：

```text
Fixes-feedback: FB-12, FB-15
```

这一行要顶格写，和 git 的 trailer 一样；缩进的行算引用，不生效，所以在提交信息里举例说明这个写法时要缩进。`FB-` 前缀不能省：在 GitHub 上 `#12` 是第 12 号 issue 或 PR，`Fixes #12` 还会把那个 issue 关掉。行首的键大小写都认，同一行可以列多条。**写在提交信息里，不写在 PR 描述里**：main 是 squash 合并，squash 提交的正文是这个 PR 里各个提交的信息拼起来的，PR 描述进不了 main 的历史。

测试环境的部署（`deploy-dev.yml`）在部署前记下正在跑的版本，最后一步（前面每一步都成功之后）用 GitHub 的 compare 取出「被替换的版本 → 这次的版本」之间的提交，交给正在跑的后端容器里的 `scripts/ship_feedback.py`。它按上面那行找到每条反馈（`shipping.py`），先通过 `advance` 记「已修复」，时间是那个 PR 的合并时间，再通过 `set_status` 推到 `deployed`——和管理员按按钮是同一条路，所以时间线照写、提交者的未读数照涨。合并时间取 PR 的 `merged_at`（部署那一步对每个提交查一次 `commits/{sha}/pulls`，所以工作流有 `pull-requests: read`）：squash 提交自己的时间是它进合并队列的时间，比真正合进 main 早十几分钟；没有 PR 的直推提交用它的提交时间。已经是「已修复」的（有人手动设过）不再记第二条。还停在「已收录」的（没人指派过），同一次查询拿到的 PR `created_at` 记成「处理中」，备注「PR #N 已打开」——PR 打开就是有人开始动它的时刻；直推提交没有这个时间，就不补。提交者收到的这条通知是有意的：修复上线就告诉提的人，和管理员手动改状态时一样。这一步没有推它的人（`by_handle` 为 NULL），时间线上这几步的 `note` 分别写「PR #N 已打开」「已由 PR #N 修复」「已由 PR #N 修复并上线」加 PR 链接，PR 号取自 squash 标题末尾的 `(#N)`，没有就链到提交。

- 只读这次新增的提交：一条后来被重新打开的反馈，不会被一次不相干的部署改回去。下一个写了它编号的修复上线时，它会再被推一次。
- 已经是 `deployed` 的不动（不写第二条时间线）；编号不存在或已删除的跳过，照样打一行日志。
- 这一步没有新增端点，也没有新增凭据：部署本来就在那台机器上用 `docker exec` / `docker compose run` 跑后端脚本，这里是同一种做法。
- 只有测试环境（`okcheese.com`）这样做。生产环境是另一份数据库，同一个编号在那里指的是另一条反馈。

## 栏位是过滤器，不是分区 {#tabs}

公开侧四个栏位 `all` / `hot` / `active` / `resolved`，定义只写在 `_tab_where` 一处，列表和计数共用：

- `resolved` 装的是 `CLOSED_STATUSES`，也就是修复、上线和不修复 —— 对提它的人来说那都是一个答复。
- 办完的条目会从工作栏下沉，但**只有办完的 bug**：一条办完的建议是本该做、也做了的东西，仍然值得读，把它藏起来是更宽的那种读法。
- 推论：栏位是过滤器不是分区，一条办完的建议同时出现在 `all` 和 `resolved` 里，四个数加起来不等于总数。这是决定，不是记账错误。

`hot` 既是筛选也是排序，两者都来自 `hot_score()` = 支持数 × 0.5^(天数/14)，外加 `HOT_MIN_ITEMS`（5）条兜底，保证新板上第一条 0 票的反馈也出现。排序和判据共用一个表达式：写两份的话，数字和列表会对不上，而两边各自看着都对。

## 后台分诊 {#admin}

`/admin/feedback` 四个栏位：公开 / 私密 / agent 提的 / 安全（`ADMIN_TABS`）。`tab` 或 `sort` 不认识时回 400 而不是悄悄退回默认 —— 猜错栏位会让人以为「这条反馈不见了」，而它其实在隔壁。

| 入口 | 作用 |
| --- | --- |
| `GET /admin/feedback` | 四栏列表，外加 `assignee` / `since` / `resolved_since` / `deployed_since` 三个可选下界 |
| `PATCH /admin/feedback/{id}` | 优先级 / 指派人 / 是否安全问题 |
| `POST /admin/feedback/{id}/status` | 推状态（走 `set_status`，带时间线） |
| `POST /admin/feedback/{id}/notes` | 管理员之间的内部备注，只增不改 |

`since` 按提交时间收，`resolved_since` / `deployed_since` 按**时间线**收（`EXISTS` 子查询问「窗口内到过这个状态」）：一条后来又被退回的条目照样算「解决过」，那正是分诊要看见的那一批。备注是行不是列：一个字符串列在两个管理员之间会互相覆盖，而「上一版写了什么」恰恰是分诊时最需要知道的。

对外的 `counts` 被 `PUBLIC_ONLY` 收窄（匿名读者不该从一个数字里得知私密反馈有多少）；管理看板的 `admin_board_counts` 是**全量**口径，含私密和安全。两个口径不能互相替代 —— 拿 `counts` 去填 KPI 会让卡片说公开那一臂、旁边的曲线说全量。`unassigned` 只对管理员给，它数的是所有没人认领的未解决条目，私密和安全也算在内。

未读数是**游标**不是标记（`FeedbackReadState`）：标记是每人每条一行，游标是每人一行。`counts` 会自己再问一次 `is_admin` 而不是信调用方传进来的那一位，否则管理员会在这条收窄里反过来丢掉自己的未读数。

## 边界与坑 {#traps}

- **两处判据会各自漂开。** `may_delete_comment` / `may_delete_feedback` 既在删除前问、又用来填 `can_delete`，只有一处的写法（两处各写一遍就是「按钮画得出来、点下去 403」）。客户端**从不**自己重推这条规则，按钮照 `can_delete` 画。
- **作者配额只算直接提交。** 每人每 24 小时 `settings.feedback_reports_per_author_per_day`（默认 30）条，且**删除的不减配额** —— 上限管的是「你在产出多少」，删掉一条不是买回额度的方法。提案卡路径不走这条：它已经被话题配额、指纹和「不用」的记忆挡过一遍，而它的作者是那个 agent，不是按发送的人。两处同样是先锁后数。
- **`Block.meta` 是普通 JSON 列。** `mark_accepted` 整块写一个新 dict，就地改会被 SQLAlchemy 静默丢掉；症状是「同一个请求里生效、下一个请求就失效」。
- **接受用一把按卡命名的锁**（`feedback-proposal-accept:{block_id}`），和话题锁是两个命名空间。读 `meta` 必须在拿锁**之后**重新 SELECT，READ COMMITTED 下这条 SELECT 才看得见赢家提交的 `accepted_feedback_id`；反过来就是两条逐字相同、而人只按了一次发送的反馈。
- **`hot` 计数和 `hot` 列表曾是两条规则。** 有人把「办完了」写成一个字面量 `!= resolved`，`deployed` 加进来时那份拷贝悄悄慢了一个状态。`CLOSED_STATUSES` 就是为这个存在的。
- **提案卡是快照，正文不是。** 发送时正文取请求体（抽屉是预填的，人可以改完再发），房子（`topic_id` / `project_id`）取 URL 和卡 —— 客户端能说错的东西一律不采信。
