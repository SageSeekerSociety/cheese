---
title: 前端结构
kind: 参考
summary: 两个入口、壳与路由、房间的两栏、工作面板的页签、状态与请求的两套栈、i18n 债务表和四道质量闸。
covers:
  - frontend/src/main.ts
  - frontend/src/demo-main.ts
  - frontend/src/App.vue
  - frontend/src/router/
  - frontend/src/views/workspace/
  - frontend/src/views/spaces/board/
  - frontend/src/components/ChatPanel.vue
  - frontend/src/components/WorkPanel.vue
  - frontend/src/components/panels/
  - frontend/src/components/room/
  - frontend/src/stores/
  - frontend/src/api.ts
  - frontend/src/network/
  - frontend/src/i18n/
  - frontend/scripts/tsc-ratchet.mjs
  - frontend/scripts/stylelint-ratchet.mjs
  - frontend/stylelint.config.cjs
  - frontend/nginx.conf
  - docs/design-system.md
---

# 前端结构 {#frontend}

前端是一个 Vue 3 + Vuetify 的单页应用，但 `frontend/index.html` 之外还有第二个入口：文档站用它内嵌的 `/demo/<名字>` 演示。

> 讲：两个入口、壳与路由、房间两栏和它们的行管线、工作面板的页签、状态与请求的两套栈、翻译债务表和四道机器闸。不讲：设计 token 的取值和色板（见仓库内 `docs/design-system.md`，闸门见[质量闸](#gates)），文档站怎么构建和发布（见[文档站与问芝士](/dev/docs-site#build)），后端接口的形状和约定（见[后端结构与接口约定](/dev/backend-app#envelope)）。

## 两个入口 {#entries}

| 入口 HTML | 脚本 | 是什么 |
|---|---|---|
| `index.html` | `src/main.ts` | 整个应用：装 Vuetify / i18n / pinia、恢复登录、挂路由 |
| `demo.html` | `src/demo-main.ts` | 只装演示页（`views/demo/DemoView.vue`）：不起路由、不恢复登录、不连后端 |

`demo-main.ts` 的存在理由是「越轻越好」：文档把这一页嵌进 iframe，后端没起来它也不能挂，而整个应用那条入口两样都做不到。它照样 `.use(vuetify).use(i18n)`，所以画面用的是产品自己的组件和主题，和真界面长得一样。地址是 `/demo/<名字>`，不带就放第一个场景。

nginx（`frontend/nginx.conf`）把两条路分开：`location /` 走 `try_files $uri $uri/ /index.html`（SPA 兜底），`location /demo/` 走 `try_files /demo.html =404`。dev server 在 `vite.config.ts` 里做同样的事（`req.url` 以 `/demo` 开头就改写成 `/demo.html`）。`/docs/`、`/docs/dev/` 各有自己的 location，见[文档站与问芝士](/dev/docs-site#build)。

`main.ts` 在挂载前后各做两件容易漏的事：

- `watchForStaleBuild()` / `clearStaleBuildGuard()`：一个标签页跨过一次发版之后，懒加载的 chunk 在新构建里已经不存在，点一下就是一个 rejected import 而不是一页。启动时先接住这个失败，挂载成功就是「刷一下确实修好了」的证据，于是把一次性开关放开给下一次发版。
- `installErrorReporter(app)`：浏览器侧的报错上报进当前话题的「现场」，这样读不到用户控制台的 agent 也能拿到它。

## 壳与路由 {#shell}

`App.vue` 是一层极薄的壳，只在两种路由上工作不同：

- **`meta.publicLanding`** —— 不套应用外壳，`App.vue` 直接 `<router-view />`。首页的落地页各分支（`router/home.ts`）和用户协议、隐私政策（`router/legal.ts`）用它。
- **`meta.isFullPage`** —— 一页占满，没有常规布局（加入团队、预览、站点、设备、连接设备、待办、市场等）。

路由表在 `router/index.ts` 里拼装：每个域一个文件（`account`、`home`、`user`、`question`、`spaces`、`teams`、`feedback`、`legal`、`courseJoin`），加 `./legacyProjectPaths` 的旧地址重定向、`workspaceRoutes`（房间那一棵）和 `views/spaces/board/routes.ts`（空间新界面那一棵）。通配 `NotFound` **必须挂在最后** —— 被它吃掉的后果是那几页打不开，而「打不开」看起来像后端 404。

四条全局守卫各管一件事：

| 位置 | 做什么 |
|---|---|
| `beforeEach(carryLoginRedirect)` + `requireEmail(router, …)` | 未登录跳登录、未验证邮箱先补邮箱 |
| `beforeEach`（动态标题） | 有 `meta.getDynamicTitle` 的路由把标题算出来交给 `stores/title.ts` |
| `afterEach` | 触发标题更新；`recordEntry(to, from, …)` 记下来路，顶栏那颗 ← 靠它回得去。名字**必须在这一刻**抓下来跟着路由一起存 —— 按 ← 的时候那一页早就卸载了 |
| `beforeEach`（话题预取） | 进 `workspace-topic` 且已登录时，立刻起消息请求并预热 pdf.js。**不在这里起头**的话，消息要等话题页那串 chunk 下完、ChatPanel 挂上之后才开始请求，冷打开一个话题时那条最大的消息晚半秒以上 |

懒加载页面在导航时下载，所以 `router.onError(reloadForNewBuild)` 兜住「发版撞上这次点击」。

## 房间：一列对话，一块工作面板 {#room}

房间是 `views/workspace/` 那一棵：`ProjectShell.vue` 是项目壳（左栏 `ProjectSidebar`），话题在 `TopicView.vue` —— 一个话题头，然后 `TopicChatColumn.vue`（左）和 `WorkPanel.vue`（右）分栏。手机上两栏放不下，对话变成工作面板那条 tab 栏的第一格。

`ProjectShell` 按 topicId 给 `TopicView` 打 key，所以**每个话题一个全新实例**，下面（或任何子组件里）不会有上一个话题的状态被带过来。

工作面板开在哪个页签写进 URL（`?tab=`），所以「你来看一眼这个 diff」是一条能发出去的链接。地址是页面的事，不是面板的：面板只报告自己动了，页面负责写回 query。

## 对话的行管线 {#rows}

聊天区一行一行的东西有**三种**：一条消息、一条还没落库的消息（同一个组件的 pending 档）、以及「芝士正在做」那一行（`ChatPanel` 自己画的）。它们的几何只有一份，写在 `components/room/room-row.css` 里，三个组件各自 `<style scoped src>` 引进去 —— 复制三份的话，改了其中一处的留白，另外两行就会错位。

`ChatPanel.vue` 里 `rows` 这个 computed 是唯一的行管线：

```
messages → coalesceSplitFencedCodeBlocks → collapseNotices → 渲染
```

- `coalesceSplitFencedCodeBlocks` 先把被拆成连续几行的围栏代码块拼回来，**再**交给折叠。
- `collapseNotices`（`lib/platformNotice.ts`）决定平台自己说的话怎么渲染：连续的同类事件折成一行，长文进 `<details>`。硬约束是「信息不能丢，只能收起来」—— 没有任何一档会把原文扔掉，折成一行时每一次的原文都还在展开区里。

平台事件的契约是**后端只发码、文案在前端**：`kind=event` 的 block 带 `meta`（`event_type`、`severity`、`who`、`detail`、`detail_label`…），正文只有一行 ≤40 字的人话。`meta.in_room` 单独一格管「露不露面」，缺省是露面 —— 它过去和「谁写的」挤在 `author_type` 一个字段里（system 出现、ai 不出现），于是「确实由芝士产生、又该让人看见的事件」无法表达。对消息同样生效：一条通篇没有中文的 AI 消息照常落库、照常在历史里，只是聊天区不显示。

多个队友并行跑时，工作面板的「现场」页签标签上带名字和跳动的点，见[同一话题里的几个 AI 队友](/dev/turn#seats-ui)。

## 工作面板与页签 {#workpanel}

`WorkPanel.vue` 只负责两件事：**哪一页签在屏幕上、到底有哪几页签**；以及那些「必须一直对、不随页签切走」的信号。一页签渲染和取的东西都属于它自己那个 SFC。唯一的跨页签线是 `open-file`：文档里（或聊天里）一个 `<&path>` chip 打开那个文件 —— 房间文件里能画的走自己的页签，否则走「改动」。

固定页签是五格：`chat`（对话）、`overview`（总览）、`site`（现场）、`changes`（改动）、`preview`（预览）。旧地址里的 `?tab=doc` / `?tab=tasks` 都并进总览了。另有一类**文件页签**，键是 `file:<路径>`，一份文件一个，「你看一下这个文件」因此也是一条能发的链接。

页签的内容在 `components/panels/` 下：`PanelDoc`（总览）、`PanelSite`（现场，`SiteStepOutput`、`SiteStatusBar`）、`PanelChanges`（改动，没绑仓库时 `PanelChanges.noRepo`）、`PanelPreview`（预览，含文档与媒体）、`PanelProgress`（任务进度）、`PanelCard`（共用外壳）。

## 看板是并存的一棵 {#board}

`views/spaces/board/` 是空间的新界面，挂在 `/spaces/:id/board` 下，**是并存的一棵而不是替换**：老的空间页仍在 `/spaces/:id/{tasks,analytics,…}` 上原样服务。整板看板那一格已经是这块板自己的（`pages/Analytics.vue`，数字来自老树那九页背后同一组接口），老树那九页一页没删。

它有自己的外壳（`SpaceBoardShell.vue`），顶层路由，不被老 `/spaces/:spaceId` 那条嵌套路由接住，所以不会套上老侧栏。所有地址都用**命名路由**，路径前缀只出现在 `routes.ts` 一个文件里 —— 等新界面把老页面接手过去，收的时候改一行。

管理员的门槛写在 `beforeEnter`（`managerOnly`）而不是页面里，因为**直接输地址也要挡住**；守卫必须先 `await loadBoard(spaceId)`，角色是从 `space.admins` 算出来的，没装好之前谁都是 MEMBER。

看板列是怎么推出来的见[看板与里程碑](/dev/boards#board)，待处理清单见[任务与工作目录](/dev/tasks#awaiting)。

## 状态与请求：并存的两套栈 {#state-network}

这是读这一栏时最容易踩的一件事：**前端有两条各自独立的请求栈。**

| | `src/api.ts` | `src/network/` |
|---|---|---|
| 底座 | 原生 `fetch` | axios（`class Api` 包一层实例 + 拦截器） |
| 服务谁 | 房间、话题、工作面板那一批视图 | 主产品的那批路由（`network/api/*`：users、tasks、teams、spaces、materials…） |
| 信封 | 自己剥 `{code,message,data}` | 响应拦截器里剥 |
| 401 处理 | 请求里重试一次（见下） | 响应拦截器刷新后重放 |

`stores/` 是 pinia：`workspace.ts`（项目与话题）、`feedback.ts`（反馈，含缓存、分页、陈旧判断）、`space.ts`、`discussionStore.ts`、`navigation.ts`、`signup.ts`、`title.ts`。房间 chrome、聊天和工作面板挂载时会各自请求同一份名册/任务摘要，所以 `api.ts` 里有一个 `pendingRoomReads` 表**只共享在飞行中的读**：写请求（任何非 GET）一进来就整表清空，下一次读一定打到服务器。

## 请求的信封、重试、401 与预算 {#api}

`api.ts` 的 `request<T>` 是 GET 才套预算，非 GET 直接进 `performRequest`：

| 机制 | 细节 |
|---|---|
| 读预算 | `withinBudget(..., READ_BUDGET_MS)`（20 秒）给 GET 一个 `AbortController`，超时抛 `RequestTimeoutError`（「请求等待超时，请重试」）；外部传了 signal 就一起接 |
| 信封 | 成功路径要求 `envelope.code === 200`，否则抛 `Error(envelope.message || 'API error code N')`，返回值是 `envelope.data` |
| 错误 | 非 2xx 抛 `ApiError(status, message, code, requestId, retryable)`。文案优先取 `error.message || message` —— 一个只显示 `HTTP 422 for /path` 的提示会让房间里的人去找服务器早就解释过的东西。`requestId` 取响应头 `X-Request-ID` |
| 401 | **任何方法**都重试一次，且只在强制刷新真的换到了不同的 token 时重试（否则一个因别的原因 401 的服务器会让每次调用都发两遍）。这一次不计入退避预算（`attempt -= 1`） |
| 发请求前的刷新 | `ensureFreshToken()` 按 `exp`（提前 60 秒）决定要不要刷。但 `exp` 不是 token 唯一的死法：签名密钥在后端重启后变过，一个还有 457 秒寿命的令牌被两层 API 连续拒了 24 次。所以还有 `refreshNow()` —— 不看 `exp`，一律刷，走 `refreshSession` 所以一串 401 只付一次刷新，也不会和另一个标签页抢 |
| GET 重试 | 状态码 502/503/504 与 Cloudflare 的 520–530（nginx 答 502–504 表示够不到应用，Cloudflare 答 520–530 表示够不到源站），延迟 `[250, 750]` 毫秒 |
| 不是应用在说话 | `readJson` 先看 `content-type`；响应体不是 JSON（门户页、SPA 兜底 —— 两者都可能说 200）时抛 `ApiError(res.status, transportFailureMessage(...))`，而不是把一个 HTML 页塞进 JSON 解析器 |

`connectorRequest` 是同一形状的镜像：连接器在源根 `/connector/*`、不在 `/api` 下，也不套信封，所以那边跳过前缀和剥壳，只保留 Bearer。

`isEndpointMissing(e)`（404/405）是给「后端还没上这个接口」和「上了但失败了」分开用的 —— 否则首次渲染一个还没合并的 API 就是一条像 bug 的错误横幅。

## 国际化 {#i18n}

`i18n/` 下两份目录 `messages/zh-CN` 与 `messages/en` 必须描述同一个产品：只在一边存在的命名空间，就是 #929 那次「英文访客进了工作区，得到半屏中文而构建什么都没说」。`catalog.spec.ts` 就是那个「something」。

不是「缺翻译」和「没人用」各存一份债表：

| 文件 | 是什么 | 今天 |
|---|---|---|
| `untranslated.json` | 会渲染、但没有英文的叶子 | 8 条 |
| `unused.json` | 没有任何源文件引用的叶子 | 93 条 |

两张表**不相交**（没人渲染的叶子不是翻译欠账，算进去会把实际的活夸大）；两张都只能有意编辑 —— 一条不再符合现实的记录会让套件失败，所以表是诚实缩小而不是烂掉。语言名和切换标签是母语写法（每种语言里都是同一个字符串），所以它们根本不住在目录里，而是 `languages.ts` 的常量：于是「英文目录里出现 CJK 是正确的」这件事没有豁免可躲。

`fallbackLocale` 是 `zh-CN`：少一个 key 退化成可读的中文而不是原始 key；这个回退在运行时是**静默的**，保证「这个缺口是已知的」的地方是债表和 `catalog.spec.ts`。开发构建对每一次缺失和回退都告警。

## 质量闸 {#gates}

四道机器闸，全部**只读**（能改文件的版本是另一个脚本，见下），跑在 `.github/workflows/frontend.yml`：

| 闸门 | 命令 | 拦什么 |
|---|---|---|
| ESLint | `pnpm run lint`（`task fe:lint:check`） | 代码问题；故意不传 `--fix` —— 会重写工作区的闸门可以在它偷偷修好的违规上退出 0 |
| 设计 token | `pnpm run lint:style` | 新增的写死颜色（hex、颜色名、数值型 `rgb()`/`hsl()`）与不在 6/8/12/999 档位里的 `border-radius` |
| 类型 | `pnpm run typecheck` | `vue-tsc --noEmit` 的新增报错 |
| 棘轮自己的单测 | `pnpm run test:ratchet` | `scripts/*.test.mjs`（node:test 地盘，不是 vitest 的） |

两个棘轮的形状一样：`tsc-ratchet.mjs` / `stylelint-ratchet.mjs` 跑检查、解析报告、和基线 `tsc-baseline.json` / `stylelint-baseline.json` 比，**只拦新增**，`--update` 把基线降下来。今天的 `tsc-baseline.json` 是**空的**（一个类型错误都不许有），`stylelint-baseline.json` 冻着 17 个文件的存量违规。两个脚本都显式解析 `node_modules/.bin` 下的二进制而不是信 PATH：**一个只是缺失的 vue-tsc / stylelint 不能长得像一次干净的检查**；一个非零退出但解析不出任何诊断，是崩溃而不是「零违规」。

调色板还有第三道闸（`color="grey-*"`、`bg-white` 这类固定色），在仓库根的 `.claude/scripts/check-repo-rules.sh`，存量冻在 `frontend/palette-baseline.json`。三份基线都**只能降不能升**，理由见 `docs/design-system.md` §7：一个悄悄失效的闸门和一棵干净的树，输出一模一样。那里也列了没有闸门、只能靠 review 的部分（排版、文案、动效）。

## 测试约定 {#tests}

- 单测是 vitest，`environment: 'happy-dom'`，setup 文件 `src/test/setup-network.ts`；CI 跑 `vitest run --dir src`。
- 测试文件与源文件同目录、同名前缀（`ChatPanel.mentionMenu.spec.ts`、`WorkPanel.tabs.spec.ts`），一个行为一个文件。
- `@/api` 会被 `vi.mock` 掉，所以请求栈本身的测试（`apiRetry.spec.ts`、`api401Retry.spec.ts`、`apiTokenRefresh.spec.ts`、`apiEdgeErrorPage.spec.ts`）单独摆一份。
- `scripts/` 下的是 `node --test` 的地盘（`pnpm run test:ratchet`），不是 vitest 的：它们匹配 vitest 默认的 include glob，混进去会让整个 run 以 "No test suite found" 挂掉。
- 端到端是 Playwright（`e2e/`），它自己在 `e2e/playwright.config.ts` 里复现 `/api` 前缀的剥法。

## 边界与坑 {#traps}

- **两套请求栈不是重构没做完。** 改一个接口前先确认调用方用的是 `@/api`（fetch、自己剥信封）还是 `@/network`（axios、拦截器剥），两边的重试与 401 行为不同。
- **`retryable` 读不出东西。** 后端构造的每个错误体里它都是 `false`（见[后端结构与接口约定](/dev/backend-app#errors)），所以 `api.ts` 里「`retryable !== false` 才重试」这条条件在今天的后端上恒为真 —— 它拦不住任何一次重试。
- **`App.vue` 之外还有第二个入口。** 改全局样式或插件时，`demo-main.ts` 那条路只装了 vuetify 和 i18n：依赖路由、登录态或任何 store 的东西在那里都没有，而文档的每次构建都会跑到它。
- **`NotFound` 通配必须最后。** 前面插一条落不进任何 match 的路由，被它吃掉的表现是「页面打不开」，看起来像后端 404。
- **折起来的必须还在。** `collapseNotices` 的硬约束是「信息不能丢，只能收起来」；新增一档折叠时，原文必须仍然在展开区里，不能只留一行摘要。
- **`meta.in_room` 缺省是露面。** 不写这一格的事件会进聊天区 —— 想让机器自己的输出不占屏，必须显式写 `false`，而不是指望某个作者的默认。
- **棘轮基线只能减。** 用 `--update` 降基线，不要为了让 PR 过而手工往 `*-baseline.json` 里加条目；加进去的那一条永远不会自己消失。
- **两套栈的错误类型互不认识。** `network/utils/requestErrorMessage.ts` 只认 axios 那侧的 `BusinessError`，**别的错误一律返回调用方给的 fallback** —— 把一个 `ApiError` 递给它，界面就只会说那句兜底的话。要按状态码分辨（例如把 409 和别的分开展示），读 `ApiError.status`，不要对 message 做子串匹配。
