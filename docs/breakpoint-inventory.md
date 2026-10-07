# 断点欠账清单

`frontend/src` 里还有 **47 处** CSS 宽高查询 + **3 处** JS 视口判断没用共享档位，基线 `08578667`。收口的目标只有两种落法：归到 `frontend/src/styles/breakpoints.scss` 的四个视口档，或者改成挂在内容列上的容器查询。

口径是 `frontend/src` 一个目录，不是全仓。`docs/site/`（357 行宽度查询）是独立的文档站，`backend/sandbox/skills/showcase/templates/`（8 个文件）是出图模板，两者都不进产品骨架，不在这笔账里——第四批收口那道闸也只闸 `frontend/src`。

四个视口档只有这一份来源——`@media` 里用不了 CSS 变量，所以它们只能是预处理器常量：

| token | 值 | 对齐谁 | 这条线的意思 |
|---|---|---|---|
| `$bp-phone` | 767.98px | `SPLIT_MIN_WIDTH` 768 | 比它窄，项目里的话题列表和房间各占一整页 |
| `$bp-mobile` | 959.98px | Vuetify `mdAndUp` 960 | 比它窄就是手机外壳 |
| `$bp-compact` | 1179.98px | `COMPACT_DESKTOP_MAX_WIDTH` 1180 | 平板横放，二级侧栏可收 |
| `$bp-wide` | 1279.98px | Vuetify `lg` 1280 | |

`#2634` 已经按「就近归四档」收过一批（1000→960、1100→1180、900→960、700→768、599.98→767.98），并定下两条政策：管理后台的断点判**内容列**不判视口；`AdminQueueDetail` 的 1280 是 JS 常量匹配视口，刻意保留。这份清单按同一套政策往下走。

用法是 `@use '…/styles/breakpoints.scss' as bp;` 加 `@include bp.below(bp.$bp-phone)`。目前只有 3 个文件这么写（`artifact/ArtifactCompare.vue`、`tasks/Detail.vue`、`tasks/detail/InsightsView.vue`），其余都是把数字重抄一遍——这也是这批欠账能积累起来的原因。

## 一、归 `$bp-phone`（767.98）——21 处

绝大多数是同一句话：一行放不下，右边那一栏/那一格落到下面。现值 599 / 599.98 / 600 / 700 / 720 五种写法讲的是同一件事，收的是折叠点，不是判据。

| 文件:行 | 现值 |
|---|---|
| `components/common/Notification/NotificationItem.vue:337` | 599.98 |
| `components/feedback/FeedbackCard.vue:256` | 599.98 |
| `views/ProjectDocsView.vue:389` | 599.98 |
| `views/feedback/FeedbackCenterPage.vue:620` | 599.98 |
| `components/tasks/form/task-form.css:14` | 599 |
| `views/ProjectSkillsView.vue:541` | 599 |
| `views/spaces/detail/AuditTaskRow.vue:257` | 599 |
| `views/spaces/detail/PublishedTaskRow.vue:176` | 599 |
| `views/spaces/detail/TaskRow.vue:181` | 599 |
| `components/teams/knowledge/KnowledgeTable.vue:193` | 600 |
| `components/teams/knowledge/KnowledgeToolbar.vue:137` | 600 |
| `components/teams/knowledge/KnowledgeUploadDialog.vue:340` | 600 |
| `styles/docBlocks.css:925` | 600 |
| `views/tasks/Detail.vue:513` | 600 |
| `views/tasks/Detail.vue:610` | 600 |
| `views/teams/detail/Compute.vue:333` | 600 |
| `views/user/settings/SecurityView.vue:453` | min-width 600 |
| `components/usage/UsagePackList.vue:114` | 700 |
| `components/usage/UsageShareList.vue:97` | 700 |
| `style.css:1221` | 700 |
| `views/workspace/ChannelTasksView.vue:214` | 720 |

`style.css:1221` 的 `.admin-form-card` 在管理后台里，但它是全局样式表里的一格内距，跟着后台的容器查询走要跨文件引容器名，归 767.98 更省。

表里三处判据要单独说，落地时按这个办：

- `SecurityView.vue:453` 是这一组唯一的 **min-width**，方向和其余 20 条相反。现值 600 对照的是 Vuetify `sm`（同一段注释里「below md」和「600px」自相矛盾）。归 767.98 意味着 600–767 这段从「单行截断」变成「换行」——**改的是行为，不是写法**。设置面统一一条线是对的，但要在真浏览器里看一眼 600–767 的设备行。
- `KnowledgeTable.vue:193` 的判据不是「列落下来」，是「窄了就把截断从 300px 收到 150px」，同一个方向、同一个折叠点，归 767.98 不受影响。
- `ChannelTasksView.vue:214`（720）、`UsagePackList.vue:114` 与 `UsageShareList.vue:97`（700）、`task-form.css:14`（599）都是标准的「一行放不下」折叠，599 有 `#2634` 的先例。

