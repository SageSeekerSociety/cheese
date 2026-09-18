# i18n 词表

翻译界面词条时，核心术语用哪个英文——**以这份为准**。词表不是译文，是尺子：
同一个中文词在两个界面译成两个英文词，是这个产品最容易出、也最难被测试发现的一类错。

结论只写英文，不写理由；理由和「不采用什么」写在第三列，因为**防止下一个人重新纠结或反向改回去**，
比给出结论本身更重要。

英文的依据是**代码里已经存在的标识符**——后端路由、前端目录名、API 客户端文件名。这些是产品实际在用的英文，
不是谁现翻的。现有 `en/` 目录里的值**不作为依据**：它是 PR #929 照中文逐句硬译的产物，
键名本身就是英文句子碎片（`account.and = "and"`、`account.sentenceEnd2 = ""`），照抄它只会把毛病复制下去。

---

## 1. 平台自身的概念

这一节最容易译岔：它们是这个产品独有的东西，没有现成英文可抄。

| 中文 | 英文 | 依据 / 不采用什么 |
|---|---|---|
| 话题 | **topic** | 后端 `/topics/{topic_id}`、`views/spaces/detail/ManageTopics.vue`、`addTopic`。**不用 thread**：界面里没有「楼」的概念，用 thread 会让人以为能把一条讨论拆成多段。**不用 subject**：那是「学科/主题」的意思，会和赛题的「选题」撞车 |
| 讨论 | **discussion** | `views/spaces/detail/Discussions.vue`、`views/spaces/detail/CreateDiscussion.vue`、`network/api/discussions/`。**不用 topic**：平台里 topic 已是另一个概念，两者并存（`spaces.detail.manageTopics` 与讨论是同层的两个东西）。**不用 post**：一条 discussion 是讨论串，不是单条发言 |
| 空间 | **space** | `views/spaces/`、`components/spaces/SpaceSidebar.vue`、`navigation.spaces = "Spaces"`。**不用 area / zone / room**：room 在本平台另有含义（房间），会与话题混 |
| 项目 | **project** | `views/projects/`、后端 `/projects/{project_id}`。**不用 program**：那是「项目集」 |
| 团队 | **team** | `views/teams/`、`navigation.teams = "Teams"`。**不用 group**：group 留给「域名组」 |
| 赛题 | **challenge** | 既有英文已经这么用：中文「部分赛题可能仅对特定域名邮箱开放」对应的 `account.someChallengesAreOnlyAvailableToParticular` 写的是 `Some challenges are…`。出现 25 次，是这套 catalog 里最大的一个术语。**不用 problem / contest**：problem 是算法题，contest 是比赛本身而非题目。见 §5 第 3 条，这里有个命名陷阱 |
| 任务 | **task** | 指平台里的工作单元时（`publicSite.taskProgress = 任务进展`、`/topics/{topic_id}/tasks/{task_id}`）。**不用 job**：job 在技术语境里是后台作业。中文「任务」在 catalog 里还有第二种用法，指的其实是**赛题 = challenge**，见 §5 第 3 条 |
| 分类 | **category** | `views/spaces/detail/ManageCategories.vue`、`spaces.detail.manageCategories.*`。**不用 tag**：tag 是另一套东西（`network/api/tags/`） |
| 域名组 | **domain group** | `views/spaces/detail/ManageDomainGroups.vue`、`spaces.domainGroups.*`。域 = **domain**，不用 field / realm |
| 模板 | **template** | `views/spaces/detail/ManageTemplates.vue`、`views/spaces/detail/SelectTemplate.vue`、`views/spaces/detail/TemplateForm.vue`。**不用 boilerplate / preset** |
| 公告 | **announcement** | `views/spaces/detail/Announcements.vue`。**不用 notice**：notice 更像「系统提示」，且和通知混淆 |
| 邀请 | **invitation**（名词）/ **invite**（动词） | 后端 `/invitations/{invitation_id}`、`en/account.json` 既有 `invitationCode = "Invitation code"`。邀请码固定为 **invitation code** |
| 回复 | **reply** | 代码里 `reply` 出现 148 次（`reply_to`、`replyTarget`、`repliesCount`）。**和「回答」不是一个词**，见下条 |
| 回答 | **answer** | `views/question/`、`network/api/answers/`。**不用 response / reply**：reply 是「回复某条发言」 |
| 提问 | **question** | `network/api/questions/` |
| 通知 | **notification** | `network/api/notifications/`、`notifications.common.notificationCenter = 通知中心`。**不用 alert**：alert 是弹窗 |
| 附件 | **attachment** | `network/api/attachments/` |
| 标签 | **tag** | `network/api/tags/` |
| 工作台 | **workspace** | 既有 `publicSite.openWorkspace = "Open workspace"`、`navigation.workspace = "Workspace"`。**不用 dashboard / console**：dashboard 在本产品另有位置（空间的数据看板）。中文自己也不统一——`navigation.workspace` 写「工作区」、`publicSite.openWorkspace` 写「进入工作台」，英文一律 workspace |

