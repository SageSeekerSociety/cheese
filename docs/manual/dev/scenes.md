---
title: 场景清单
kind: 参考
summary: 前端每一个「场景」——路由页和工作面板——今天能不能脱离后端、路由和 store 单独渲染，一张表列清哪些已经可以、哪些卡在哪一步、一共多少个，以及这条线从 2026-09-30 起由哪条检查看住。
covers:
  - .claude/scripts/frontend_grade.py
  - .claude/scripts/scene-ratchet.py
  - frontend/scene-baseline.json
  - frontend/src/components/panels/
  - frontend/src/views/demo/catalog.ts
  - frontend/src/views/demo/catalogFixtures.ts
---

# 场景清单 {#scenes}

组件预览站（`/demo/catalog`）能做什么，取决于**谁被注册进去了**；而这个集合是攒出来的，没人知道还剩多少、剩下的差在哪。这一页把它翻过来数一遍：前端一共有多少个能叫「场景」的东西，几个今天就能脱离后端单独跑一遍，剩下的各卡在哪一步。

> 讲：每个路由页和工作面板今天站在哪一档、卡住的原因是什么、一共多少个、下一步先动哪一批，以及**从今天起这条线怎么被机器看住**。不讲：怎么往目录里加一个组件（三步写在仓库内的 `frontend/AGENTS.md`），A/B/C/D 的判据和怎么跑（见[架构指标](/dev/arch-metrics#metrics)），组件边界那三道闸本身（见[前端结构](/dev/frontend#gates)）。

## 「单独渲染」是什么意思 {#standalone}

**给一组 props，它就能出画面**：不要后端（不 import `@/api`、`@/network`、`@/services/*`，也不经别的模块绕过去），不要路由（不读 `useRoute` / `$route`），不要任何会自己去取数的 store。判据就是[架构指标](/dev/arch-metrics#metrics)那套 A/B/C/D：

| 档 | 什么情况 | 要补什么才能单独跑 |
|---|---|---|
| **A** | 只吃 props 和事件 | 不用补，只差一份形状数据 |
| **B** | 只读应用外壳的 store（`usePageTitleStore`、`useNavigationStore`） | 在外壳里装一个空的同名 store，或者把那两个读点改成 props |
| **C** | 自己取数——直接 import、经中间模块绕、自己写 `fetch`/`axios`——或者读业务 store | 把取数那一段挪出去：外面拿数据，它只收结果。已合的样板：#2193 的 `views/spaces/detail/settings/BasicInfo.vue` 取数、读 store、保存、删除后跳转，同目录 `BasicInfoView.vue` 只收 props 和事件，A 级、已冻结在 ready |
| **D** | 还绑在挂载位置上：读路由、`$parent` / `$root`、事件总线、`provide` / `inject` | 先把「从哪来」改成 props 或事件，再谈数据 |

判据只有一份，写在 `.claude/scripts/frontend_grade.py` 里：看板（`arch-metrics.py`）和下面那条闸门读的是同一个函数，所以不会出现「看板说是 A、闸门说不是」。**A 档在这一页就叫「能单独跑」**，[基线](#ratchet)冻的就是它。

## 一共多少 {#totals}

2026-09-30 在 `feat/scene-ratchet`（rebase 到当时的 `main` 之后）上数出来的：`main` 上的 #2152 / #2154 删掉了 24 个旧场景（旧 `courses`、`board/pages/*`、`discussions` 等，功能下线，不是改名），基线里因此不再有它们：**111 个路由页 + 27 个工作面板 SFC**，其中 **10 个（7%）今天就能单独渲染**；目录里现在有 20 项，其中 4 项是工作面板（`panel-tabs` 加上三个拆出来的视图），剩下 16 项是更小的零件（卡片、导航项、图表……），**一个路由页都没有**。

| 场景 | 一共 | A 今天就能单独跑 | C 卡在取数 | D 卡在路由 / `$parent` / `inject` |
|---|---|---|---|---|
| 路由页（`src/views/`，router 表里挂上去的每一页） | 110 | 2 | 36 | 72 |
| 工作面板（`src/components/panels/` 下的 SFC） | 27 | 8 | 19 | 0 |

和上一版（2026-09-30 早先的 20 个）差十个，原因是判据修了一处**过宽**：传递依赖原来只跟 `.ts` 边，父页面 import 一个自己去取数的子组件（`.vue`）不算「到了接口层」，于是「自己干净、子组件在取数」的十个场景被当成能单独跑。`.` 这条边现在也跟（[原因](#how)），数字按修完的结果写在这里。修之前报出去的 20 是错的，不是这一版退步。

一个场景可以同时踩中好几条（100 页直接 import 了取数模块，87 页读路由），**档位取最差的那条**——所以 D 那一列不是「只差一个路由」。

按目录看那 130 个页面：

| 目录 | 一共 | A | C | D |
|---|---|---|---|---|
| `views（顶层）` | 19 | 1 | 6 | 12 |
| `views/account/` | 17 | 0 | 1 | 16 |
| `views/admin/` | 9 | 0 | 4 | 5 |
| `views/feedback/` | 5 | 0 | 2 | 3 |
| `views/home/` | 5 | 0 | 4 | 1 |
| `views/legal/` | 1 | 0 | 0 | 1 |
| `views/question/` | 4 | 0 | 1 | 3 |
| `views/spaces/` | 23 | 0 | 9 | 14 |
| `views/tasks/` | 6 | 0 | 2 | 4 |
| `views/teams/` | 9 | 0 | 3 | 6 |
| `views/user/` | 5 | 1 | 4 | 0 |
| `views/workspace/` | 7 | 0 | 0 | 7 |

今天就能单独跑的那 2 页：`views/404.vue`、`views/user/settings/General.vue`。

2026-10-05，account 线（登录、注册、找回密码、OAuth 回调、实名/安全/资料）21 页拆完：每页当容器，画面进同目录的 `<页面名>View.vue`，视图只吃 props 和事件。`--update` 之后基线是 **82 个 ready、97 个 debt**（此前 debt 118），这 21 页全部离开欠债表。上面两张表还是 2026-09-30 的口径；「页面」一表里对应的行已改成「容器」并写出画面在哪，目录一表里 `views/account/` 17 页现在全是容器。

2026-10-06，其余线（后台 9、反馈 5、首页 5、问答 4、作业 4、团队 9，共 36 页）拆完：同样是每页当容器、画面进同目录的 `<页面名>View.vue`。`--update` 之后基线是 **120 个 ready、62 个 debt**（此前 101 / 81）。「页面」一表里对应的行已改成「容器」并写出画面在哪。当天补完最后一条 `views/feedback/FeedbackSubmitPage.vue`：它当时交不出去，卡在共用的表单组件 `SubmitFeedbackForm` 自己读 feedback store。拆法是把那个组件也拆成「画面（`SubmitFeedbackFormView.vue`，只吃 props 和事件）+ 接线（`useSubmitFeedbackForm.ts`，页面和对话框两份容器共用）」，这一页于是变成容器，这一线 36 页齐（并入主干两次之后重跑 `--update`，基线是 **137 个 ready、45 个 debt**，其中涨的那部分来自主干上别的线）。

## 从今天起它是一条闸门 {#ratchet}

2026-09-30 起，「能单独跑」不只是一张表上的状态：`pnpm run lint:scenes` 会逐场景重新判一遍，该红的红、该退 2 的退 2，commit 和 CI 都跑（`--self-test` 在 `repo-guards.yml` 里）。产品定的规则是**只拦新增**：今天能单独跑的冻结下来只许多，今天跑不起来的允许继续躺着，**从今往后新加的每一个场景必须第一天就能单独跑**。

冻在哪：`frontend/scene-baseline.json`，两份名单。

| 名单 | 是什么 | 只能 |
|---|---|---|
| `ready` | 今天就能单独跑的场景（10 个） | 增（掉档就红） |
| `debt` | 仓库里本来就跑不起来的场景（128 个） | 减（变 A 会提示你收紧） |

不在两份名单里的场景就是**新的**——这也是为什么债务必须一条条列出来，而不是记一个数字：没有这张表，「新」和「旧」没法分。六种结果：

| 情况 | 结果 |
|---|---|
| `ready` 里的场景掉档（A 变成 B/C/D） | 红（退出 1），它拦的就是这个 |
| 新场景（新挂的路由页、新加的面板）不是 A | 红（退出 1） |
| 页面不是 A，但它渲染同目录的 `<页面名>View.vue`，而那个视图是 A | 过：页面算这个视图的容器，冻结的是视图（见下） |
| 老场景本来就不是 A | 过（它在 `debt` 里） |
| `debt` 里的场景变 A 了 | 过，并提示你 `--update` 收紧基线 |
| 判断不了（没有 `frontend/src/`、基线读不出来、场景文件读不了） | 退出 2，永远不算过 |

场景从哪来：**页面**是 router 的 import 图能走到的每个 `views/*.vue`（新挂一页就自动进集合，不用改这里），**面板**是 `components/panels/` 目录下的每个 SFC，**视图**是页面 import 的那个同目录 `<页面名>View.vue`。所以「新场景」不需要谁记得去登记。

**新页面可以是容器。** 路由落到页面，读地址、取数、保存本来就是页面的活；一刀切地不许页面做这些，新页面的数据就没有地方放了。所以页面可以留着这些活，前提是把画面交给一个视图：同目录的 `<页面名>View.vue`，由页面用值 import 进来（`import type` 不算）**并且模板真的渲染它**——只 import 不渲染不算容器。渲染用的是静态判断：HTML 注释里的标签不算渲染，属性值里的 `</template>` 不会截断扫描，`<component :is>` 判不了、直接不当容器。除了视图本身，模板渲染的其他组件也都要么是包里的、Vuetify（按仓库 vuetify 的自动导入表认名字，不是按 V 前缀）、要么是能核实为 A 的自家组件，不然取数只是往下挪了一层；小写标签只有真是 HTML/SVG 元素才算原生，否则按组件核（`<child />` 会去找 `child`/`Child` 的 import）；本地绑定的名字优先于内置名——import 的照常评级，`const RouterView = …` 这类声明让名字无从核实，核实不了就不豁免。视图单独算一个场景，和别的场景一样评级、冻结，新的必须第一天就是 A；页面按容器处理，不进两份名单，每次检查都重新看它的视图。`--list` 里容器那一行会写「container of …」。容器规则只给页面：面板没有路由，穿同样衣服的面板就是一个会取数的面板，照常评级。

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

改完 `pnpm run lint:scenes`；它判成 A 了，会提示你跑 `--update` 把这一条从 `debt` 搬进 `ready`（搬进去就再也不许掉出来了）。

### 基线怎么更新 {#update-baseline}

三条命令，读的那条是 CI 跑的，写的那条只加不减。

```bash
pnpm run lint:scenes                                # 检查（CI 跑的就是它）
pnpm run lint:scenes:update                         # 收紧基线：往 ready 加、从 debt 减
python3 .claude/scripts/scene-ratchet.py --list     # 每个场景的档和理由，用来重算这一页的表
```

`--update` 有两种情况会**拒绝执行**（退出 1）：有场景掉了档、有新场景不是 A。它拒绝时一个字节都不写——这不是它的脾气，是它唯一值钱的地方：能靠改基线过关的闸门等于没有。掉了档的场景只能改回去（或走 review 改规则），做不到的场景只能改到能单独跑。

### 目录那条提醒 {#catalog-warning}

能单独跑、但没挂进 `/demo/catalog` 的场景，检查会打一行 warning（今天 9 个：2 个页面 + 7 个面板），**不算失败**：目录里放什么由人定，页面和面板本来也不是一回事。挂上去之后，`pnpm exec vitest run src/views/demo/catalog.spec.ts` 才是「它真的能单独挂起来」的那条机械结论——加一个组件到目录的三步写在 `frontend/AGENTS.md`。

## 页面 {#pages}

`src/views/` 下被 `src/router/` 挂上去的每一页。「卡在哪」列的是最差的那一条，同一页可能还踩了别的。

| 页面 | 档 | 卡在哪 |
|---|---|---|
| `views/404.vue` | A | 只吃 props 和事件 |
| `views/ConnectView.vue` | D | 读路由；直接取数（`api.ts`） |
| `views/InboxView.vue` | C | 直接取数（`api.ts`） |
| `views/MarketView.vue` | C | 直接取数（`api.ts`） |
| `views/MyArchivedProjectsView.vue` | C | 直接取数（`api.ts`）；读 store（workspace） |
| `views/MyConnectionsView.vue` | D | 读路由；直接取数（`api.ts`）；经 `api/feishu.ts` 取数 |
| `views/MyDevicesView.vue` | C | 直接取数（`api.ts`）；直接取数（`services/account.ts`）；经 `lib/desktop.ts` 取数 |
| `views/PreviewOpenView.vue` | D | 读路由；直接取数（`api.ts`） |
| `views/ProfileView.vue` | C | 直接取数（`api.ts`）；经 `composables/useChosenAvatar.ts` 取数 |
| `views/ProjectArtifactView.vue` | D | 读路由；直接取数（`api.ts`） |
| `views/ProjectDocsView.vue` | D | 读路由；直接取数（`api.ts`） |
| `views/ProjectLibraryView.vue` | D | 读路由；直接取数（`api.ts`）；经 `lib/libraryApi.ts` 取数 |
| `views/ProjectRoutinesView.vue` | D | 读路由；直接取数（`api.ts`） |
| `views/ProjectSearchView.vue` | D | 读路由；直接取数（`api.ts`）；经 `views/workspace/search/docs.palette.ts` 取数；经 `views/workspace/search/library.palette.ts` 取数；经 `views/workspace/search/messages.palette.ts` 取数；经 `views/workspace/search/projectDocs.palette.ts` 取数；经 `views/workspace/search/tasks.palette.ts` 取数 |
| `views/ProjectSettingsView.vue` | D | 读路由；直接取数（`api.ts`）；经 `utils/sudo.ts` 取数；读 store（workspace） |
| `views/ProjectSkillsView.vue` | D | 读路由；直接取数（`api.ts`） |
| `views/SiteOpenView.vue` | D | 读路由；直接取数（`api.ts`） |
| `views/TeamInviteView.vue` | D | 读路由；直接取数（`api.ts`）；直接取数（`network/api/teams`） |
| `views/account/AddEmail.vue` | 容器 | 画面在 `AddEmailView.vue`（A 级）；取数、路由留在本页 |
| `views/account/AppSignInFinish.vue` | 容器 | 画面在 `AppSignInFinishView.vue`（A 级）；取数、路由留在本页 |
| `views/account/AppSignInStart.vue` | 容器 | 画面在 `AppSignInStartView.vue`（A 级）；取数、路由留在本页 |
| `views/account/BackToApp.vue` | 容器 | 画面在 `BackToAppView.vue`（A 级）；取数、路由留在本页 |
| `views/account/OAuthComplete.vue` | 容器 | 画面在 `OAuthCompleteView.vue`（A 级）；取数、路由、同意弹窗的取版本留在本页 |
| `views/account/OAuthError.vue` | 容器 | 画面在 `OAuthErrorView.vue`（A 级）；取数、路由留在本页 |
| `views/account/OAuthSuccess.vue` | 容器 | 画面在 `OAuthSuccessView.vue`（A 级）；取数、路由留在本页 |
| `views/account/OAuthVerify.vue` | 容器 | 画面在 `OAuthVerifyView.vue`（A 级）；取数、路由留在本页 |
| `views/account/PasskeyOffer.vue` | 容器 | 画面在 `PasskeyOfferView.vue`（A 级）；取数、路由、passkey 助手留在本页 |
| `views/account/SignIn.vue` | 容器 | 画面在 `SignInView.vue`（A 级）；取数、路由、passkey 助手留在本页 |
| `views/account/Verify2FA.vue` | 容器 | 画面在 `Verify2FAView.vue`（A 级）；取数、路由、passkey 助手留在本页 |
| `views/account/emailCode/Request.vue` | 容器 | 画面在 `RequestView.vue`（A 级）；取数、路由留在本页 |
| `views/account/emailCode/Verify.vue` | 容器 | 画面在 `VerifyView.vue`（A 级）；取数、路由留在本页 |
| `views/account/recover/password/Start.vue` | 容器 | 画面在 `StartView.vue`（A 级）；取数留在本页 |
| `views/account/recover/password/Verify.vue` | 容器 | 画面在 `VerifyView.vue`（A 级）；取数、路由留在本页 |
| `views/account/signup/Start.vue` | 容器 | 画面在 `StartView.vue`（A 级）；取数、路由、signup store 留在本页 |
| `views/account/signup/VerifyEmail.vue` | 容器 | 画面在 `VerifyEmailView.vue`（A 级）；取数、路由、signup store 留在本页 |
| `views/admin/AdminDashboardPage.vue` | 容器 | 画面在 `AdminDashboardPageView.vue`（A 级）；路由、读 store 留在本页 |
| `views/admin/AdminFeaturePage.vue` | 容器 | 画面在 `AdminFeaturePageView.vue`（A 级）；取数、路由留在本页 |
| `views/admin/AdminFeatureStatsPage.vue` | 容器 | 画面在 `AdminFeatureStatsPageView.vue`（A 级）；取数、路由留在本页 |
| `views/admin/AdminIntegrationsPage.vue` | 容器 | 画面在 `AdminIntegrationsPageView.vue`（A 级）；取数留在本页 |
| `views/admin/AdminLayout.vue` | 容器 | 画面在 `AdminLayoutView.vue`（A 级）；路由、读 store 留在本页 |
| `views/admin/AdminMembersPage.vue` | 容器 | 画面在 `AdminMembersPageView.vue`（A 级）；取数留在本页 |
| `views/admin/AdminModelsPage.vue` | 容器 | 画面在 `AdminModelsPageView.vue`（A 级）；取数留在本页 |
| `views/admin/AdminQueuePage.vue` | 容器 | 画面在 `AdminQueuePageView.vue`（A 级）；路由、读 store 留在本页 |
| `views/admin/AdminSpacesPage.vue` | 容器 | 画面在 `AdminSpacesPageView.vue`（A 级）；取数留在本页 |
| `views/feedback/AdminFeedbackPage.vue` | 容器 | 画面在 `AdminFeedbackPageView.vue`（A 级）；取数、读 store 留在本页 |
| `views/feedback/FeedbackCenterPage.vue` | 容器 | 画面在 `FeedbackCenterPageView.vue`（A 级）；读 store 留在本页 |
| `views/feedback/FeedbackDetailPage.vue` | 容器 | 画面在 `FeedbackDetailPageView.vue`（A 级）；取数、路由、读 store 留在本页 |
| `views/feedback/FeedbackMinePage.vue` | 容器 | 画面在 `FeedbackMinePageView.vue`（A 级）；路由、读 store 留在本页 |
| `views/feedback/FeedbackSubmitPage.vue` | 容器 | 画面在 `FeedbackSubmitPageView.vue`（A 级）；取数、路由、读 store 留在本页 |
| `views/home/Download.vue` | 容器 | 画面在 `DownloadView.vue`（A 级）；取数留在本页 |
| `views/home/Landing.vue` | 容器 | 画面在 `LandingView.vue`（A 级）；取数留在本页 |
| `views/home/MyWork.vue` | D | 读路由；直接取数（`api.ts`）；直接取数（`network/api/spaces`）；直接取数（`network/api/tasks`）；读 store（workspace） |
| `views/home/Solutions.vue` | 容器 | 画面在 `SolutionsView.vue`（A 级）；取数留在本页 |
| `views/legal/LegalDocument.vue` | 容器 | 画面在 `LegalDocumentView.vue`（A 级）；取数留在本页（路由指向本页） |
| `views/question/Ask.vue` | 容器 | 画面在 `AskView.vue`（A 级）；取数、路由留在本页 |
| `views/question/Detail.vue` | 容器 | 画面在 `DetailView.vue`（A 级）；取数、路由、provide/inject 留在本页 |
| `views/question/DetailAnswer.vue` | 容器 | 画面在 `DetailAnswerView.vue`（A 级）；取数、路由留在本页 |
| `views/question/DetailAnswerList.vue` | 容器 | 画面在 `DetailAnswerListView.vue`（A 级）；取数、provide/inject 留在本页 |
| `views/spaces/Detail.vue` | D | 读路由；直接取数（`network/api/avatars`）；直接取数（`network/api/spaces`）；直接取数（`services/account.ts`）；读 store（space） |
| `views/spaces/Index.vue` | D | 读路由；直接取数（`api.ts`）；直接取数（`network/api/avatars`）；直接取数（`network/api/spaces`）；直接取数（`services/account.ts`）；读 store（workspace） |
| `views/spaces/JoinCourse.vue` | D | 读路由；直接取数（`network/api/spaces`）；直接取数（`network/api/users`） |
| `views/spaces/board/SpaceBoardShell.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数 |
| `views/spaces/board/pages/Analytics.vue` | D | 读路由；直接取数（`network/api/spaces`） |
| `views/spaces/board/pages/Announcements.vue` | D | 读路由；直接取数（`services/account.ts`）；经 `views/spaces/board/store.ts` 取数；读 store（space） |
| `views/spaces/board/pages/BoardHome.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数；读 store（space） |
| `views/spaces/board/pages/Members.vue` | D | 读路由；直接取数（`network/api/spaces`）；经 `views/spaces/board/store.ts` 取数 |
| `views/spaces/board/pages/Mine.vue` | D | 读路由；直接取数（`network/api/spaces`）；经 `views/spaces/board/store.ts` 取数 |
| `views/spaces/board/pages/Review.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数 |
| `views/spaces/board/pages/TaskDetail.vue` | D | 读路由；直接取数（`network/api/spaces`）；直接取数（`network/api/tasks`）；经 `views/spaces/board/store.ts` 取数；`provide()` / `inject()` |
| `views/spaces/board/pages/TaskInsights.vue` | D | 读路由；直接取数（`network/api/spaces`）；直接取数（`network/api/tasks`）；经 `views/spaces/board/store.ts` 取数 |
| `views/spaces/board/pages/TaskPublish.vue` | D | 读路由；直接取数（`network/api/tasks`）；直接取数（`services/ErrorHandler.ts`）；经 `views/spaces/board/store.ts` 取数；`provide()` / `inject()`；读 store（space） |
| `views/spaces/course/CourseHome.vue` | C | 直接取数（`services/account.ts`）；读 store（space） |
| `views/spaces/course/CourseSettings.vue` | D | 读路由；读 store（space） |
| `views/spaces/course/CourseWork.vue` | C | 直接取数（`services/account.ts`）；读 store（space） |
| `views/spaces/course/People.vue` | D | 读路由；直接取数（`network/api/spaces`）；读 store（space） |
| `views/spaces/course/Quiz.vue` | D | 读路由；直接取数（`network/api/spaces`） |
| `views/spaces/course/Team.vue` | D | 读路由；直接取数（`network/api/spaces`）；直接取数（`network/api/teams`） |
| `views/spaces/course/Units.vue` | D | 读路由；直接取数（`network/api/spaces`） |
| `views/spaces/detail/Announcements.vue` | D | 读路由；直接取数（`network/api/spaces`）；读 store（space） |
| `views/spaces/detail/AuditTask.vue` | C | 直接取数（`network/api/tasks`）；读 store（space） |
| `views/spaces/detail/CreateDiscussion.vue` | D | 读路由；直接取数（`network/api/discussions`） |
| `views/spaces/detail/DiscussionItem.vue` | D | 读路由；直接取数（`network/api/discussions`） |
| `views/spaces/detail/Discussions.vue` | D | 读路由；直接取数（`network/api/attachments`）；直接取数（`network/api/discussions`） |
| `views/spaces/detail/ManageCategories.vue` | C | 读 store（space） |
| `views/spaces/detail/ManageDomainGroups.vue` | D | 读路由；直接取数（`network/api/spaces`）；读 store（space） |
| `views/spaces/detail/ManageInviteCodes.vue` | D | 读路由；直接取数（`network/api/spaces`） |
| `views/spaces/detail/ManageTemplates.vue` | D | 读路由；读 store（space） |
| `views/spaces/detail/ManageTopics.vue` | C | 读 store（space） |
| `views/spaces/detail/PublishTask.vue` | 容器 | 画面在 `PublishTaskView.vue`（A 级）；取数、路由留在本页 |
| `views/spaces/detail/Tasks.vue` | D | 读路由；直接取数（`network/api/spaces`）；直接取数（`network/api/tasks`）；读 store（space） |
| `views/spaces/detail/TemplateForm.vue` | D | 读路由；读 store（space） |
| `views/spaces/detail/analytics/Alerts.vue` | C | 直接取数（`network/api/spaces`） |
| `views/spaces/detail/analytics/Index.vue` | A | 只吃 props 和事件 |
| `views/spaces/detail/analytics/Learning.vue` | C | 直接取数（`network/api/spaces`） |
| `views/spaces/detail/analytics/Overview.vue` | C | 直接取数（`network/api/spaces`） |
| `views/spaces/detail/analytics/Participants.vue` | C | 直接取数（`network/api/spaces`） |
| `views/spaces/detail/analytics/Publishers.vue` | C | 直接取数（`network/api/spaces`） |
| `views/spaces/detail/analytics/Tasks.vue` | C | 直接取数（`network/api/spaces`） |
| `views/spaces/detail/member-tasks/MyParticipating.vue` | D | 读路由；直接取数（`network/api/spaces`） |
| `views/spaces/detail/member-tasks/MyPublishing.vue` | D | 读路由；直接取数（`network/api/spaces`）；读 store（space） |
| `views/tasks/Detail.vue` | 容器 | 画面在 `DetailView.vue`（A 级）；路由留在本页 |
| `views/tasks/Edit.vue` | 容器 | 画面在 `EditView.vue`（A 级）；取数、路由、读 store 留在本页 |
| `views/tasks/detail/Overview.vue` | D | 读路由；直接取数（`api.ts`）；直接取数（`network/api/tasks/types.ts`）；直接取数（`services/account.ts`） |
| `views/tasks/detail/Submissions.vue` | 容器 | 画面在 `SubmissionsView.vue`（A 级）；取数留在本页 |
| `views/tasks/detail/Submit.vue` | 容器 | 画面在 `SubmitView.vue`（A 级）；取数、路由留在本页 |
| `views/teams/Detail.vue` | 容器 | 画面在 `DetailView.vue`（A 级）；取数、路由、provide/inject 留在本页 |
| `views/teams/Explore.vue` | 容器 | 画面在 `ExploreView.vue`（A 级）；取数留在本页 |
| `views/teams/Index.vue` | 容器 | 画面在 `IndexView.vue`（A 级）；取数、路由留在本页 |
| `views/teams/Mine.vue` | 容器 | 画面在 `MineView.vue`（A 级）；取数留在本页 |
| `views/teams/Pending.vue` | 容器 | 画面在 `PendingView.vue`（A 级）；取数留在本页 |
| `views/teams/detail/Compute.vue` | 容器 | 画面在 `ComputeView.vue`（A 级）；取数、provide/inject 留在本页 |
| `views/teams/detail/Knowledge.vue` | 容器 | 画面在 `KnowledgeView.vue`（A 级）；取数、provide/inject 留在本页 |
| `views/teams/detail/Members.vue` | 容器 | 画面在 `MembersView.vue`（A 级）；取数、路由、provide/inject 留在本页 |
| `views/teams/detail/TeamProjects.vue` | 容器 | 画面在 `TeamProjectsView.vue`（A 级）；取数、路由、provide/inject 留在本页 |
| `views/user/settings/General.vue` | A | 只吃 props 和事件 |
| `views/user/settings/Profile.vue` | 容器 | 画面在 `ProfileView.vue`（A 级）；取数、头像上传留在本页 |
| `views/user/settings/RealName.vue` | 容器 | 画面在 `RealNameView.vue`（A 级）；取数、sudo、日志分页留在本页 |
| `views/user/settings/Security.vue` | 容器 | 画面在 `SecurityView.vue`（A 级）；取数、sudo、对话框流程留在本页 |
| `views/workspace/DmView.vue` | D | 读路由；直接取数（`api.ts`）；读 store（workspace） |
| `views/workspace/ProjectMembersView.vue` | D | 读路由；直接取数（`api.ts`）；读 store（workspace） |
| `views/workspace/ProjectShell.vue` | D | 读路由；读 store（workspace） |
| `views/workspace/ProjectSidebar.vue` | D | 读路由；经 `lib/routePrefetch.ts` 取数；读 store（workspace） |
| `views/workspace/RunningWorkView.vue` | D | 读路由；直接取数（`api.ts`）；读 store（workspace） |
| `views/workspace/TopicView.vue` | D | 读路由；直接取数（`api.ts`）；读 store（workspace） |
| `views/workspace/WorkspaceEntry.vue` | D | 读路由；读 store（workspace） |

## 工作面板 {#panels}

`src/components/panels/` 下的 21 个 SFC，也就是频道和任务页右侧那一块工作面板和它几个页签的内容。

| 面板 | 档 | 卡在哪 |
|---|---|---|
| `components/panels/ChangesFileTree.vue` | A | 只吃 props 和事件 |
| `components/panels/PanelCard.vue` | C | 直接取数（`api.ts`）；经 `components/room/composables/useRoomSocket.ts` 取数 |
| `components/panels/PanelChanges.vue` | C | 经 `composables/usePanelChanges.ts` 取数 |
| `components/panels/PanelChangesView.vue` | A | 只吃 props 和事件 |
| `components/panels/PanelDoc.vue` | C | 经 `composables/usePanelDoc.ts` 取数 |
| `components/panels/PanelDocView.vue` | A | 只吃 props 和事件 |
| `components/panels/PanelPreview.vue` | C | 经 `composables/usePanelPreview.ts` 取数 |
| `components/panels/PanelPreviewView.vue` | A | 只吃 props 和事件 |
| `components/panels/PanelSite.vue` | C | 直接取数（`api.ts`） |
| `components/panels/PanelTabs.vue` | A | 只吃 props 和事件 |
| `components/panels/SiteStepOutput.vue` | C | 直接取数（`api.ts`） |
| `components/panels/TodoChecklist.vue` | A | 只吃 props 和事件 |
| `components/panels/doc/DocComments.vue` | C | 直接取数（`api.ts`） |
| `components/panels/doc/DocOverlays.vue` | A | 只吃 props 和事件 |
| `components/panels/doc/DocSlashMenu.vue` | A | 只吃 props 和事件 |
| `components/panels/doc/DocSurface.vue` | A | 只吃 props 和事件 |
| `components/panels/preview/PreviewPages.vue` | A | 只吃 props 和事件 |
| `components/panels/preview/PreviewSheet.vue` | A | 只吃 props 和事件 |
| `components/panels/preview/RevisionList.vue` | C | 直接取数（`api.ts`） |
| `components/panels/preview/RoomFileEditor.vue` | C | 直接取数（`api.ts`） |
| `components/panels/preview/RoomFileHistory.vue` | C | 直接取数（`api.ts`） |

**三个外壳正在被拆空。** `PanelDoc` / `PanelChanges` / `PanelPreview` 今天都是 C，但它们是「页签的外壳」：取数在 `composables/usePanelDoc.ts`、`usePanelChanges.ts`、`usePanelPreview.ts` 里，外壳把结果整理成 props 交给视图。三个视图自己也不取数，但它们在模板里渲染的子组件（`AttachmentImage`、`preview/RoomFileEditor`、`doc/DocComments` 等）会取数，所以判据修好之后**它们是 C**，要看下一层拆得干不干净。方向不变：**把 C 留在外壳上，把 A 一层层攒出来**。

## 卡在哪一步 {#blockers}

130 个页面里，每条原因各占多少页（一页可以算多笔）：

| 原因 | 页数 | 分成 |
|---|---|---|
| 直接 import 取数模块（`@/api`、`@/network/*`、`@/services/*`） | 100 | |
| 读路由（`useRoute` / `$route`） | 86 | |
| 读 store | 43 | `space` 20、`workspace` 11、`feedback` 7、`pageTitle` 4、`signup` 2、`navigation` 1 |
| 经中间模块绕到取数 | 27 | `views/spaces/board/store.ts` 9、`utils/sudo.ts` 4、`views/account/passkeyEnrollment.ts` 4、`composables/useChosenAvatar.ts` 3、`api/feishu.ts` 2、`lib/desktop.ts` 2、`views/admin/features/registry.ts` 2、其余 9 个各 1 |
| `provide()` / `inject()` | 各 4 | |

前两条就是大头，而且它们的修法不一样：

- **直接取数（100 页）** 是「把这一段挪出去」的问题，机械，但要一页一页做。`usePanelDoc.ts` 那条路（外壳取数、视图收 props）是可复制的样板。
- **读路由（86 页）** 是「这个页面怎么知道自己是谁」的问题。`views/spaces/board/` 已经有一套答案（命名路由 + `loadBoard(spaceId)` 在守卫里），`/spaces/:id/board` 那棵树 43 页里 12 页 C、30 页 D，是最大的一块。
- **`views/workspace/`（7 页全 D）** 是剩下最硬的骨头：7 页每一页都是「读路由 + 读 workspace store」，`ProjectShell` 那一页本身就是外壳，动它是动整个 `/workspace` 那棵路由。`views/account/` 那 17 页的输入——OAuth 回调码、邀请令牌、验证码——2026-10-05 已经改成 props 进视图，整条线拆完。

## 这份清单怎么来的 {#how}

数字不是手数的：`.claude/scripts/frontend_grade.py` 里的 `grade_frontend` 逐文件跑一遍，取它的等级和理由；页面清单取自 `src/router/` 的 import 图（`scene-ratchet.py --list` 能把两者一起打出来——这一页的两张表就是它生成的）。看板上的 A/B/C/D 是**全部 435 个 `.vue`** 的口径，这里只切出「场景」这一层（路由页 + 面板），所以和看板对不上是正常的。

两个已知偏差写在这里，免得被当成事实用：

- **`import type` 那条边已经修掉（2026-09-30）。** 规约说 `import type` 不算（构建时就被抹掉，见[架构指标](/dev/arch-metrics#metrics)）；但 `lib/previewSession.ts` 整个文件只有一句 `import type { PreviewSession } from '../api'`，它到 `api.ts` 的那条边一度仍然进了传递闭包，于是 `PanelPreviewView` 被判成 C——它自己一行取数代码都没有。现在这条边不进图了。但同一次里还查出一处**过宽**：传递依赖只跟 `.ts`，`.vue` 子组件那条链被漏掉，于是「自己干净、子组件取数」的十页被判成 A 并冻进基线。两处都在 2026-09-30 修掉，基线和这一页的数字按修完的结果重算（能单独跑 20 → 10）。
- **正则不是编译器。** 解析不出来的模块说明符当成外部依赖，不当成一条边，所以真隔着一条解析不出来的链在取数的组件会被判高一档（A 或 B）。

## 下一步 {#next}

1. **已经拆出来的三个视图挂上目录**：`PanelChangesView`、`PanelPreviewView`、`PanelDocView`，每个带 loading / 空 / 有数据 / 出错几种状态。上一版已经做了。
2. **A 档的先补目录**，成本几乎为零：`--list` 现在会打出 14 个没挂的（7 个页面 + 7 个面板：`ChangesFileTree`、`TodoChecklist`、`doc/DocOverlays`、`doc/DocSlashMenu`、`doc/DocSurface`、`preview/PreviewPages`、`preview/PreviewSheet`）。挂上去之后，改外观和改排版就有地方看效果。
3. **C 档按「外壳 / 内容」拆**：取数留在外层 composable，视图只收 props，一次一个页签；每拆出一个就跑 `pnpm run lint:scenes:update` 把它从 `debt` 搬进 `ready`。
4. **D 档要单独排**，不是一页一页能拆完的：读路由那一批要先定「参数从哪进来」。`views/account/` 已按「参数当 props 进视图」拆完（2026-10-05），`views/workspace/`、`views/spaces/` 两块各还要一个方案，动哪块由产品定。
5. **剩下的就是搬 `debt`。** `ready` 只增不减、`debt` 只减不增（[规则](#ratchet)之后没有别的口子），所以这条曲线只有一个方向：97 → 0。