## 二、归 `$bp-mobile`（959.98）——2 处

| 文件:行 | 现值 | 说明 |
|---|---|---|
| `components/common/LoadingSkeleton.vue:613` | 959 | 注释自己写着「Vuetify mdAndUp 的门槛是 960px」，959 是手误 |
| `views/spaces/detail/PinnedAnnouncements.vue:118` | 959 | 同上，手机外壳的门槛 |

## 三、已在档位上、只是写成了裸数字——4 处

数值等于 `$bp-mobile`，收的是写法不是行为。留着它们，`breakpoints.scss` 那道「唯一来源」就不是真的。

| 文件:行 | 现值 |
|---|---|
| `components/common/OfflineBanner.vue:69` | `width < 960px` |
| `style.css:764` | min-width 960 |
| `styles/common.scss:34` | min-width 960 |
| `styles/common.scss:117` | min-width 960 |

同类的还有一条在 JS 里（`lib/viewTransition.ts:31` 的 `'(min-width: 960px)'`），列在第八组，因为它要动的是 `breakpoints.scss` 的 JS 镜像常量，不是 CSS。

## 四、归 `$bp-wide`（1279.98）——2 处

同一个文件里 587 行已经写 1279.98，598 和 803 写 1280，两侧夹着同一个布局切换。

| 文件:行 | 现值 |
|---|---|
| `views/feedback/FeedbackDetailPage.vue:598` | min-width 1280 |
| `views/feedback/FeedbackDetailPage.vue:803` | min-width 1280 |

## 五、改成容器查询——7 处

这些判的是**一块内容区**有多宽，不是窗口有多宽，用视口是在问错人（§3.5、§10.10）。

能不能就地改成 `@container`，取决于最近的那个祖先有没有 `container-type`。`frontend/src` 里声明过的只有 10 处：`AppPage.vue:284`（`admin`）、`BaseTable.vue:321`（`agrid`）、`SettingsRow.vue:69`、`PanelPreviewView.vue:714`、`PanelDocView.vue:593`、`ChatTimeline.vue:583`、`PreviewSlides.vue:311`、`ProjectSettingsView.vue:410`、`NotificationsView.vue:259`、`RunningWorkView.vue:565`。表里逐个对过，只有 `PanelChangesView` 那一处要**新加**容器声明。

| 文件:行 | 现值 | 改成 |
|---|---|---|
| `views/admin/AdminRunRecordsPageView.vue:338` | min-width 1200 | 后台容器（`admin`） |
| `components/admin/AdminKpiCard.vue:146` | 600 | 待裁：删掉还是归 767.98（见下） |
| `components/admin/credits/AdminPlanDialog.vue:447` | 700 | 弹窗自己的容器 |
| `components/panels/PanelChangesView.vue:674` | 720 | `@container`，**要新加** `container-type` |
| `components/panels/PanelPreviewView.vue:921` | 720 | `@container`（本文件 714 行已声明） |
| `components/panels/preview/RevisionList.vue:171` | 720 | 跟 `PanelPreviewView` 同一条容器线 |
| `components/panels/doc/DocHistory.vue:342` | 640 | `@container`（宿主 `PanelDocView` 已声明） |

阈值在归的时候定，不在这份清单里定。后台容器 `#2634` 落的是 `max-width: 719.98px` 和 `max-width: 1319.98px`（`AdminMembersPage.vue:745`、`AdminModelsPage.vue:261,268`），但同一套里已经混进了裸值 `700` 和视口味的 `900`（`AdminSpacesPage.vue:438`、`AdminLiveSpine.vue:185`、`AdminModelsAudit.vue:209`）——这批按 `719.98 / 1319.98` 对齐，面板沿用 720。

`AdminKpiCard.vue:146` 那条 `@media (max-width: 600px)` 有个对不上的前提。文件注释说它是「给没有容器祖先的页面（模型页）兜底」，但模型页的链是 `AdminModelsPage.vue:108` → `AdminPage.vue:30` → `AppPage width="admin"` → `AppPage.vue:284` 的 `container: admin / inline-size`，**模型页是有容器祖先的**——同文件 `:152` 那条无名 `@container (max-width: 760px)` 按定义就该在模型页命中。真是这样的话这 600 是死代码，删掉即可；判断得靠真浏览器量模型页六位数字的字号，不在这份清单里下结论。

## 六、营销页 `views/home/`——10 处，自成一套

公开落地页不进产品骨架，它的 900 / 600 / 520 / 420 / 374 是画面自己的排版档，跟骨架的四个视口档不是一回事。