**「活」不是界面词。** 房间对话里说的「这条活」是**平台内部的黑话**，对应的界面概念就是「任务」= **task**。
`zh-CN` 目录里出现的「活」只有「活跃」（= active）这一处。译文里不要出现 "work" 或 "job" 来表达「活」。

## 2. 动作与状态

| 中文 | 英文 | 不采用什么 |
|---|---|---|
| 发布 | **publish** | 不用 release / post |
| 创建 | **create** | 不用 add：add 用于往已有集合里添一项（添加话题 = add topic） |
| 删除 | **delete** | 不用 remove：remove 用于从集合里移出，不销毁 |
| 编辑 | **edit** |  |
| 归档 | **archive** / 取消归档 **unarchive** | 不用 disable：那是停用，不是归档 |
| 提交 | **submit** | 不用 commit：commit 在技术上已被 git 占用 |
| 交付 | **deliver**（动词）/ **delivery**（名词/定语） | 依据：落地页第 4 步标签为 `Deliver`，`deliveryNotes = "Delivery notes"` |
| 成果 | **result** | 依据：落地页示例按钮为 `Show sample result`。动词和定语用 deliver 一族：**成果交付 = Deliver**（阶段名）、**成果说明 = Delivery notes**。别把「成果」译成 outcome / achievement |
| 采纳 | **accept** | 与验收卡（acceptance card）的 accept 同源，保持一致 |
| 取消 | **cancel** |  |
| 确认 | **confirm** |  |
| 置顶 | **pin** |  |
| 截止日期 | **deadline** | 截止日期提醒 = **deadline reminder** |
| 报名 | **registration** | 既有 `tasks.form.registrationStartAt = 报名开始日期`、`tasks.form.deadline = 报名截止日期`。**不用 sign-up**：sign-up 留给账号注册（`account.*` 里那一堆 registration 说的才是注册，别混）。`已报名` = **Registered** |
| 参与 | **participation**（名词）/ **join**（动词） | `participantType = 参与者类型` → **Participant type**、`participantLimit = 参与人数上限`。加入某个赛题用 **join** |
| 审核 | **review**（流程）/ **audit**（模块名） | 界面词一律用 review：`待审核 = Pending review`。**不用 audit**：audit 只出现在键名和 `views/spaces/detail/AuditTask.vue` 这类代码标识里，那是日志审计的语感，不该给用户看 |
| 通过 | **Approve** | `auditTasks.approve`。与「采纳 = accept」区分：approve 是审核放行，accept 是采纳回答 |
| 驳回 | **Reject** | `auditTasks.reject`、`驳回理由 = Rejection reason`。**不用 decline**：decline 用于拒绝邀请 |

## 2.1 通用词

| 中文 | 英文 | 不采用什么 |
|---|---|---|
| 成员 | **member** | `/projects/{project_id}/members` |
| 用户 | **user** | `/users/{handle}/profile` |
| 所有者 | **owner** | `cx_types.ts` 里是 `owner_handle`、`Project.owner_handle`、角色枚举 `'owner' \ | 'admin' \ | 'member'`。不用 proprietor |
| 权限 | **permission** | `tasks.form.accessControl.title = 权限设置` → **Permissions**。不用 privilege |
| 搜索 | **search** | 不用 find |
| 图片 | **image** | `editor.image.tooltip = 插入图片` → **Insert image** |
| 保存 | **save** | 不用 store / persist |
| 返回 | **back** | 按钮上是 **Back**；`返回讨论列表 = Back to discussions` |
| 加载 | **load** | `加载更多 = Load more`、`加载失败 = Couldn't load` |
| 提示 | **hint** / **tip** | 表单下方的说明用 hint，悬浮提示用 tip |
| 成功 | **success** | 名词是 success，形容词/副词看组合：`发布成功 = Published` 而不是 `Publish success`，见 §3 |
| 失败 | **failure** / **failed** | 名词是 failure，状态是 failed：`加载失败 = Couldn't load`。允许用缩写 `Couldn't`，比 `Failed to load` 短 |

