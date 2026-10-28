---
title: 场景清单
kind: 参考
summary: 前端每一个「场景」——路由页和工作面板——今天能不能脱离后端、路由和 store 单独渲染，一张表列清哪些已经可以、哪些卡在哪一步、一共多少个，以及这条线从 2026-09-30 起由哪条检查看住。
covers:
  - .claude/scripts/frontend_grade.py
  - .claude/scripts/scene-ratchet.py
  - frontend/scripts/scene-split.mjs
  - frontend/scripts/scene-split-core.mjs
  - frontend/src/components/panels/
  - frontend/src/views/demo/catalog.ts
  - frontend/src/views/demo/catalogFixtures.ts
---

# 场景清单 {#scenes}

组件预览站（`/demo/catalog`）能做什么，取决于**谁被注册进去了**；而这个集合是攒出来的，没人知道还剩多少、剩下的差在哪。这一页把它翻过来数一遍：前端一共有多少个能叫「场景」的东西，几个今天就能脱离后端单独跑一遍，剩下的各卡在哪一步。

> 讲：每个路由页和工作面板今天站在哪一档、卡住的原因是什么、一共多少个、下一步先动哪一批，以及**从今天起这条线怎么被机器看住**。不讲：怎么往目录里加一个组件（见[前端结构](/dev/frontend#catalog)），A/B/C/D 的判据和怎么跑（见[架构指标](/dev/arch-metrics#metrics)），组件边界那三道闸本身（见[前端结构](/dev/frontend#gates)）。

## 「单独渲染」是什么意思 {#standalone}

**给一组 props，它就能出画面**：不要后端（不 import `@/api`、`@/network`、`@/services/*`，也不经别的模块绕过去），不要路由（不读 `useRoute` / `$route` / `$router`，自己也不画 `<router-view>` / `<router-link>`），不要任何会自己去取数的 store。判据就是[架构指标](/dev/arch-metrics#metrics)那套 A/B/C/D：

| 档 | 什么情况 | 要补什么才能单独跑 |
|---|---|---|
| **A** | 只吃 props 和事件 | 不用补，只差一份形状数据 |
| **B** | 只读应用外壳的 store（`usePageTitleStore`、`useNavigationStore`） | 在外壳里装一个空的同名 store，或者把那两个读点改成 props |
| **C** | 自己取数——直接 import、经中间模块绕、自己写 `fetch`/`axios`——或者读业务 store | 把取数那一段挪出去：外面拿数据，它只收结果。已合的样板：#2193 的 `views/spaces/detail/settings/BasicInfo.vue` 取数、读 store、保存、删除后跳转，同目录 `BasicInfoView.vue` 只收 props 和事件，A 级、已冻结在 ready |
| **D** | 还绑在挂载位置上：读路由、画 `<router-view>` / `<router-link>`、`$parent` / `$root`、事件总线、`provide` / `inject` | 先把「从哪来」改成 props 或事件，再谈数据 |

判据只有一份，写在 `.claude/scripts/frontend_grade.py` 里：看板（`arch-metrics.py`）和下面那条闸门读的是同一个函数，所以不会出现「看板说是 A、闸门说不是」。`vue-router` 按**脚本里出现的 import 和模板里画出来的标签**算：注释先被去掉，写在注释里的话不算（`components/common/NavLink.vue` 的注释就在说它替谁干活，它是 A 级）。**A 档在这一页就叫「能单独跑」**，[闸门](#ratchet)冻的就是它。

## 一共多少 {#totals}

2026-10-14 按 `--list` 重数的（这一页带数字的表都是这一天重算的，之前几版的口径记在下面那段日志里）：

| 场景 | 一共 | A 今天就能单独跑 | 容器（画面在同目录的 `<页面名>View.vue` 里） | C 卡在取数 | D 卡在路由 / `$parent` / `inject` |
|---|---|---|---|---|---|
| 路由页（`src/views/`，router 表里挂上去的每一页） | 125 | 4 | 98 | 5 | 18 |
| 工作面板（`src/components/panels/` 下的 SFC） | 62 | 62 | — | 0 | 0 |

4 + 98 + 62 = 164 个今天就能单独跑；5 + 18 = 23 个还跑不起来。一个场景可以同时踩中好几条（44 页直接 import 了取数模块，110 页经中间模块绕到取数，75 页读路由），**档位取最差的那条**——所以 D 那一列不是「只差一个路由」。容器页不算场景，算场景的是它的视图。

（2026-09-30 首次数这套数时是 111 个路由页 + 27 个面板，其中 10 个能单独跑——那之前 #2152 / #2154 刚删掉 24 个旧场景（旧 `courses`、`board/pages/*`、`discussions` 等，功能下线，不是改名）。之后的每次变动记在下面那段日志里。）

按目录看那 125 个页面：

| 目录 | 一共 | A | 容器 | C | D |
|---|---|---|---|---|---|
| `views/`（顶层） | 19 | 1 | 11 | 2 | 5 |
| `views/account/` | 12 | 0 | 12 | 0 | 0 |
| `views/account/emailCode/` | 2 | 0 | 2 | 0 | 0 |
| `views/account/recover/password/` | 2 | 0 | 2 | 0 | 0 |
| `views/account/signup/` | 2 | 0 | 2 | 0 | 0 |
| `views/admin/` | 13 | 0 | 10 | 1 | 2 |
| `views/feedback/` | 4 | 0 | 3 | 0 | 1 |
| `views/home/` | 5 | 2 | 2 | 1 | 0 |
| `views/legal/` | 1 | 0 | 1 | 0 | 0 |
| `views/question/` | 4 | 0 | 4 | 0 | 0 |
| `views/spaces/` | 2 | 0 | 1 | 0 | 1 |
| `views/spaces/detail/` | 23 | 0 | 23 | 0 | 0 |
| `views/tasks/` | 2 | 0 | 0 | 0 | 2 |
| `views/tasks/detail/` | 5 | 0 | 3 | 1 | 1 |
| `views/teams/` | 5 | 0 | 5 | 0 | 0 |
| `views/teams/detail/` | 5 | 0 | 5 | 0 | 0 |
| `views/user/settings/` | 8 | 1 | 7 | 0 | 0 |
| `views/welcome/` | 1 | 0 | 1 | 0 | 0 |
| `views/workspace/` | 10 | 0 | 4 | 0 | 6 |

今天就能单独跑的 4 页：`views/404.vue`、`views/home/Landing.vue`、`views/home/Solutions.vue`、`views/user/settings/General.vue`。另外 98 页的画面在同目录的 `<页面名>View.vue` 里，那 98 个视图冻结在 `ready`。

2026-10-05，account 线（登录、注册、找回密码、OAuth 回调、实名/安全/资料）21 页拆完：每页当容器，画面进同目录的 `<页面名>View.vue`，视图只吃 props 和事件。`--update` 之后基线是 **82 个 ready、97 个 debt**（此前 debt 118），这 21 页全部离开欠债表。（当时那两张汇总表还是 2026-09-30 的口径——它们到 2026-10-13 才重算，见下面那天；「页面」一表里对应的行已改成「容器」并写出画面在哪。）2026-10-07 的 `frontend/scene-baseline.json` 是 **102 个 ready、77 个 debt**。2026-10-08 判据改了两处，基线从 **110 个 ready、74 个 debt** 变成 **109 个 ready、74 个 debt**：注释里提到 `vue-router` 不再算依赖（#3066）；模板里画 `<router-view>` / `<router-link>` 现在算 D，`export { … } from` / `export * from` 也算一条边（`export type` 不算）。前一条让仓库里 12 个已冻结在 `ready` 的场景改用 `components/common/NavLink.vue`（改完仍是 A）：`views/account/` 那 8 页、`views/user/settings/ProfileView.vue`、`views/user/settings/RealNameView.vue`、`views/home/Landing.vue`、`views/home/Solutions.vue`。后一条让 `views/tasks/detail/Brief.vue` 经 `views/tasks/composables/index.ts` 的 barrel 够到 `@/api`，落进 container、冻结转到同目录的 `BriefView.vue`（110 → 109，见[这份清单怎么来的](#how)）。2026-10-12，团队、空间分析和管理页的 C 档拆页（含 `teams/Pending.vue`、`spaces/detail/analytics/Learning.vue`）做完后，基线是 **137 个 ready、48 个 debt**。2026-10-13，`views/spaces/detail/analytics/` 里四个 C 档页（总览 / 参与者 / 出题人 / 题目）拆成「页面当容器 + 同目录 `<页面名>View.vue`」，导出按钮拉文件那一段挪到页面层的新 composable：这一拆自己多了四个 A 档场景（四个 `<X>View.vue`）。同一次 `--update` 还把另外四个**本来就是 A 档、只是没进过名单**的场景一起冻上 —— `components/panels/doc/blocks/CanvasBlockView.vue`、`views/admin/AdminExternalProblemPageView.vue`、`views/admin/AdminMachinesPageView.vue`、`views/user/settings/ModelConnectionsView.vue`（拆之前树上共有 141 个 A 档，名单里只写着 137 个），基线 → **145 个 ready、44 个 debt**。表里这一段六行一并改成容器：`Alerts.vue`、`Learning.vue` 是更早两批拆的、表当时没跟上。同一天把这一页按棘轮整个重算了一遍。此前几版漏改的远不止那六行——还有 23 行是更早拆的页、「档」列仍写着 C/D（`views/` 顶层七页、`views/admin/` 三页、`views/spaces/detail/` 八页、`views/teams/` 五页），这次不逐行补，直接照 `--list` 把「页面」一表重新生成（当时打出来 127 行、含 79 行容器，其中一行是已删的页，见本段末尾），并把「一共多少」「按目录看」「两份名单」「工作面板」「卡在哪一步」几处的数字一并算到 2026-10-13（它们此前停在 2026-09-30 / 10-07 的口径）。重算下来另有两条事实：面板从 27 个涨到 62 个、且全部是 A；`components/panels/PanelCard.vue` 已经删了。同一天再往后的一批把 `question/Ask`、`TeamInviteView`、`teams/detail/TeamProjects`、`spaces/Index` 四页拆成容器 + 同目录 `<页面名>View.vue`，四个视图进 `ready`：基线 **148 个 ready、40 个 debt**，路由欠账 20 → 18；这一批也照当天的树把这一页又重算了一遍，并改掉上一版的一处硬错——上一版表里的 `views/admin/AdminExternalProblemPage.vue` 是已经被删的页（#3658 的 `3d2a19e40` 把 YOJ 接线摘掉时删的），总数因此从 127 落到 126，`views/admin/` 是 13 页而不是 14 页。2026-10-14，再一批四页（顶层 `MyConnectionsView`、`ProjectSkillsView`，`admin/AdminFeaturePage`，`feedback/FeedbackDetailPage`）拆成容器 + 同目录 `<页面名>View.vue`，四个视图进 `ready`；`AdminFeaturePage` 顺带不再读地址——路由记录上的 `props: true` 把 id 当 prop 交给组件，所以它这一页从 D 落到 C。基线 **152 个 ready、36 个 debt**，路由欠账 18 → 14；这一页的数字也照当天的树又算了一遍。还是 2026-10-14，再一批两页（`question/DetailAnswerList`、`spaces/detail/analytics/Index`）拆成容器 + 同目录 `<页面名>View.vue`，两个视图进 `ready`；`DetailAnswerList` 顺带把采纳/投票/收藏挪进新 composable `useAnswerActions.ts`，`AnswerCard.vue` 只收 props 和事件，也从 C 落到 A。这一批删掉了两个只被自家页面渲染的中间件：`components/questions/AnswerList.vue`（画面进了 `DetailAnswerListView.vue`）和 `views/spaces/detail/analytics/AnalyticsLayout.vue`（画面进了 `IndexView.vue`）。基线 **154 个 ready、34 个 debt**，路由欠账 14 保持 14；这一页的数字也照当天的树又算了一遍。还是 2026-10-14，队列那一批（`admin/AdminQueuePage`）也拆成容器 + 同目录 `AdminQueuePageView.vue`，这个视图进 `ready`；详情（`components/admin/AdminQueueDetail.vue`）和「指派给谁」（`components/admin/AdminAssigneeSelect.vue`）顺带不再读 store、不再自己去搜候选人，改成吃 props 出事件，两件都从 C 落到 A —— 这一批新增的三个 A 档都补进了组件预览站（`catalogQueue.ts`）。删掉两个只被自家页面渲染的中间件：`components/feedback/AdminFeedbackDetailDrawer.vue`（画面进了 `AdminQueuePageView.vue` 里那个窄屏抽屉）和 `views/feedback/AdminFeedbackPage.vue`（`/admin/feedback` 这条老地址现在直接指向队列页，不再是一层薄壳 —— 见 `router/feedback.ts` 的文件头）。基线 **155 个 ready、32 个 debt**，路由欠账 14 保持 14；这一页的数字也照当天的树又算了一遍。还是 2026-10-14，首页那一批（`home/HomeSidebar`、`home/HomeHub`）拆成容器 + 同目录 `HomeSidebarView.vue` / `HomeHubView.vue`，两个视图进 `ready`；两页共用的取数（团队名单、待办数、四个弹窗的开合与进行中/出错、改名与解散与转让、加入空间）挪进新 composable `useTeamNav.ts`，`home/HomeNav.vue` 顺带从 D 落到 A，`home/JoinSpaceDialog.vue` 从自己取数改成吃 props 出事件（`InboxView` 那边跟着改用同一个 `useJoinSpace.ts`）。基线 **157 个 ready、30 个 debt**，路由欠账 14 保持 14（`HomeHub` 仍读地址：宽屏上要把这一页重定向到收件箱）；这一页的数字也照当天的树又算了一遍。还是 2026-10-14，题目详情那一批（`question/Detail`、`question/DetailAnswer`）拆成容器 + 同目录 `DetailView.vue` / `DetailAnswerView.vue`，两个视图进 `ready`；`question/Detail` 一直渲染的 `components/questions/InvitationList.vue` 也从 C 落到 A——它原来经页面够到取数层，现在只吃 props 出事件，名单的取数挪进 `useQuestionInvitations.ts` 新增的 `active` 开关里、由页面的邀请弹窗开合驱动，弹窗没开就不发请求。这一批新增的三个 A 档都补进了组件预览站（`catalogScenesPages3.ts`）。基线 → **159 个 ready、28 个 debt**，路由欠账 14 保持 14（两页仍读地址：`question/Detail` 靠 `:questionId` 取题目、`question/DetailAnswer` 靠 `:answerId` 取那一份回答，参数还没改走 `props`）；这一页的数字也照当天的树又算了一遍——`views/question/` 4 页全部成了容器，这个目录的欠债出清。还是 2026-10-14，小队那一批（`teams/Index`、`teams/Detail`）拆成容器 + 同目录 `IndexView.vue` / `DetailView.vue`，两个视图进 `ready`；建队弹窗里那段简介由画面从编辑器正文里取好、连同「要创建」一起交给容器，取数与那条创建请求仍留在页面。这一批新增的两个 A 档都补进了组件预览站（`catalogScenesMisc.ts`），顺带把 `HomeTeamsMine` / `HomeTeamsPending` 两条按名字指路的去处补进演示路由（`demoRouter.ts`）—— 少了它们，页头上那三条页签在预览站上整页都画不出来。基线 → **161 个 ready、26 个 debt**，路由欠账 14 保持 14（两页仍读地址：`teams/Detail` 靠 `:handle` 取小队、还往子页提供小队数据，`teams/Index` 靠路由往外跳）；这一页的数字也照当天的树又算了一遍——`views/teams/` 5 页全部成了容器，这个目录的欠债也出清。还是 2026-10-14，小队详情目录里最后一页（`teams/detail/Members`）拆成容器 + 同目录 `MembersView.vue`，视图进 `ready`；它一直渲染的那张「团队地址与加入」卡（`teams/detail/TeamJoinLinkCard.vue`）也从 D 落到 A —— 取数、写入、复制失败怎么提示、换完地址把浏览器带到哪全挪到页面层，卡只吃 props、往上报动作，地址那一格自己留一份草稿。这一批新增的两个 A 档都补进了组件预览站（`catalogScenesMisc.ts` 的 `team-members-view` / `team-join-link-card`）。基线 → **162 个 ready、25 个 debt**；`scene-debt-baseline.json` 的 `--update` 一次收到 children 35 / routes 10 / network 0，其中这一批自己还掉的是 `teams/detail/Members` 那条路由和 `Members -> TeamJoinLinkCard` 那条子件边，另外几条是更早几批已经还清、当时没跟着收紧的（`question/Detail`、`question/DetailAnswer`、`teams/Detail` 三条路由和 `question/Detail -> InvitationList` 那条子件边）—— `--update` 只减不增，顺手一起扫掉。这一页的数字也照当天的树又算了一遍——`views/teams/detail/` 5 页全部成了容器。还是 2026-10-14，反馈那两页（`feedback/FeedbackCenterPage`、`feedback/FeedbackMinePage`）也拆成容器 + 同目录 `FeedbackCenterPageView.vue` / `FeedbackMinePageView.vue`，两个视图进 `ready`；两页共用那张卡片（`components/feedback/FeedbackCard.vue`）顺带从 C 落到 A——它原来自己读 store、自己发支持请求，现在只吃 props、把「支持了哪一条」报上去，请求由各自的页面发（页面上点支持的观感不变：不本地 ±1、计数仍取服务端回的那份）。这一批新增的两个 A 档都补进了组件预览站（`catalogFeedback.ts` 的 `feedback-center-page-view` / `feedback-mine-page-view`，这是这两页第一次能在预览站单独看）。基线 → **164 个 ready、23 个 debt**；`scene-debt-baseline.json` 的 `--update` 把这一批还掉的两条子件边一并收走（`FeedbackCenterPage -> FeedbackCard`、`FeedbackMinePage -> FeedbackCard`，children 35 → 33）。这一页的数字也照当天的树又算了一遍——`views/feedback/` 4 页里 3 页成了容器（`FeedbackSubmitPage` 仍欠债，它经 `SubmitFeedbackForm.vue` 取数）。

## 从今天起它是一条闸门 {#ratchet}

2026-09-30 起，「能单独跑」不只是一张表上的状态：`pnpm run lint:scenes` 会逐场景重新判一遍，该红的红、该退 2 的退 2，commit 和 CI 都跑（`--self-test` 在 `repo-guards.yml` 里）。产品定的规则是**只拦新增**：今天能单独跑的只许多，今天跑不起来的允许继续躺着，**从今往后新加的每一个场景必须第一天就能单独跑**。

冻在哪：**分支与 `origin/main` 的合并基准那一棵树**，用它自己这套判据重新判一遍。

| 那一棵树上的场景 | 在这个分支上只能 |
|---|---|
| 已经能单独跑（2026-10-14 有 164 个：62 个面板、98 个容器视图、4 个本来就 A 的路由页） | 不能掉档，掉档就红 |
| 跑不起来（同期 23 个：5 个 C、18 个 D） | 随便躺着；变 A 了会列为「比冻结集好」，不必做什么 |
| 树上还没有的（这个分支新加的） | 第一天就得能单独跑，不然红 |

「新」和「旧」由**树本身**分，不再由一张表分——这也是为什么 2026-10-14 起仓库里没有 `frontend/scene-baseline.json` 了。那张表是同一份冻结集的缓存，九十天里四十来个提交碰过它，两条在飞的分支拆同一页就会在它上面撞车；改成读合并基准以后，分支改不动冻结集，能改的只有场景本身。六种结果：

| 情况 | 结果 |
|---|---|
| `ready` 里的场景掉档（A 变成 B/C/D） | 红（退出 1），它拦的就是这个 |
| 新场景（新挂的路由页、新加的面板）不是 A | 红（退出 1） |
| 页面不是 A，但它渲染同目录的 `<页面名>View.vue`，而那个视图是 A | 过：页面算这个视图的容器，冻结的是视图（见下） |
| 老场景本来就不是 A | 过（它在 `debt` 里） |
| `debt` 里的场景变 A 了 | 过，只写一句「was debt, is standalone-ready now」；没有名单要收紧 |
| 判断不了（没有 `frontend/src/`、合并基准那棵树读不出来、场景文件读不了） | 退出 2，永远不算过 |

场景从哪来：**页面**是 router 的 import 图能走到的每个 `views/*.vue`（新挂一页就自动进集合，不用改这里），**面板**是 `components/panels/` 目录下的每个 SFC，**视图**是页面 import 的那个同目录 `<页面名>View.vue`。所以「新场景」不需要谁记得去登记。

**新页面可以是容器。** 路由落到页面，读地址、取数、保存本来就是页面的活；一刀切地不许页面做这些，新页面的数据就没有地方放了。所以页面可以留着这些活，前提是把画面交给一个视图：同目录的 `<页面名>View.vue`，由页面用值 import 进来（`import type` 不算）**并且模板真的渲染它**——只 import 不渲染不算容器。渲染用的是静态判断：HTML 注释里的标签不算渲染，属性值里的 `</template>` 不会截断扫描，`<component :is>` 判不了、直接不当容器。除了视图本身，模板渲染的其他组件也都要么是包里的、Vuetify（按仓库 vuetify 的自动导入表认名字，不是按 V 前缀）、要么是能核实为 A 的自家组件，不然取数只是往下挪了一层；小写标签只有真是 HTML/SVG 元素才算原生，否则按组件核（`<child />` 会去找 `child`/`Child` 的 import）；本地绑定的名字优先于内置名——import 的照常评级，`const RouterView = …` 这类声明让名字无从核实，核实不了就不豁免。视图单独算一个场景，和别的场景一样评级、冻结，新的必须第一天就是 A；页面按容器处理，不算场景，每次检查都重新看它的视图。`--list` 里容器那一行会写「container of …」。容器规则只给页面：面板没有路由，穿同样衣服的面板就是一个会取数的面板，照常评级。

### 怎么把一个场景改成能单独跑 {#make-standalone}

一句话：**外面拿数据，它只收结果**；改法按卡住的那条走。

| 卡在哪 | 怎么改 |
|---|---|
| 直接取数（`@/api`、`@/network/*`、`@/services/*`） | 把取数挪到 composable 或页面里，场景只收 props。已合的样板：#2193 的 `views/spaces/detail/settings/BasicInfo.vue` 当容器（经 `@/network/api/spaces` 取数、读 store、保存、删除后跳转），画面全在同目录 `BasicInfoView.vue`——只吃 props 和事件，A 级，已冻结在 ready |
| 读路由（`useRoute` / `$route`） | 页面读地址，把要用的值作为 props 交给它的 `<页面名>View.vue` |
| 读业务 store（`space`、`workspace`、`feedback`……） | 同上：读 store 的那一层留在页面或 composable 里，视图收结果 |
| 新页面要读写数据 | 页面当容器：取数、保存、读地址都留在页面，画面放进同目录的 `<页面名>View.vue`，数据按 props 进，操作按事件出 |
| `provide()` / `inject()` | 先改成 props / 事件——它是「从哪来」的问题，排在数据前面 |
| `$parent` / `$root`、事件总线 | 改成 props / 事件 |

改完 `pnpm run lint:scenes`。判成 A 就过了，不必登记什么——合并进主干那天起，它就在两边都同意的那棵树里，从这里往后不许再掉档。

#### 脚手架：先把画面那一半拆出来 {#scene-split}

「把画面放进 `<页面名>View.vue`」这一步是机械的，有工具做：`pnpm run scene:split -- src/views/某页.vue` 打出计划——视图该收哪些 props、哪些 `v-model`、哪些事件，哪些 import 要搬过去，脚本里的 `route.params.X` 会改写成 `props.X`、那条路由记录要不要补 `props: true`——外加一份**它不敢替你决定的地方**（模板里的 `<slot>`、`$route`、解析不出来的表达式、`<component :is>`）。

加 `--write` 才落盘；只要「需要人来」那份清单非空，它一个字节都不写，也不覆盖已经存在的视图。写的是骨架：props 的名字和类型它认得出来，**哪些值该由页面传下来、这一页该不该拆，是产品决定**，工具只把机械的部分做掉。写完照旧跑上面三条命令，页面那半边（取数、路由、store）自己接着改。

判据在 `frontend/scripts/scene-split-core.mjs`，样例页面盯着它：`node --test scripts/scene-split-core.test.mjs`（在 `frontend/` 下跑）。

### 没有基线可以更新 {#update-baseline}

冻结集是读出来的，不是写下来的，所以这一条没有 `--update`。

```bash
pnpm run lint:scenes                                # 检查（CI 跑的就是它）
python3 .claude/scripts/scene-ratchet.py --list     # 每个场景的档和理由，用来重算这一页的表
```

两条命令都不动文件。以前能靠改名单过关的两件事，现在都不可能：掉了档的场景只能改回去（或走 review 改判据），做不到的场景只能改到能单独跑。判据本身要改，改的是合并基准两边都会用到的那一个 `frontend_grade.py`——一次改两边，没有「新判据抓到的旧场景」被为难这一说。

### 进没进目录，不归这条检查 {#catalog-ratchet}

「能单独跑、但没挂进 `/demo/catalog`」不是这条检查的事，也没有别的检查拦它。哪些组件进了目录，[组件总览](/dev/ref-components)从预览站自己的代码读：从 `src/views/demo/catalog.ts` 出发，它本身、以及它 `import` 进来**并展开**进列表（`...X_ENTRIES`）的分册，其中某个文件 `import` 了这个 `.vue`，并且把它写成某条条目的 `component:`。进了目录的组件能不能真挂起来，归 `pnpm exec vitest run src/views/demo/catalog.spec.ts`。

加一个组件到目录的三步见[前端结构](/dev/frontend#catalog)。

## 页面 {#pages}

`src/views/` 下被 `src/router/` 挂上去的每一页。这张表是 2026-10-14 按 `python3 .claude/scripts/scene-ratchet.py` 重算的快照——**棘轮才是权威**，对不上时以它为准，要看现在的就跑那条命令。「卡在哪」把棘轮给的理由都列出来（一页可能好几条）；容器那一行的「本页留着」就是它为什么还不是 A。档和理由分两处拿：`--list` 给每一页的档和理由、容器再带一句 `(container of …)`，但是**容器只打那一行、容器页自己的理由不打**；`--json` 只为在册的欠债场景打理由。所以容器那两栏是把 `--list` 的容器关系和判据对每一页跑出来的理由拼起来的，不是照抄一条命令的输出。

| 页面 | 档 | 卡在哪 |
|---|---|---|
| `views/404.vue` | A | 只吃 props 和事件 |
| `views/ConnectView.vue` | 容器 | 画面在 `ConnectViewView.vue`（A 级）；本页留着：直接取数（`api.ts`）；读路由 |
| `views/DocsSignIn.vue` | 容器 | 画面在 `DocsSignInView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/docs.ts` 取数；读路由 |
| `views/InboxView.vue` | C | 直接取数（`api.ts`）；经 `composables/useJoinSpace.ts` 取数；经 `views/home/NotificationFeed.vue` 取数；读 store（workspace） |
| `views/MarketView.vue` | 容器 | 画面在 `MarketViewView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/machineSkills.ts` 取数 |
| `views/MyArchivedProjectsView.vue` | 容器 | 画面在 `MyArchivedProjectsViewView.vue`（A 级）；本页留着：直接取数（`api.ts`）；读 store（workspace） |
| `views/MyConnectionsView.vue` | 容器 | 画面在 `MyConnectionsViewView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/feishu.ts` 取数；读路由 |
| `views/MyDevicesView.vue` | C | 直接取数（`api.ts`）；经 `components/DeviceLiveViewer.vue` 取数；经 `lib/desktop.ts` 取数；经 `api/ownAgents.ts` 取数；经 `commands/copy.ts` 取数；直接取数（`services/account.ts`） |
| `views/PreviewOpenView.vue` | 容器 | 画面在 `PreviewOpenViewView.vue`（A 级）；本页留着：直接取数（`api.ts`）；读路由 |
| `views/ProfileView.vue` | D | 经 `composables/useChosenAvatar.ts` 取数；直接取数（`api.ts`）；模板里画 `<router-link>` |
| `views/ProjectArtifactView.vue` | D | 直接取数（`api.ts`）；经 `components/ArtifactVersionPreview.vue` 取数；经 `views/artifact/ArtifactCompare.vue` 取数；经 `commands/copy.ts` 取数；读路由 |
| `views/ProjectDocsView.vue` | D | 经 `composables/useProjectAgentName.ts` 取数；直接取数（`api.ts`）；经 `api/projectDocuments.ts` 取数；经 `components/work/PanelDocHost.vue` 取数；经 `query/project.ts` 取数；读路由；读 store（workspace） |
| `views/ProjectLibraryView.vue` | D | 直接取数（`api.ts`）；经 `components/library/LibraryDocumentPage.vue` 取数；经 `composables/useLibraryDocuments.ts` 取数；经 `composables/useLibraryNaming.ts` 取数；经 `composables/useLibraryPages.ts` 取数；经 `composables/useOpenLibraryDocument.ts` 取数；经 `lib/libraryApi.ts` 取数；经 `views/library/LibraryVersionsDialog.vue` 取数；读路由；读 store（workspace） |
| `views/ProjectRoutinesView.vue` | 容器 | 画面在 `ProjectRoutinesViewView.vue`（A 级）；本页留着：经 `composables/useProjectAgentName.ts` 取数；经 `composables/useRoutineList.ts` 取数；直接取数（`api.ts`）；读路由；读 store（workspace） |
| `views/ProjectSearchView.vue` | 容器 | 画面在 `ProjectSearchViewView.vue`（A 级）；本页留着：经 `api/projectSearch.ts` 取数；经 `views/workspace/search/docs.palette.ts` 取数；经 `views/workspace/search/library.palette.ts` 取数；经 `views/workspace/search/messages.palette.ts` 取数；经 `views/workspace/search/projectDocs.palette.ts` 取数；经 `views/workspace/search/tasks.palette.ts` 取数；读路由 |
| `views/ProjectSettingsView.vue` | D | 经 `composables/useBranchProtection.ts` 取数；经 `composables/useProjectAddress.ts` 取数；经 `composables/useProjectArchive.ts` 取数；经 `composables/useProjectComputeSettings.ts` 取数；经 `composables/useProjectDefaultModelSettings.ts` 取数；经 `composables/useProjectEnvironmentSettings.ts` 取数；经 `composables/useProjectExport.ts` 取数；经 `composables/useProjectMcpSettings.ts` 取数；经 `composables/useProjectSettings.ts` 取数；经 `composables/useProjectTaskNamingSettings.ts` 取数；经 `api/channels.ts` 取数；经 `api/environment.ts` 取数；经 `components/DeviceSessionsSwitch.vue` 取数；经 `components/settings/AgentTeamSettings.vue` 取数；经 `components/settings/CreditsPanel.vue` 取数；经 `components/settings/OwnAgentsSettings.vue` 取数；经 `components/settings/ProjectChannelSettings.vue` 取数；经 `query/project.ts` 取数；读路由；读 store（workspace） |
| `views/ProjectSkillsView.vue` | 容器 | 画面在 `ProjectSkillsViewView.vue`（A 级）；本页留着：经 `api/projectSkills.ts` 取数；经 `query/project.ts` 取数；读路由 |
| `views/SiteOpenView.vue` | 容器 | 画面在 `SiteOpenViewView.vue`（A 级）；本页留着：直接取数（`api.ts`）；读路由 |
| `views/TeamInviteView.vue` | 容器 | 画面在 `TeamInviteViewView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/teams.ts` 取数；读路由 |
| `views/account/AddEmail.vue` | 容器 | 画面在 `AddEmailView.vue`（A 级）；本页留着：经 `api/http.ts` 取数；经 `api/userAuth.ts` 取数；经 `api/users.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/account/AppSignInFinish.vue` | 容器 | 画面在 `AppSignInFinishView.vue`（A 级）；本页留着：经 `api/userAuth.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/account/AppSignInStart.vue` | 容器 | 画面在 `AppSignInStartView.vue`（A 级）；本页留着：读路由 |
| `views/account/BackToApp.vue` | 容器 | 画面在 `BackToAppView.vue`（A 级）；本页留着：经 `api/userAuth.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/account/DesktopSignIn.vue` | 容器 | 画面在 `DesktopSignInView.vue`（A 级）；本页留着：读路由 |
| `views/account/OAuthComplete.vue` | 容器 | 画面在 `OAuthCompleteView.vue`（A 级）；本页留着：经 `composables/useConsentDocuments.ts` 取数；经 `api/axiosHttp.ts` 取数；经 `api/userAuth.ts` 取数；读路由 |
| `views/account/OAuthError.vue` | 容器 | 画面在 `OAuthErrorView.vue`（A 级）；本页留着：经 `api/userAuth.ts` 取数；读路由 |
| `views/account/OAuthSuccess.vue` | 容器 | 画面在 `OAuthSuccessView.vue`（A 级）；本页留着：直接取数（`services/account.ts`）；读路由 |
| `views/account/OAuthVerify.vue` | 容器 | 画面在 `OAuthVerifyView.vue`（A 级）；本页留着：经 `api/userAuth.ts` 取数；读路由 |
| `views/account/PasskeyOffer.vue` | 容器 | 画面在 `PasskeyOfferView.vue`（A 级）；本页留着：经 `utils/sudo.ts` 取数；经 `views/account/passkeyEnrollment.ts` 取数；经 `api/users.ts` 取数；读路由 |
| `views/account/SignIn.vue` | 容器 | 画面在 `SignInView.vue`（A 级）；本页留着：经 `views/account/passkeyEnrollment.ts` 取数；经 `api/axiosHttp.ts` 取数；经 `api/userAuth.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/account/Verify2FA.vue` | 容器 | 画面在 `Verify2FAView.vue`（A 级）；本页留着：经 `views/account/passkeyEnrollment.ts` 取数；经 `api/http.ts` 取数；经 `api/userAuth.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/account/emailCode/Request.vue` | 容器 | 画面在 `RequestView.vue`（A 级）；本页留着：经 `api/axiosHttp.ts` 取数；经 `api/userAuth.ts` 取数；读路由 |
| `views/account/emailCode/Verify.vue` | 容器 | 画面在 `VerifyView.vue`（A 级）；本页留着：经 `views/account/passkeyEnrollment.ts` 取数；经 `api/axiosHttp.ts` 取数；经 `api/userAuth.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/account/recover/password/Start.vue` | 容器 | 画面在 `StartView.vue`（A 级）；本页留着：经 `api/axiosHttp.ts` 取数；经 `api/userAuth.ts` 取数 |
| `views/account/recover/password/Verify.vue` | 容器 | 画面在 `VerifyView.vue`（A 级）；本页留着：经 `api/axiosHttp.ts` 取数；经 `api/userAuth.ts` 取数；读路由 |
| `views/account/signup/Start.vue` | 容器 | 画面在 `StartView.vue`（A 级）；本页留着：经 `composables/useConsentDocuments.ts` 取数；经 `api/axiosHttp.ts` 取数；经 `api/userAuth.ts` 取数；读路由；读 store（signup） |
| `views/account/signup/VerifyEmail.vue` | 容器 | 画面在 `VerifyEmailView.vue`（A 级）；本页留着：经 `composables/useConsentDocuments.ts` 取数；经 `api/axiosHttp.ts` 取数；直接取数（`services/account.ts`）；读路由；读 store（signup） |
| `views/admin/AdminCreditsPage.vue` | 容器 | 画面在 `AdminCreditsPageView.vue`（A 级）；本页留着：经 `composables/useAdminCredits.ts` 取数 |
| `views/admin/AdminFeaturePage.vue` | 容器 | 画面在 `AdminFeaturePageView.vue`（A 级）；本页留着：经 `views/admin/features/registry.ts` 取数 |
| `views/admin/AdminFeatureStatsPage.vue` | D | 经 `views/admin/features/featureApi.ts` 取数；经 `views/admin/features/registry.ts` 取数；读路由；模板里画 `<RouterLink>` |
| `views/admin/AdminIntegrationsPage.vue` | 容器 | 画面在 `AdminIntegrationsPageView.vue`（A 级）；本页留着：经 `api/feishu.ts` 取数 |
| `views/admin/AdminLayout.vue` | D | 经 `composables/useAdminSections.ts` 取数；读路由；模板里画 `<RouterView>`；读 store（feedback） |
| `views/admin/AdminMachinesPage.vue` | 容器 | 画面在 `AdminMachinesPageView.vue`（A 级）；本页留着：经 `views/admin/machinesApi.ts` 取数 |
| `views/admin/AdminMembersPage.vue` | 容器 | 画面在 `AdminMembersPageView.vue`（A 级）；本页留着：直接取数（`api.ts`） |
| `views/admin/AdminModelsPage.vue` | C | 经 `composables/useAdminModels.ts` 取数；经 `components/admin/AdminModelDetailDrawer.vue` 取数 |
| `views/admin/AdminQueuePage.vue` | 容器 | 画面在 `AdminQueuePageView.vue`（A 级）；本页留着：经 `composables/useAdminQueue.ts` 取数 |
| `views/admin/AdminRunRecordsPage.vue` | 容器 | 画面在 `AdminRunRecordsPageView.vue`（A 级）；本页留着：经 `views/admin/runRecordsApi.ts` 取数 |
| `views/admin/AdminSpacesPage.vue` | 容器 | 画面在 `AdminSpacesPageView.vue`（A 级）；本页留着：经 `api/spaces.ts` 取数 |
| `views/admin/AdminStatsPage.vue` | 容器 | 画面在 `AdminStatsPageView.vue`（A 级）；本页留着：经 `composables/useAdminStats.ts` 取数；读路由 |
| `views/feedback/FeedbackCenterPage.vue` | 容器 | 画面在 `FeedbackCenterPageView.vue`（A 级）；本页留着：读 store（feedback） |
| `views/feedback/FeedbackDetailPage.vue` | 容器 | 画面在 `FeedbackDetailPageView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/feedbackClaim.ts` 取数；读路由；读 store（feedback） |
| `views/feedback/FeedbackMinePage.vue` | 容器 | 画面在 `FeedbackMinePageView.vue`（A 级）；本页留着：读路由；读 store（feedback） |
| `views/feedback/FeedbackSubmitPage.vue` | D | 经 `components/feedback/SubmitFeedbackForm.vue` 取数；读路由；读 store（feedback） |
| `views/home/Download.vue` | C | 经 `lib/desktop.ts` 取数；经 `lib/desktopChangelog.ts` 取数 |
| `views/home/HomeHub.vue` | 容器 | 画面在 `HomeHubView.vue`（A 级）；本页留着：经 `composables/useTeamNav.ts` 取数；读路由 |
| `views/home/HomeSidebar.vue` | 容器 | 画面在 `HomeSidebarView.vue`（A 级）；本页留着：经 `composables/useTeamNav.ts` 取数 |
| `views/home/Landing.vue` | A | 只吃 props 和事件 |
| `views/home/Solutions.vue` | A | 只吃 props 和事件 |
| `views/legal/LegalDocument.vue` | 容器 | 画面在 `LegalDocumentView.vue`（A 级）；本页留着：经 `api/legal.ts` 取数；读路由 |
| `views/question/Ask.vue` | 容器 | 画面在 `AskView.vue`（A 级）；本页留着：经 `composables/useTopicSearch.ts` 取数；经 `api/questions.ts` 取数；读路由 |
| `views/question/Detail.vue` | 容器 | 画面在 `DetailView.vue`（A 级）；本页留着：经 `composables/useChosenAvatar.ts` 取数；经 `composables/useQuestionInvitations.ts` 取数；经 `api/answers.ts` 取数；经 `api/questions.ts` 取数；读路由；模板里画 `<router-view>` |
| `views/question/DetailAnswer.vue` | 容器 | 画面在 `DetailAnswerView.vue`（A 级）；本页留着：经 `composables/useAnswerActions.ts` 取数；经 `composables/useChosenAvatar.ts` 取数；经 `api/answers.ts` 取数；读路由 |
| `views/question/DetailAnswerList.vue` | 容器 | 画面在 `DetailAnswerListView.vue`（A 级）；本页留着：经 `composables/useAnswerActions.ts` 取数；经 `composables/useAnswerPaging.ts` 取数；经 `composables/useChosenAvatar.ts` 取数；`provide()` / `inject()` |
| `views/spaces/Detail.vue` | D | 经 `composables/useSpaceData.ts` 取数；经 `components/common/ErrorBoundary.vue` 取数；读路由；模板里画 `<router-view>`；读 store（space） |
| `views/spaces/Index.vue` | 容器 | 画面在 `IndexView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/avatars.ts` 取数；经 `api/spaces.ts` 取数；经 `commands/copy.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/spaces/detail/Announcements.vue` | 容器 | 画面在 `AnnouncementsView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；经 `views/spaces/detail/useSpaceAnnouncements.ts` 取数；经 `api/spaces.ts` 取数；读路由；读 store（space） |
| `views/spaces/detail/AuditTask.vue` | 容器 | 画面在 `AuditTaskView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；经 `api/taskBoard.ts` 取数；读 store（space） |
| `views/spaces/detail/InviteCodes.vue` | 容器 | 画面在 `InviteCodesView.vue`（A 级）；本页留着：经 `api/spaces.ts` 取数；读路由 |
| `views/spaces/detail/ManageCategories.vue` | 容器 | 画面在 `ManageCategoriesView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；读 store（space） |
| `views/spaces/detail/ManageDenied.vue` | 容器 | 画面在 `ManageDeniedView.vue`（A 级）；本页留着：读路由 |
| `views/spaces/detail/ManageDomainGroups.vue` | 容器 | 画面在 `ManageDomainGroupsView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；经 `api/spaces.ts` 取数；读路由；读 store（space） |
| `views/spaces/detail/ManageTemplates.vue` | 容器 | 画面在 `ManageTemplatesView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；读路由；读 store（space） |
| `views/spaces/detail/ManageTopics.vue` | 容器 | 画面在 `ManageTopicsView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；读 store（space） |
| `views/spaces/detail/Members.vue` | 容器 | 画面在 `MembersView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；经 `api/spaces.ts` 取数；读路由；读 store（space） |
| `views/spaces/detail/PublishTask.vue` | 容器 | 画面在 `PublishTaskView.vue`（A 级）；本页留着：经 `composables/useAttachmentUploads.ts` 取数；经 `composables/useSpaceData.ts` 取数；经 `composables/useSpaceMaterials.ts` 取数；经 `api/attachments.ts` 取数；经 `api/taskBoard.ts` 取数；直接取数（`services/ErrorHandler.ts`）；读路由；读 store（space） |
| `views/spaces/detail/Tasks.vue` | 容器 | 画面在 `TasksView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；经 `views/spaces/detail/useSpaceAnnouncements.ts` 取数；经 `api/spaces.ts` 取数；经 `api/taskBoard.ts` 取数；读路由；读 store（space） |
| `views/spaces/detail/TemplateForm.vue` | 容器 | 画面在 `TemplateFormView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；读路由；读 store（space） |
| `views/spaces/detail/analytics/Alerts.vue` | 容器 | 画面在 `AlertsView.vue`（A 级）；本页留着：经 `api/spaces.ts` 取数 |
| `views/spaces/detail/analytics/Index.vue` | 容器 | 画面在 `IndexView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；模板里画 `<router-view>`；读 store（space） |
| `views/spaces/detail/analytics/Learning.vue` | 容器 | 画面在 `LearningView.vue`（A 级）；本页留着：经 `api/spaces.ts` 取数 |
| `views/spaces/detail/analytics/Overview.vue` | 容器 | 画面在 `OverviewView.vue`（A 级）；本页留着：经 `composables/useAnalyticsPublishers.ts` 取数；经 `api/spaces.ts` 取数 |
| `views/spaces/detail/analytics/Participants.vue` | 容器 | 画面在 `ParticipantsView.vue`（A 级）；本页留着：经 `composables/useAnalyticsPublishers.ts` 取数；经 `views/spaces/detail/analytics/composables/useAnalyticsExport.ts` 取数；经 `api/spaces.ts` 取数 |
| `views/spaces/detail/analytics/Publishers.vue` | 容器 | 画面在 `PublishersView.vue`（A 级）；本页留着：经 `views/spaces/detail/analytics/composables/useAnalyticsExport.ts` 取数；经 `api/spaces.ts` 取数 |
| `views/spaces/detail/analytics/Tasks.vue` | 容器 | 画面在 `TasksView.vue`（A 级）；本页留着：经 `composables/useAnalyticsPublishers.ts` 取数；经 `views/spaces/detail/analytics/composables/useAnalyticsExport.ts` 取数；经 `api/spaces.ts` 取数 |
| `views/spaces/detail/settings/BasicInfo.vue` | 容器 | 画面在 `BasicInfoView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；经 `api/avatars.ts` 取数；经 `api/spaces.ts` 取数；读路由；读 store（space） |
| `views/spaces/detail/settings/Index.vue` | 容器 | 画面在 `IndexView.vue`（A 级）；本页留着：读路由；模板里画 `<router-view>`；读 store（space） |
| `views/spaces/detail/settings/SpaceGuidance.vue` | 容器 | 画面在 `SpaceGuidanceView.vue`（A 级）；本页留着：经 `composables/useSpaceData.ts` 取数；经 `composables/useSpaceMaterials.ts` 取数；读 store（space） |
| `views/spaces/detail/settings/SpaceMaterials.vue` | 容器 | 画面在 `SpaceMaterialsView.vue`（A 级）；本页留着：经 `api/spaces.ts` 取数；读路由 |
| `views/tasks/Detail.vue` | D | 经 `views/tasks/components/TaskSide.vue` 取数；经 `api/taskBoard.ts` 取数；经 `views/tasks/components/index.ts` 取数；经 `views/tasks/composables/index.ts` 取数；经 `views/tasks/composables/useAssistant.ts` 取数；经 `components/tasks/TaskSubmissionHistory.vue` 取数；读路由；模板里画 `<router-link>` |
| `views/tasks/Edit.vue` | D | 经 `composables/useAttachmentUploads.ts` 取数；经 `composables/useSpaceData.ts` 取数；经 `composables/useSpaceMaterials.ts` 取数；经 `views/tasks/composables/index.ts` 取数；经 `api/taskBoard.ts` 取数；直接取数（`services/ErrorHandler.ts`）；读路由；模板里画 `<router-link>`；读 store（space） |
| `views/tasks/detail/Brief.vue` | 容器 | 画面在 `BriefView.vue`（A 级）；本页留着：经 `views/tasks/composables/index.ts` 取数 |
| `views/tasks/detail/Insights.vue` | 容器 | 画面在 `InsightsView.vue`（A 级）；本页留着：经 `api/spaces.ts` 取数；经 `api/taskBoard.ts` 取数；读路由 |
| `views/tasks/detail/Roster.vue` | 容器 | 画面在 `RosterView.vue`（A 级）；本页留着：经 `api/spaces.ts` 取数；经 `api/taskBoard.ts` 取数；读路由 |
| `views/tasks/detail/Submissions.vue` | C | 直接取数（`services/account.ts`）；经 `components/tasks/TaskSubmissionHistory.vue` 取数 |
| `views/tasks/detail/Submit.vue` | D | 经 `api/attachments.ts` 取数；经 `api/taskBoard.ts` 取数；读路由 |
| `views/teams/Detail.vue` | 容器 | 画面在 `DetailView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/teams.ts` 取数；读路由；模板里画 `<router-view>` |
| `views/teams/Explore.vue` | 容器 | 画面在 `ExploreView.vue`（A 级）；本页留着：经 `api/teams.ts` 取数 |
| `views/teams/Index.vue` | 容器 | 画面在 `IndexView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/avatars.ts` 取数；经 `api/teams.ts` 取数；读路由；模板里画 `<router-view>` |
| `views/teams/Mine.vue` | 容器 | 画面在 `MineView.vue`（A 级）；本页留着：经 `api/teams.ts` 取数 |
| `views/teams/Pending.vue` | 容器 | 画面在 `PendingView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/teams.ts` 取数 |
| `views/teams/detail/Compute.vue` | 容器 | 画面在 `ComputeView.vue`（A 级）；本页留着：直接取数（`api.ts`） |
| `views/teams/detail/Credits.vue` | 容器 | 画面在 `CreditsView.vue`（A 级）；本页留着：经 `composables/useCreditUsage.ts` 取数；经 `api/creditUsage.ts` 取数 |
| `views/teams/detail/Knowledge.vue` | 容器 | 画面在 `KnowledgeView.vue`（A 级）；本页留着：经 `composables/useTeamKnowledge.ts` 取数 |
| `views/teams/detail/Members.vue` | 容器 | 画面在 `MembersView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/teams.ts` 取数；经 `composables/useAccountLookup.ts` 取数；直接取数（`services/account.ts`）；直接取数（`services/ErrorHandler.ts`）；读路由 |
| `views/teams/detail/TeamProjects.vue` | 容器 | 画面在 `TeamProjectsView.vue`（A 级）；本页留着：经 `composables/useProjectMenu.ts` 取数；直接取数（`api.ts`）；读路由 |
| `views/user/settings/General.vue` | A | 只吃 props 和事件 |
| `views/user/settings/ModelConnections.vue` | 容器 | 画面在 `ModelConnectionsView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `api/modelConnections.ts` 取数；经 `api/ownAgents.ts` 取数 |
| `views/user/settings/Notifications.vue` | 容器 | 画面在 `NotificationsView.vue`（A 级）；本页留着：经 `api/notificationPreferences.ts` 取数；直接取数（`services/webPush.ts`） |
| `views/user/settings/Profile.vue` | 容器 | 画面在 `ProfileView.vue`（A 级）；本页留着：经 `composables/useChosenAvatar.ts` 取数；经 `api/avatars.ts` 取数；经 `api/axiosHttp.ts` 取数；经 `api/users.ts` 取数；直接取数（`services/account.ts`） |
| `views/user/settings/RealName.vue` | 容器 | 画面在 `RealNameView.vue`（A 级）；本页留着：经 `utils/sudo.ts` 取数；经 `composables/useChosenAvatar.ts` 取数；经 `api/axiosHttp.ts` 取数；经 `api/users.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/user/settings/Security.vue` | 容器 | 画面在 `SecurityView.vue`（A 级）；本页留着：经 `utils/sudo.ts` 取数；直接取数（`api.ts`）；经 `api/axiosHttp.ts` 取数；经 `api/users.ts` 取数；直接取数（`services/account.ts`） |
| `views/user/settings/ThisDevice.vue` | 容器 | 画面在 `ThisDeviceView.vue`（A 级）；本页留着：经 `composables/useThisDevice.ts` 取数；经 `lib/desktop.ts` 取数 |
| `views/user/settings/Usage.vue` | 容器 | 画面在 `UsageView.vue`（A 级）；本页留着：经 `composables/useCreditUsage.ts` 取数；经 `api/creditUsage.ts` 取数 |
| `views/welcome/Welcome.vue` | 容器 | 画面在 `WelcomeView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `lib/desktop.ts` 取数；经 `query/projects.ts` 取数；直接取数（`services/account.ts`）；读路由 |
| `views/workspace/ChannelBrowse.vue` | 容器 | 画面在 `ChannelBrowseView.vue`（A 级）；本页留着：经 `api/channels.ts` 取数；读路由；读 store（workspace） |
| `views/workspace/DmView.vue` | D | 直接取数（`api.ts`）；经 `components/ChatPanel.vue` 取数；经 `query/project.ts` 取数；读路由；读 store（workspace） |
| `views/workspace/ProjectMembersView.vue` | D | 经 `composables/useAccountLookup.ts` 取数；直接取数（`api.ts`）；经 `components/LeaveProjectDialog.vue` 取数；经 `components/TransferProjectDialog.vue` 取数；经 `query/project.ts` 取数；读路由；模板里画 `<router-link>`；读 store（workspace） |
| `views/workspace/ProjectOverview.vue` | 容器 | 画面在 `ProjectOverviewView.vue`（A 级）；本页留着：直接取数（`api.ts`）；经 `lib/routePrefetch.ts` 取数；经 `query/project.ts` 取数；经 `query/tasks.ts` 取数；读路由；读 store（workspace） |
| `views/workspace/ProjectShell.vue` | D | 经 `lib/addresses.ts` 取数；经 `lib/routePrefetch.ts` 取数；经 `query/project.ts` 取数；经 `query/projectFeed.ts` 取数；经 `views/workspace/ProjectAccessNotice.vue` 取数；读路由；模板里画 `<router-view>`；读 store（workspace） |
| `views/workspace/ProjectSidebar.vue` | D | 经 `components/TopicSidebar.vue` 取数；经 `lib/addresses.ts` 取数；经 `lib/routePrefetch.ts` 取数；经 `query/tasks.ts` 取数；读路由；读 store（workspace） |
| `views/workspace/ProjectTasks.vue` | 容器 | 画面在 `ProjectTasksView.vue`（A 级）；本页留着：经 `commands/topicActions.ts` 取数；经 `query/tasks.ts` 取数；读路由；读 store（workspace） |
| `views/workspace/TopicView.vue` | D | 经 `composables/useAcceptCard.ts` 取数；经 `composables/useChannelThreads.ts` 取数；经 `composables/useTopicPanel.ts` 取数；经 `api/tasks.ts` 取数；经 `api/threads.ts` 取数；经 `components/PushPermissionPrompt.vue` 取数；经 `components/task/TaskOverview.vue` 取数；经 `components/TopicAcceptCard.vue` 取数；经 `components/TopicHeader.vue` 取数；经 `components/WorkPanel.vue` 取数；经 `lib/routePrefetch.ts` 取数；经 `query/project.ts` 取数；经 `query/room.ts` 取数；经 `views/workspace/TopicChatColumn.vue` 取数；经 `views/workspace/useChannelOverview.ts` 取数；经 `views/workspace/useTaskOverview.ts` 取数；经 `views/workspace/useTaskPage.ts` 取数；经 `views/workspace/useTopicPanes.ts` 取数；经 `views/workspace/ThreadPane.vue` 取数；读路由；读 store（workspace） |
| `views/workspace/WorkspaceEntry.vue` | D | 经 `views/workspace/ProjectSidebar.vue` 取数；读路由；读 store（workspace） |

## 工作面板 {#panels}

`src/components/panels/` 下 62 个 SFC，棘轮把每一个都当场景，**现在全部是 A 档、都冻结在 `ready` 里**。取数一条都不在这一层：

- 取数在 `composables/usePanelDoc.ts`、`usePanelChanges.ts`、`usePanelPreview.ts` 这类组合式函数里，由 `components/work/` 下的 Host 组件（`PanelDocHost`、`PanelChangesHost`、`PanelPreviewHost`、`PanelSiteHost`）调，结果按 props 往下递。`PanelDoc` / `PanelChanges` / `PanelPreview` 三个外壳因此从 C 变成了 A（分家的经过写在 `PanelChanges.vue` 的文件头注释里）。
- Host 自己不是场景——`components/work/` 既不是路由页、也不在 `panels/` 下，棘轮不判它；挂它的多半也不是场景（`components/WorkPanel.vue`），这笔取数就常常不落在任何场景头上。例外是 `views/ProjectDocsView.vue`：它自己挂 `PanelDocHost`，理由里就有这一条。

2026-09-30 那一版这里是一张 21 行的面板表，当时 9 个面板卡在 C、`PanelCard.vue` 还在；现在 62 个全是 A，逐行表没有留着的必要了。方向没变：**把 C 留在取数那一层，把 A 一层层攒出来**。

## 卡在哪一步 {#blockers}

125 个页面里，每条原因各占多少页（一页可以算多笔，2026-10-14 的数）：

| 原因 | 页数 | 分成 |
|---|---|---|
| 直接 import 取数模块（`@/api`、`@/network/*`、`@/services/*`） | 44 | |
| 读路由（`useRoute` / `$route`） | 75 | |
| 读 store | 39 | `workspace` 16、`space` 16、`feedback` 5、`signup` 2 |
| 经中间模块绕到取数 | 110 | `api/spaces.ts` 17、`composables/useSpaceData.ts` 15、`api/userAuth.ts` 13、`api/axiosHttp.ts` 11、`query/project.ts` 8、`api/taskBoard.ts` 8、`api/teams.ts` 7、`composables/useChosenAvatar.ts` 6、`api/users.ts` 5、`lib/desktop.ts` / `views/account/passkeyEnrollment.ts` / `api/avatars.ts` / `lib/routePrefetch.ts` 各 4、其余 121 个各 ≤3 |
| 模板里画 `<router-view>` / `<router-link>` | 13 | |
| `provide()` / `inject()`、`$parent` / `$root`、事件总线 | 1 | `views/question/DetailAnswerList.vue` 一页，这次拆页时从 `components/questions/AnswerList.vue` 收上来的 |

修法按前两条分：

- **直接取数 / 经中间模块绕到取数（44 / 110 页）** 是「把这一段挪出去」的问题，机械，但要一页一页做。页面走容器配方——画面交给同目录 `<页面名>View.vue`，取数留在页面；面板走「取数进 composable、由挂它的 Host 调」。中间模块那 110 页里，最大的一条链是空间数据层（`api/spaces.ts`、`composables/useSpaceData.ts`）。
- **读路由（75 页）** 是「这个页面怎么知道自己是谁」的问题。`views/account/` 那 17 页 2026-10-05 已经按「参数当 props 进视图」拆完。剩下最集中的一处是 `views/workspace/`：10 页里 6 页 D，`TopicView.vue` 一页就有 21 条理由；再往后是顶层 5 页、`views/feedback/` 3 页。
- **13 页的 D 是因为模板里画了 `<router-view>` / `<router-link>`**（这一条 2026-10-08 起算 D），它们多半自己就是承重点：`views/workspace/ProjectShell.vue`、`views/spaces/Detail.vue`、`views/teams/Index.vue`、`views/admin/AdminLayout.vue`。动它们等于动那棵路由树。（13 是「档位判成 D」的页数，含 5 个容器页；容器不算欠债，所以还挂在欠债表上的只有 8 页。判据认标签本身、大小写都算 —— `views/admin/AdminLayout.vue` 写的是 `<RouterView>`。这一批拆的两页仍在这 13 里，只是从欠债换成了容器。）

## 这份清单怎么来的 {#how}

数字不是手数的：等级和理由来自 `.claude/scripts/frontend_grade.py`，看板和棘轮读的是同一份判据；页面清单取自 `src/router/` 的 import 图（`scene-ratchet.py --list` 能把两者一起打出来——这一页的表就是它生成的）。看板上的 A/B/C/D 是**全部 `.vue`** 的口径（数量见[架构指标](/dev/arch-metrics#metrics)），这里只切出「场景」这一层（路由页 + 面板），所以和看板对不上是正常的。

三个已知偏差写在这里，免得被当成事实用：

- **`import type` 那条边已经修掉（2026-09-30）。** 规约说 `import type` 不算（构建时就被抹掉，见[架构指标](/dev/arch-metrics#metrics)）；但 `lib/previewSession.ts` 整个文件只有一句 `import type { PreviewSession } from '../api'`，它到 `api.ts` 的那条边一度仍然进了传递闭包，于是 `PanelPreviewView` 被判成 C——它自己一行取数代码都没有。现在这条边不进图了。但同一次里还查出一处**过宽**：传递依赖只跟 `.ts`，`.vue` 子组件那条链被漏掉，于是「自己干净、子组件取数」的十页被判成 A 并冻进基线。两处都在 2026-09-30 修掉，基线和这一页的数字按修完的结果重算（能单独跑 20 → 10）。
- **`export … from` 那条边已经修掉（2026-10-08）。** 依赖原来只认字面量 `import`，经 index barrel 再导出的文件就少一条边。实例是 `views/tasks/detail/Brief.vue`：它经 `views/tasks/composables/index.ts` 够到 `useTaskAttachments.ts` 里的 `@/api`，却被判成 A、冻在 `ready` 里。现在 `export { … } from` / `export * from` 和 `import` 一样算一条边（`export type` 不算），它落进 container，冻结转到同目录的 `BriefView.vue`。同一版里还修了模板那半边：画 `<router-view>` / `<router-link>` 算 D，注释里提到 `vue-router` 不算依赖。
- **正则不是编译器。** 解析不出来的模块说明符当成外部依赖，不当成一条边，所以真隔着一条解析不出来的链在取数的组件会被判高一档（A 或 B）。

## 下一步 {#next}

1. **已经拆出来的三个视图挂上目录**：`PanelChangesView`、`PanelPreviewView`、`PanelDocView`，每个带 loading / 空 / 有数据 / 出错几种状态。上一版已经做了。
2. **A 档的先补目录**，成本几乎为零：还没挂的 A 级组件（不止场景，所有组件）在[组件总览](/dev/ref-components)里标作「能收未收」。挂上去之后，改外观和改排版就有地方看效果。
3. **C 档按「外壳 / 内容」拆**：取数留在外层 composable，视图只收 props，一次一个页签；拆出来跑一遍 `pnpm run lint:scenes` 就行，不必登记。
4. **D 档要单独排**，不是一页一页能拆完的：读路由那一批要先定「参数从哪进来」。`views/account/` 已按「参数当 props 进视图」拆完（2026-10-05），`views/workspace/`、`views/spaces/` 两块各还要一个方案，动哪块由产品定。
5. **剩下的就是搬欠债。** 能单独跑的那 164 个只增不减，23 个跑不起来的只减不增（[规则](#ratchet)之后没有别的口子），所以这条曲线只有一个方向：23 → 0。