| 文件:行 | 现值 |
|---|---|
| `views/home/LandingFilm.vue:155` | 900 |
| `views/home/LandingUseCases.vue:272` | 900 |
| `views/home/landing.css:240` | 900 |
| `views/home/landing.css:366` | 900 |
| `views/home/landing.css:914` | 900 |
| `views/home/Download.vue:537` | `width < 600px` |
| `views/home/LandingUseCaseScene.vue:346` | 520 |
| `views/home/landing.css:1025` | 420 |
| `views/home/landing.css:1039` | 374 |
| `views/home/LandingShell.vue:53` | 900（JS `matchMedia`） |

两种走法，选一种：把这 9 处也收到四个档（900→959.98 有先例），或者在 `docs/design-system.md` 里给落地页开一节，写明它自成一套、例外被承认。倾向前者，除非画面会因此变形。

## 七、例外——2 处

| 文件:行 | 现值 | 为什么留 |
|---|---|---|
| `components/admin/AdminQueueDetail.vue:894` | min-width 1280 | `#2634` 已定：`AdminQueuePage` 用 `matchMedia` 在真实视口上匹配，再当 JS 常量传下来，不是样式折叠；且宽档下它渲染在 `admin` 容器之外 |
| `components/room/ComposerActions.vue:328` | 480 | 只在 <480 收掉「交给某某」的名字留两个字。归 768 会在 480–767 这一段误收——那里输入框是整宽，长名字放得下 |

`ComposerActions` 这处要么承认成一个具名档，要么按现状留成注释里的例外。倾向后者：它管的是两个字放不放得下，不是布局折叠。

严格说它判的是「这一次有多宽」而不是「窗口有多宽」，容器查询本来更贴题。排除它是因为代价不对等：这一行现在没有任何 `container-type` 祖先，要挂容器得先给动作行加声明，为两个字多一层行内尺寸包含不划算。这条取舍要写进代码注释，不然下一个人会觉得它是漏网的。

## 八、JS 里的视口断点——3 处

`@media` 之外还有一处会积欠账的地方：JS 自己判窗口宽度。`frontend/src` 里所有 `matchMedia` 逐条看过，判宽度的只有三条（其余是 `prefers-reduced-motion`、`hover`、`pointer`、`display-mode`，与宽度无关）：

| 文件:行 | 现值 | 归到 |
|---|---|---|
| `lib/viewTransition.ts:31` | `const WIDE = '(min-width: 960px)'` | 960 那一档，裸数字改成引用共享常量 |
| `views/home/LandingShell.vue:53` | `matchMedia('(width <= 900px)')` | 营销页那一组（第六组） |
| `components/feedback/SubmitFeedbackDialog.vue:24` | `const { xs } = useDisplay()` → `:fullscreen="xs"` | 归 767.98，**这批唯一一处藏在 JS 里的散值** |

`viewTransition` 和 `SubmitFeedbackDialog` 这两处要动 `breakpoints.scss` 的 JS 侧镜像常量，光加一道 `@media` 的闸管不到它们。

不算欠账、不必列的两类：Vuetify 的 `mdAndUp`（=960，正好是 `$bp-mobile`）在 15 个组件里用着，`PinnedAnnouncements.vue:16` 的 `smAndDown` 也是 960；`AdminModelDetailDrawer.vue:42` 与 `SkillDetailDrawer.vue:37` 拿 `useDisplay().width` 做的是「抽屉不宽过视口」的钳制，不是分档。

## 收口

归完之后加一道闸，走在 `frontend/stylelint.config.cjs` 里，跟颜色和圆角同一个机制：`@media` 里的宽度只许取四个档的值（或 `bp.$bp-*`），存量冻进 `frontend/stylelint-baseline.json`，之后只许减不许增。写完用 `bash .claude/scripts/check-repo-rules.sh --self-test` 一类的自证确认闸门真的会红，不是摆设。

这道闸只覆盖 `@media` 和 `frontend/src`，两处留白要一起写进 `docs/design-system.md`，否则下一个人会以为「闸绿了就是没有散值」：

- **JS 侧**（第八组那三条）闸不到，只能靠 `breakpoints.scss` 的 JS 镜像常量做唯一来源。
- **容器查询的阈值**同样没有单一来源。整个 `frontend/src` 里有 60 多条 `@container`，散着 310 / 440 / 480 / 559 / 560 / 640 / 660 / 672 / 700 / 720 / 760 / 900 / 1000 / 1040 / 1319.98 / 1320 / 1440 等值，而且同一件事两种写法并存：后台容器一处写 `max-width: 719.98px`、另一处写 `700px`。这一批只把 7 处视口查询改成容器查询，不动既有阈值——但要在 `docs/design-system.md` 里点名它还是一笔没归的账，别让人以为收口等于清零。