## 2.2 工作台（房间右侧面板）与看板

这一节按**代码标识符**分组，因为屏幕上的词都挂在它们身上：面板的五个页签是 `TabKey`
（`components/WorkPanel.vue`），板上的列是 `BoardColumn`（`cx_types.ts`），话题头的状态是
`TopicPhase` / `CardPhase`（`lib/topicState.ts`），路由标题是 `router/workspaceRoutes.ts` 里的
`meta.titleKey`。

**`workspace.status.*` 是一张表，不是四张。** 施工中 / 交付中 / 已完成 同时出现在看板列名
（`lib/board.ts` 的 `columnLabel()`）、话题状态徽章（`topicStateBadge()` / `topicPhaseBadge()`）和
房间总览的计数条（`RunningWorkView.vue`）上；各写一份的话，改一边就会在两个屏幕上看到两种叫法。

**面板页签（`TabKey`）**

| 中文 | 英文 | 依据 / 不采用什么 |
|---|---|---|
| 对话 | **Chat** | `TabKey = 'chat'`，这一个页签就是聊天本身。不用 Conversation：那是「一段会话」的名词，页签上是动作性的入口 |
| 总览 | **Overview** | `TabKey = 'overview'`、`views/OverviewView.vue`。路由标题同词（`workspace.routes.overview`） |
| 现场 | **Activity** | `TabKey = 'site'`。**键名不采用**：site 脱离产品语境会被读成「网站」。**Scene 不采用**：那是「一场戏」的场面。**Transcript 不采用**：那是「一份可回看的文字稿」，而这个页签底下是**芝士正在做的事的实时流水**，是正在发生的事，不是事后的记录。选 Activity 是因为它描述「正在发生」，与 §1「活跃 = active」同源 |
| 改动 | **Changes** | 键名就是 `changes`。不用 Diff：那是格式不是内容（页签里既有文件列表也有新改动提示）。不用 Files：那只是列表里的东西，说不了「有新改动」那层意思 |
| 预览 | **Preview** | `TabKey = 'preview'`、API `getPreview`。不用 View / Output |

**看板列（`BoardColumn`）**

| 中文 | 英文 | 依据 / 不采用什么 |
|---|---|---|
| 施工中 | **Building** | `BoardColumn = 'building'`，与话题状态共用 `workspace.status.building`。不用 Working / In progress：那一列说的是芝士在动手，Building 是标识符里的原词 |
| 交付中 | **Delivering** | `BoardColumn = 'delivering'`，§2「交付」= deliver 一族 |
| 待处理 | **Needs you** | `BoardColumn = 'needs_you'`。这一列是**整块板上唯一要人动手的**，直译成 Pending / To do 会被读成「还没轮到」，而它要说的正是「现在轮到你了」。第二人称、句末不加标点 |
| 已完成 | **Done** | `BoardColumn = 'done'`，与话题状态共用 `workspace.status.done`。不用 Completed：更长，且界面里没有先例 |
| 已归档 | **Archived** | `BoardColumn = 'archived'`，§2「归档」= archive。板上现在没有这一列（活不归档，只有房间会），词先留着，等真出现时不必再定一次。**注意这个是「已归档」，和话题状态里那个 `archived` 不是一个东西——见下面的命名陷阱** |

**话题状态（`TopicPhase` / `CardPhase`）**

