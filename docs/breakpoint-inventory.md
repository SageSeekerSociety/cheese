# 断点欠账清单

全仓还有 **47 处**宽高查询没用共享档位，基线 `08578667`。收口的目标只有两种落法：归到 `frontend/src/styles/breakpoints.scss` 的四个视口档，或者改成挂在内容列上的容器查询。

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

判据是同一句话：一行放不下，右边那一栏/那一格落到下面。现值 599 / 599.98 / 600 / 700 / 720 五种写法讲的是同一件事。

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

`SecurityView` 是设置面，`#2634` 已把十张设置页统到 767.98，这一处是漏网（政策是「整个设置面一条线」）。`style.css:1221` 的 `.admin-form-card` 在管理后台里，但它是全局样式表里的一格内距，跟着后台的容器查询走要跨文件引容器名，归 767.98 更省。

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

## 四、归 `$bp-wide`（1279.98）——2 处

同一个文件里 587 行已经写 1279.98，598 和 803 写 1280，两侧夹着同一个布局切换。

| 文件:行 | 现值 |
|---|---|
| `views/feedback/FeedbackDetailPage.vue:598` | min-width 1280 |
| `views/feedback/FeedbackDetailPage.vue:803` | min-width 1280 |

## 五、改成容器查询——7 处

这些判的是**一块内容区**有多宽，不是窗口有多宽，用视口是在问错人（§3.5、§10.10）。后台页要挂在 `AppPage` 的 `admin` 容器上；预览面板自己已经声明了 `container-type`，直接就地改成 `@container`。

| 文件:行 | 现值 | 改成 |
|---|---|---|
| `views/admin/AdminRunRecordsPageView.vue:338` | min-width 1200 | 后台容器（`admin`） |
| `components/admin/AdminKpiCard.vue:146` | 600 | 后台容器（`admin`，同文件的容器版已是 760） |
| `components/admin/credits/AdminPlanDialog.vue:447` | 700 | 弹窗自己的容器 |
| `components/panels/PanelChangesView.vue:674` | 720 | `@container`（宿主已有 `container-type`） |
| `components/panels/PanelPreviewView.vue:921` | 720 | `@container`（本文件 714 行已声明） |
| `components/panels/preview/RevisionList.vue:171` | 720 | 跟 `PanelPreviewView` 同一条容器线 |
| `components/panels/doc/DocHistory.vue:342` | 640 | `@container`（宿主 `PanelDocView` 已声明） |

阈值在归的时候定，不在这份清单里定：后台容器沿用 `#2634` 的 720 / 1320，面板沿用 720。

## 六、营销页 `views/home/`——9 处，自成一套

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

两种走法，选一种：把这 9 处也收到四个档（900→959.98 有先例），或者在 `docs/design-system.md` 里给落地页开一节，写明它自成一套、例外被承认。倾向前者，除非画面会因此变形。

## 七、例外——2 处

| 文件:行 | 现值 | 为什么留 |
|---|---|---|
| `components/admin/AdminQueueDetail.vue:894` | min-width 1280 | `#2634` 已定：`AdminQueuePage` 用 `matchMedia` 在真实视口上匹配，再当 JS 常量传下来，不是样式折叠；且宽档下它渲染在 `admin` 容器之外 |
| `components/room/ComposerActions.vue:328` | 480 | 只在 <480 收掉「交给某某」的名字留两个字。归 768 会在 480–767 这一段误收——那里输入框是整宽，长名字放得下 |

`ComposerActions` 这处要么承认成一个具名档，要么按现状留成注释里的例外。倾向后者：它管的是两个字放不放得下，不是布局折叠。

## 收口

归完之后加一道闸，走在 `frontend/stylelint.config.cjs` 里，跟颜色和圆角同一个机制：`@media` 里的宽度只许取四个档的值（或 `bp.$bp-*`），存量冻进 `frontend/stylelint-baseline.json`，之后只许减不许增。写完用 `bash .claude/scripts/check-repo-rules.sh --self-test` 一类的自证确认闸门真的会红，不是摆设。
