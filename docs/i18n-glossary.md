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

## 3. 形态约定

- **大小写：句子式（sentence case）。** 只大写第一个词和专有名词：`Create account`、`New project`、
  `Sign in`、`Delivery notes`。**不用 Title Case**（`Create Account` 是错的）。PR #929 在这点上不一致，以本节为准。
- **单复数**：导航项和列表标题用**复数**（`Spaces`、`Teams`），其余界面文本用**单数**（`Space name`、`New project`）。
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