| 中文 | 英文 | 依据 / 不采用什么 |
|---|---|---|
| 已采纳 | **Accepted** | `TopicPhase = 'archived'`（房间那一档）。§2「采纳 = accept」，与验收卡的 accept 同源。**不采用 Archived**，见陷阱 |
| 已完成 | **Done** | `TopicPhase = 'closed'`（支线收工），与看板列名共用 `workspace.status.done` |
| 草稿 | **Draft** | `TopicPhase = 'draft'`，与 git 的 draft PR 同词 |
| 施工中 | **Building** | `TopicPhase = 'working'`。这一档是「芝士此刻在跑」压过纸面状态（`topicPhase()` 里 `working` 先于采纳卡），所以用同表的 building，不用 In progress——「进行中」留给 `open` |
| 交付中 | **Delivering** | `TopicPhase = 'delivering'`（也是 `CardPhase = 'delivering'`：采纳卡正在交付） |
| 待验收 | **In review** | `TopicPhase = 'reviewing'`（采纳卡非空的那两档），§2「审核」= review。不用 Pending review：这个徽章要说的是「东西在你这边」，不是「排在一个队列里」 |
| 进行中 | **In progress** | `TopicPhase = 'open'`，依据既有 `publicSite.inProgress = "In progress"`。不用 Ongoing：界面里没有先例 |

**路由标题（`meta.titleKey`，十条已全部拍定）**

| 中文 | 英文 | 依据 / 不采用什么 |
|---|---|---|
| 项目工作台 | **Project workspace** | `workspace.routes.project`，§1「工作台」= workspace、「项目」= project。不用 Workbench：那是木工台 |
| 总览 | **Overview** | `workspace.routes.overview`，与面板页签同词 |
| 看板 | **Board** | `workspace.routes.board`、`lib/board.ts`。不用 Kanban：那是具体某种看板方法的名字，这里只是「一块板」 |
| 私聊 | **Chat** | `workspace.routes.dm`。**owner 拍板用 Chat**。不用 DM：那是标识符，界面里没人这么说；不用 Direct messages：太长，侧栏那一行放不下 |
| 项目文档 | **Project docs** | `workspace.routes.docs`。不用 Documents / Files：docs 是代码里的原词，且这一页装的是项目自己的文档，不是任意文件 |
| 日历 | **Calendar** | `workspace.routes.calendar`，与 `/cal` 面板里那个 `workspace.chat.viewCalendar` 同词 |
| AI 队友 | **Agents** | `workspace.routes.agents`。**owner 拍板用 Agents**。不用 AI teammate：一个项目里可以有多个队友，侧栏那一行是**一列队友的入口**，复数才对；另见下面的收口提醒 |
| 项目设置 | **Project settings** | **复用 `projects.settings.title`**，路由不另建键——同一件事在设置弹窗和路由标题里必须是同一个词 |
| 导出与发布 | **Export & publish** | `workspace.routes.delivery`。不用 Deliver：那是 §2「交付」= deliver（芝士把活交给你），这一页是**把项目交出去 / 发出去**；用 Deliver 也会和看板列 Delivering 撞词 |
| 成员 | **Members** | `workspace.routes.members`，与 `members` 命名空间里的既有词一致 |

两条落地规则：

- **陷阱：`archived` 在代码里指两件不同的事。** 看板的 `BoardColumn = 'archived'` 是**已归档 / Archived**；
  话题状态的 `TopicPhase = 'archived'` 是**已采纳 / Accepted**（批准过、收工了的房间，`topicStateBadge()`
  里 status 为 `'archived'` 落的就是这一档）。同一个字面量、两个意思，所以是**两个键**
  （`workspace.status.archived` 与 `workspace.status.accepted`）。照代码里的 `archived` 取词，会把一间
  已经采纳的房间头写成「已归档」。
- **两个占位符的串写不了多形态，只能用 `(s)` 兜。** `总览（{count} 件任务，{open} 件进行中）` 按 §3
  只能写一条，于是用词表允许的写法：`Overview ({count} task(s), {open} in progress)`。
  单个 `{count}` 的（`总览（{count} 件任务）`、`改动（{count} 个文件）` 等）照 §3 的形态表写足 3 条。

这十条都走 `meta.titleKey`：`usePageTitle` 按它取词，侧栏的每一行和顶栏标题读同一份，
切语言时两处一起变。`workspaceRoutes.ts` 里不再有中文标题。

**收口提醒：`publicSite.aiTeammate` 目前是 "AI teammate"，与上面拍板的 Agents 不一致。**
它属于公开站点那一层，本系列未动；等「AI 队友」在公开站也要用 Agents 时，单独一笔改。

## 3. 形态约定

- **大小写：句子式（sentence case）。** 只大写第一个词和专有名词：`Create account`、`New project`、
  `Sign in`、`Delivery notes`。**不用 Title Case**（`Create Account` 是错的）。PR #929 在这点上不一致，以本节为准。
- **单复数**：导航项和列表标题用**复数**（`Spaces`、`Teams`），其余界面文本用**单数**（`Space name`、`New project`）。
- **带 `{count}` 的字符串写 3 个形态，不要写 2 个。** 这个仓库没有配 `pluralizationRules`，形态是按
  **形态条数**选的，不是按语言选的——中英走的是同一条规则，实测（vue-i18n 9.14.5，`legacy: false`）：

  | 形态条数 | n=0 | n=1 | n≥2 |
  |---|---|---|---|
  | 3 条 | 第 1 条 | 第 2 条 | 第 3 条 |
  | 2 条 | **第 2 条** | **第 1 条** | 第 2 条 |

  **2 条是反的**：第 1 条管「正好 1」，第 2 条管「其余」。所以照抄中文的 2 条会出错——
  `{count} 条回复 = No replies | {count} replies` 在 n=0 时渲染成 `0 replies`，而写成
  `No views | 1 view` 这种直觉顺序时，n=0 反而渲染成 `1 view`。英文一律写 3 条：
  `{count} 条回复 = No replies | 1 reply | {count} replies`、
  `查看全部 {count} 条回答 = View all answers | View the answer | View all {count} answers`。
  写 4 条不比 3 条多出效果（实测 n=0/1/≥2 取的仍是第 1/2/3 条），别为此加形态。

  `catalog.spec.ts` 只比**整串**的占位符总数，不看形态条数，所以「2 条的中文配 3 条的英文」是能过闸门的——
  闸门不会替你发现形态写错，得自己按上表核。上线前用真实 `vue-i18n` 把 n=0/1/2 渲染一遍最稳。
- **除了 `{count}` 还有别的占位符时，写不了多形态，只能靠不依赖单复数的措辞兜着。** 闸门比的是整串的
  占位符集合，每条形态都会重复那个占位符，`{name} has 1 reply | {name} has {count} replies` 的
  `{name}` 就变成了两个，直接对不上。`创建者 {creator} 和 {count} 位管理员` 这类只能写一条，
  于是用 `Created by {creator} · {count} admin(s)` 这种数量为 1 也读得通的写法。要根治得让闸门按
  形态比占位符，那是另一笔的事。
- **某个数量会不会取到 1，去看调用点，别看文案。** `spaces.detail.creatorAndAdmins` 的 `count` 传的是
  `admins.length - 1` 且只有 `length >= 2` 才会走到，所以 1 取得到、0 取不到。形态该写几条、有没有
  写错的风险，取决于这个区间，不取决于中文里有没有出现数字。
- **标点**：中文全角标点（，。：（））换成英文半角并加空格；句末的「。」在按钮和标签里**去掉**，
  在完整句子里换成 `.`。
- **占位符**：`{name}` 这类花括号占位符原样保留，不翻译、不调顺序、不改大小写。`catalog.spec.ts` 会检查两边占位符集合相同。
- **不要保留中文语序。** 「确认后提交」不是 `After confirmation submit`，是 `Confirm, then submit`。
- **「X 成功」译成过去分词短语，不要 `X success`。** 中文把结果名词化（发布成功 / 删除成功 / 创建任务成功），
  英文这类 toast 用被动、过去式，不加感叹号：`发布成功 = Published`、`删除成功 = Deleted`、
  `创建任务成功 = Task created`、`模板更新成功 = Template updated`、`添加话题成功 = Topic added`。
  `悬赏成功，等待回答 = Bounty added — awaiting answers`（破折号接后续状态）。
  **例外是已经有宾语的名词短语**：`保存成功` 这类没有具体对象的，用 `Saved` 就够，别写 `Save successful`。
- **「X 失败」统一用 `Couldn't <动词>`，带原因时用冒号。** `删除失败 = Couldn't delete`、
  `发布失败 = Couldn't publish`、`加载讨论失败 = Couldn't load discussions`、
  `邀请失败：{reason} = Couldn't invite: {reason}`。
  不用 `Failed to X`（更长、更像日志），也不用 `X failed`（像错误码）。带主语的句子级错误才用完整句。
- **删除确认是一句短问句，不是 `Are you sure you want to...`。**
  `确定要删除这条回复吗？ = Delete this reply?`、`确定要删除这个模板吗？ = Delete this template?`、
  `确定要删除域名组 {name} 吗？ = Delete the domain group {name}?`
  中文那句补充后果的后半句另起一句：`确定要删除这条讨论吗？其下的所有回复也会被删除。 =
Delete this discussion? All of its replies will be deleted too.`

## 4. 品牌词

**芝士 = Cheese。** 平台名，永远大写 C，永远不翻译，中文界面里就是「芝士」（既有 `joinCheese = "Join Cheese"`、
`welcomeBackToCheese = "Welcome back to Cheese"` 已经这么做了）。

## 5. 存疑——需要产品 owner 拍板

第 1、2、4 条我不擅自定（1 和 4 要 owner 给个话，2 只影响键的组织方式、不影响译法），
写在这里防止翻译任务各译各的；**第 3 条已经查清并落定，不再是存疑项**。

1. **「芝士」作为悬赏单位。** `questions.detail.bountyTip` 里「可获得 {bounty} 芝士」的「芝士」是**积分单位**，
   不是平台名。直接译成 `{bounty} Cheese` 会读成「获得 50 个 Cheese」，语义不通。
   需要一个单位名（`credits`? `Cheese credits`? 保留 `Cheese` 但加量词?）。**在定下来之前，涉及悬赏的键不要翻译。**
2. **「话题」在空间内的层级。** `spaces.detail.manageTopics` 显示空间里也有「话题」，而讨论也在空间里，
   两者关系（话题是讨论的分类标签？还是并列的另一种内容？）只能从界面看出这么多。
   英文都定为 topic 没有歧义风险，但如果两者其实是同一层概念，键的组织方式可能需要调整。

3. **中文把「赛题」和「任务」当成同一个东西在叫——已经查清，按 challenge 统一。**

   同一个实体，在 catalog 里有两套中文，而且**同一组键内部就自相矛盾**：

   | 位置 | 中文 | 键名 |
   |---|---|---|
   | `spaces.detail.publishTask.title` | 发布**赛题** | `publishTask` |
   | `spaces.detail.publishTask.taskName` | **任务**名称 | `publishTask` |
   | `spaces.detail.publishTask.taskLevel` | **任务**等级 | `publishTask` |
   | `spaces.detail.tasks.publishTask` | 发布**赛题** | `tasks` |
   | `spaces.detail.tasks.noTasks` | 暂无**赛题** | `tasks` |
   | `tasks.form.taskName` | **赛题**名称 | `tasks` |
   | `tasks.form.taskLevel` | **赛题**难度 | `tasks` |
   | `tasks.publish.title` | 发布**赛题** | `tasks` |

   两边是同一个实体的证据：字段名一一对应（`taskName` / `taskLevel` / `taskDescription`），
   而且 `PublishTask.vue` 与 `Tasks.vue` 用的是同一个 `TasksApi`（`@/network/api/tasks`）。
   外部佐证是既有英文：`tasks.form.accessControl.enableAccessRestrictionHint`（开启后，只有指定域名邮箱的
   用户才能查看和参与此**赛题**）对应 `account.someChallengesAreOnlyAvailableToParticular`
   （Some **challenges** are only available to…）。

   **所以英译一律 challenge，`spaces.detail.publishTask.*` 也一样。** 这条不再是存疑项。
   剩下的是要改**中文**的地方，不是翻译能解决的：
   - `tasks` 这个命名空间名不副实（装的是赛题，与 `website` 同一种病）；
   - `spaces.detail.publishTask.*` 的中文该跟 `title` 一样叫「赛题」，现在叫「任务」。


4. **团队 / 队伍 / 小队是三个词。** `notifications.TEAM_INVITATION`（邀请你加入**团队**）说的是平台的一等实体
   （`views/teams/`、角色、邀请）；`tasks.form.teamLockingPolicy`（**队伍**成员锁定策略）说的是赛题里报名的那组人；
   `tasks.form.team` 的选项文字又是「**小队**」。代码里三者都写作 team，中文里却是三个词。
   **我的建议是把中文统一成「团队」、英文统一成 team**，但这是改产品文案，需要 owner 确认。
   在此之前，涉及参赛队伍的键不要翻译。
