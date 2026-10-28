---
title: 前端结构
kind: 参考
summary: 两个入口、组件预览站和它的收录闸、壳与路由、组件要路由时的那条缝、房间的两栏、工作面板的页签、状态与请求、i18n 债务表和九道质量闸。
covers:
  - frontend/src/main.ts
  - frontend/src/demo-main.ts
  - frontend/src/App.vue
  - frontend/src/router/
  - frontend/src/views/workspace/
  - frontend/src/components/ChatPanel.vue
  - frontend/src/components/WorkPanel.vue
  - frontend/src/components/panels/
  - frontend/src/components/room/
  - frontend/src/components/common/NavLink.vue
  - frontend/src/composables/useNavigation.ts
  - frontend/src/views/demo/catalog.ts
  - frontend/src/views/demo/DemoCatalog.vue
  - frontend/scripts/catalog-scaffold.mjs
  - frontend/src/stores/
  - frontend/src/api.ts
  - frontend/src/api/
  - frontend/src/i18n/
  - frontend/scripts/tsc-ratchet.mjs
  - frontend/scripts/stylelint-ratchet.mjs
  - frontend/stylelint.config.cjs
  - frontend/nginx.conf
  - docs/design-system.md
---

# 前端结构 {#frontend}

前端是一个 Vue 3 + Vuetify 的单页应用，但 `frontend/index.html` 之外还有第二个入口：文档站用它内嵌的 `/demo/<名字>` 演示，`/demo/catalog` 则是脱离后端的组件预览站。

> 讲：两个入口、壳与路由、组件要路由时的那条缝、房间两栏和它们的行管线、工作面板的页签、状态与请求、翻译债务表和机器闸。不讲：设计 token 的取值和色板（见仓库内 `docs/design-system.md`，闸门见[质量闸](#gates)），文档站怎么构建和发布（见[文档站与问芝士](/dev/docs-site#build)），后端接口的形状和约定（见[后端结构与接口约定](/dev/backend-app#envelope)）。

## 两个入口 {#entries}

| 入口 HTML | 脚本 | 是什么 |
|---|---|---|
| `index.html` | `src/main.ts` | 整个应用：装 Vuetify / i18n / pinia、恢复登录、挂路由 |
| `demo.html` | `src/demo-main.ts` | 只装演示页（`views/demo/DemoView.vue`）和组件预览站（`views/demo/DemoCatalog.vue`）：不起路由、不恢复登录、不连后端 |

`demo-main.ts` 的存在理由是「越轻越好」：文档把这一页嵌进 iframe，后端没起来它也不能挂，而整个应用那条入口两样都做不到。它照样 `.use(vuetify).use(i18n)`，所以画面用的是产品自己的组件和主题，和真界面长得一样。地址是 `/demo/<名字>`，不带就放第一个场景。

同一条入口上还有**组件预览站** `/demo/catalog`，见下一节。

nginx（`frontend/nginx.conf`）把两条路分开：`location /` 走 `try_files $uri $uri/ /index.html`（SPA 兜底），`location /demo/` 走 `try_files /demo.html =404`。dev server 在 `vite.config.ts` 里做同样的事（`req.url` 以 `/demo` 开头就改写成 `/demo.html`）。文档站的 location 在 `frontend/nginx/docs/`，在 `/docs/` 下还是在自己的域名上由 `DOCS_ORIGIN` 决定，见[文档站与问芝士](/dev/docs-site#build)。

`main.ts` 在挂载前后各做两件容易漏的事：

- `watchForStaleBuild()` / `clearStaleBuildGuard()`：一个标签页跨过一次发版之后，懒加载的 chunk 在新构建里已经不存在，点一下就是一个 rejected import 而不是一页。启动时先接住这个失败，挂载成功就是「刷一下确实修好了」的证据，于是把一次性开关放开给下一次发版。
- `installErrorReporter(app)`：浏览器侧的报错上报进当前频道的「现场」，这样读不到用户控制台的 agent 也能拿到它。

## 组件预览站 {#catalog}

[okcheese.com/demo/catalog](https://okcheese.com/demo/catalog) 是组件预览站：一个组件一页，一格里一种状态，用产品真实形状的数据渲染。它不连后端、不登录、不读环境变量；本地 `pnpm dev` 后打开同一路径也能看。

- **找组件**：目录页按源码目录分组，顶上的搜索框按名字、说明或路径筛。卡片上写着它在哪个文件、单独渲染要装哪几样（Vuetify、语言包、路由、store）。
- **提调整**：打开 `/demo/catalog/<id>`，每一格下有一句说明这一格在讲什么。看到不对的地方，把这个地址和格名发给负责前端的人或芝士。
- **注册表**：`views/demo/catalog.ts` 和它展开进来的 `catalog*.ts` 分册，页面和测试读同一份。示例数据放在各分册的 `*Fixtures.ts`，尽量取自产品自己的演示剧本。

条目和 Storybook 的 CSF 一一对应，以后换工具或做视觉回归能机械迁移：

| 预览站 | CSF |
|---|---|
| 条目的 `component`、`title`、`args` | default export 的同名字段 |
| `states` 里的一格 | 一个命名 story |
| 一格的 `props` | 这个 story 的 `args`，叠在条目的 `args` 上 |
| 一格的 `slot` | 默认插槽 |

加一个组件：

1. `node scripts/catalog-scaffold.mjs <src/...vue>` 按 props 打出骨架。
2. 把骨架贴进一个已被 `catalog.ts` 导入并展开的 `catalog*.ts` 分册（新开分册就要在 `catalog.ts` 里 `import` 它的数组并 `...` 展开进 `CATALOG`，否则页面和测试都看不见它），补上 `about`、每格的名字和说明，换上真实形状的示例数据。每格的 `expect`（一句字）或 `expectSelector`（一个选择器）要能证明画出来的是这一格，而不是兄弟格也有的东西。骨架里要人补的句子和占位值都标着 `TODO(catalog)`，不改完目录测试会报红。
3. 跑 `pnpm exec vitest run src/views/demo/catalog.spec.ts`。它按每格声明的插件逐格挂载，有任何 warning 或 error 就红。

「进目录」看代码不看 `file:` 标签：`catalog.ts` 本身或它导入并展开的分册（递归；`*Fixtures.ts` 不读）里，`import` 了这个 `.vue` 并写成某条条目的 `component:`。`demoRouter.ts` 里路由的 `component:`、没被导入的分册、注释里的 `component:` 都不算。哪些组件在目录里、哪些 A 级还没进，[组件总览](/dev/ref-components)每次构建从代码读一遍。

## 壳与路由 {#shell}

`App.vue` 是一层极薄的壳，只在两种路由上工作不同：

- **`meta.publicLanding`** —— 不套应用外壳，`App.vue` 直接 `<router-view />`。首页的落地页各分支（`router/home.ts`）和用户协议、隐私政策（`router/legal.ts`）用它。
- **`meta.isFullPage`** —— 一页占满，没有常规布局（加入团队、预览、站点、设备、连接设备、待办、市场等）。

路由表在 `router/index.ts` 里拼装：每个域一个文件（`account`、`home`、`user`、`question`、`spaces`、`teams`、`feedback`、`legal`），加 `./legacyProjectPaths` 的旧地址重定向和 `workspaceRoutes`（房间那一棵）。通配 `NotFound` **必须挂在最后** —— 被它吃掉的后果是那几页打不开，而「打不开」看起来像后端 404。

四条全局守卫各管一件事：

| 位置 | 做什么 |
|---|---|
| `beforeEach(carryLoginRedirect)` + `requireEmail(router, …)` | 未登录跳登录、未验证邮箱先补邮箱 |
| `beforeEach`（动态标题） | 有 `meta.getDynamicTitle` 的路由把标题算出来交给 `stores/title.ts` |
| `afterEach` | 触发标题更新；`recordEntry(to, from, …)` 记下来路，顶栏那颗 ← 靠它回得去。名字**必须在这一刻**抓下来跟着路由一起存 —— 按 ← 的时候那一页早就卸载了 |
| `beforeEach`（频道预取） | 进 `workspace-topic` 且已登录时，立刻起消息请求并预热 pdf.js。**不在这里起头**的话，消息要等频道页那串 chunk 下完、ChatPanel 挂上之后才开始请求，冷打开一个频道时那条最大的消息晚半秒以上 |

懒加载页面在导航时下载，所以 `router.onError(reloadForNewBuild)` 兜住「发版撞上这次点击」。

## 组件要路由时 {#component-nav}

`src/components/**` 里**不许 import `vue-router`**（`import type` 也算），闸门见[质量闸](#gates)。组件要「去某处」或「读当前位置」时，按轻重从这三样里挑：

| 这一颗是 | 用什么 |
|---|---|
| 本身就是一个去处（一行、一张卡、一个页签） | `<NavLink :to="…">` —— `components/common/NavLink.vue` |
| 只画「我在哪」（哪一格是当前页、当前的 spaceId） | `useNavigation()?.route` |
| 点一下就得走、没有父级可问 | `useNavigation()?.navigate(to)` |

**收 props 加 emit 仍然是默认答案。** 父级知道去向的（列表里每行去哪由页面给），组件只该把 `to` 收进来、把点击 emit 出去（`UserRef` 的 `navigate` 就是这样）；这个 composable 是给「没有父级可问」的那一类，比如一整行的链接、页签条、底部动作面板。

`composables/useNavigation.ts` 读的是**应用**上的 `$router` / `$route`（`app.use(router)` 把它们挂在 `appContext.config.globalProperties` 上），拿不到就整个返回 `null` —— 没有路由的树里同一颗组件照样渲染，少的只是「去处」，而且不报警告。模板那一半是 `NavLink`：它画的是真 `<a>`，有路由才有 `href`，没有就不给（一个看起来能点、按下去什么都不发生的东西，比不画更糟）。`to` 的类型取 `lib/navTarget.ts` 的 `NavTarget`，不要从 `vue-router` 取 `RouteLocationRaw` —— 那一个 `import type` 同样算违规。

为什么是注入而不是 props：见上面「收 props 加 emit 仍然是默认答案」一段。

## 房间：一列对话，一块工作面板 {#room}

房间是 `views/workspace/` 那一棵：`ProjectShell.vue` 是项目壳（左栏 `ProjectSidebar`），频道在 `TopicView.vue` —— 一个频道头，然后 `TopicChatColumn.vue`（左）和 `WorkPanel.vue`（右）分栏。手机上两栏放不下，对话变成工作面板那条 tab 栏的第一格。

`ProjectShell` 按 topicId 给 `TopicView` 打 key，所以**每个频道一个全新实例**，下面（或任何子组件里）不会有上一个频道的状态被带过来。

工作面板开在哪个页签写进 URL（`?tab=`），所以「你来看一眼这个 diff」是一条能发出去的链接。地址是页面的事，不是面板的：面板只报告自己动了，页面负责写回 query。

面板并排还是浮层、开着还是收着，由 `composables/useTopicPanel.ts` 按**主区**宽度（窗口减去侧栏）定，三档的分界在 `useWorkspaceLayout.ts`：至少 `PANEL_OPEN_MIN`（1000）并排、默认开；`PANEL_DOCK_MIN`（840）到它之间并排、默认收；更窄浮在对话上方、默认收。页头的「概览」（`room/PanelToggle.vue`）开合它；并排时这个人的选择记在 store 的 `panelPref`，浮层不记，只写或清 `?tab=`。地址里有 `?tab=` 时面板总是开着。

## 对话的行管线 {#rows}

聊天区一行一行的东西有**三种**：一条消息、一条还没落库的消息（同一个组件的 pending 档）、以及「芝士正在做」那一行（`ChatPanel` 自己画的）。它们的几何只有一份，写在 `components/room/room-row.css` 里，三个组件各自 `<style scoped src>` 引进去 —— 复制三份的话，改了其中一处的留白，另外两行就会错位。

`ChatPanel.vue` 里 `rows` 这个 computed 是唯一的行管线：

```
messages → coalesceSplitFencedCodeBlocks → collapseNotices → 渲染
```

- `coalesceSplitFencedCodeBlocks` 先把被拆成连续几行的围栏代码块拼回来，**再**交给折叠。
- `collapseNotices`（`lib/platformNotice.ts`）决定平台自己说的话怎么渲染：连续的同类事件折成一行，长文进 `<details>`。硬约束是「信息不能丢，只能收起来」—— 没有任何一档会把原文扔掉，折成一行时每一次的原文都还在展开区里。

平台事件的契约是**后端只发码、文案在前端**：`kind=event` 的 block 带 `meta`（`event_type`、`severity`、`who`、`detail`、`detail_label`…），正文只有一行 ≤40 字的人话。`meta.in_room` 单独一格管「露不露面」，缺省是露面 —— 它过去和「谁写的」挤在 `author_type` 一个字段里（system 出现、ai 不出现），于是「确实由芝士产生、又该让人看见的事件」无法表达。对消息同样生效：一条通篇没有中文的 AI 消息照常落库、照常在历史里，只是聊天区不显示。

多个队友并行跑时，工作面板的「现场」页签标签上带名字和跳动的点，见[同一频道里的几个 AI 队友](/dev/turn#seats-ui)。

## 工作面板与页签 {#workpanel}

`WorkPanel.vue` 只负责两件事：**哪一页签在屏幕上、到底有哪几页签**；以及那些「必须一直对、不随页签切走」的信号。一页签渲染和取的东西都属于它自己那个 SFC。唯一的跨页签线是 `open-file`：文档里（或聊天里）一个 `<&path>` chip 打开那个文件 —— 房间文件里能画的走自己的页签；在频道里，其余的开一格只读的文件页签（`ProjectFileTab` → `useProjectFile` → `panels/ProjectFileView`），读项目当前版本（项目没有仓库、或仓库里没有这一份时，读频道自己的那一份），带行号就滚到那几行；在任务里走「改动」，读这件任务的版本。「改动」只看这一件任务：别的任务在它们自己的页面上，不在改动里的文件从「打开其他文件」按名字找。

固定页签按页面分（`WorkPanel.vue` 的 `CHANNEL_TABS`）：频道是 `overview`（概览）、`threads`（支线）、`routines`（例行事务）；任务是 `overview`、`site`（现场）、`changes`（改动）、`preview`（预览）。手机上两种页面的第一格都是 `chat`（对话）。旧地址里的 `?tab=doc` / `?tab=tasks` 都并进概览了。另有一类**文件页签**，键是 `file:<路径>`，一份文件一个，「你看一下这个文件」因此也是一条能发的链接。

总览两边各是一个组件：频道是 `components/channel/ChannelOverview.vue`（频道说明，「综合」换成项目目标的 `PanelDoc`；置顶 `ChannelOverviewPins`；进行中；最近关闭），任务是 `components/task/TaskOverview.vue`（开始信息、任务文档的 `PanelDoc`、这一轮的清单、`TaskOutputs` 产出、`TaskRelatedList` 相关）。其余页签的内容在 `components/panels/` 下：`PanelSite`（现场，`SiteRecordTree` / `SiteRecordRow`；顶上那一行是和输入框上方同一个 `room/MemberActivity`）、`PanelChanges`（改动，没绑仓库时 `PanelChanges.noRepo`）、`PanelPreview`（预览，含文档与媒体）、`PanelCard`（共用外壳）。

## 空间页 {#space}

空间的每一页都挂在 `/spaces/:spaceId/…` 下（`router/spaces.ts`），左边是 `SpaceSidebar.vue`：顶上一行「‹ 空间名」回到首页，下面依次是公告、题目（全部与各分类），以及只对所有者和管理员露出的「管理」一段（待审核、成员、数据、设置）。管理页都在 `manage/…` 下；设置是一页分栏，每一栏是 `manage/settings` 的一条子路由。题目列表的「全部 / 我参与的 / 我发布的」写在地址的 `filter` 里，「我发布的」读 `GET /spaces/{id}/me/publishing/tasks`，因为通用列表不给出题人自己还没过审的题。每一页自己画一行和侧栏顶等高的 `PageHeader`。

角色只有一处来源：`stores/space.ts` 的 `myRole` / `isManager` / `isOwner`，拿登录的人跟 `space.admins` 对出来。界面按它决定露不露管理入口，真正把关的是接口。

任务列表的列是怎么推出来的见[看板](/dev/boards#board)，待处理清单见[任务与工作目录](/dev/tasks#awaiting)。

## 状态与请求 {#state-network}

请求层只有一套：`src/api/`。写代码时从 `@/api` 拿，或者直接引 `src/api/<域>.ts`。

这一栏以前讲的是「并存的两套栈」，现在那句已经不成立（2026-10-12 收尾，见下）。

| 谁 | 走哪条传输 |
|---|---|
| `@/api` 的绝大多数调用方，以及 `src/api/**` 下按域切的模块 | `src/api/http.ts` 的 `request<T>`：原生 `fetch`，调用方自己剥 `{code,message,data}`，GET 套读预算，401 在请求里重试一次 |
| `src/api/spaces.ts` | `src/api/axiosHttp.ts`：axios 实例 + 拦截器。它整份域还是 pilot 时留下的 axios 版，为的是上传进度、超时和刷新重放；统一掉这段策略**不在本线范围**，见文件头的注释 |
| 连接器的 `/connector/*` | `src/api/connector.ts` 的 `connectorRequest`：同一形状的镜像，不套信封、只留 Bearer |

两条传输共用同一套上层：`ApiError`（`src/api/http.ts`）、`lib/session.ts` 的刷新、`ErrorHandler` 的拒绝处理器、`axiosErrorMessage` 的文案兜底。

`network/` 这个目录已经不存在了，别再往回引：ESLint 里有一条 `no-restricted-imports` 专门拦 `@/network`（`eslint.config.mjs`，报文里指到 `@/api`），棘轮的 `legacy-network` 那本账也从 140 条收到 0，只能减不能加。

`stores/` 是 pinia：`workspace.ts`（项目与频道）、`feedback.ts`（反馈，含缓存、分页、陈旧判断）、`space.ts`、`discussionStore.ts`、`navigation.ts`、`signup.ts`、`title.ts`。房间 chrome、聊天和工作面板挂载时会各自请求同一份名册/任务摘要，所以 `api.ts` 里有一个 `pendingRoomReads` 表**只共享在飞行中的读**：写请求（任何非 GET）一进来就整表清空，下一次读一定打到服务器。

## 请求的信封、重试、401 与预算 {#api}

`src/api/http.ts` 的 `request<T>` 是 GET 才套预算，非 GET 直接进 `performRequest`：

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

两边的键一一对应：`zh-CN` 的每个键在 `en` 里都有非空的英文，每个键都有源码引用，没有例外清单。缺译文就补上，没人用就删掉。语言名和切换标签是母语写法（每种语言里都是同一个字符串），所以它们根本不住在目录里，而是 `languages.ts` 的常量：于是「英文目录里出现 CJK 是正确的」这件事没有豁免可躲。

目录之外，`pnpm run lint:i18n` 扫 `src/` 里写死的中文，一行也不放过；只有被比较、被解析或被存进数据的中文可以留下，并且要在同一行写 `// i18n-data: 理由`（模板里是 `<!-- i18n-data: 理由 -->`）。注释不扫；但模板 HTML 注释在 vitest 和 `vite dev` 里会留在 DOM 上，带汉字的会撞上检查 DOM 的测试，所以模板注释写英文。规则全文在 `docs/i18n.md` §4、§5。

`fallbackLocale` 是 `zh-CN`：少一个 key 退化成可读的中文而不是原始 key；这个回退在运行时是**静默的**，保证不缺 key 的是 `catalog.spec.ts`。开发构建对每一次缺失和回退都告警。

## 质量闸 {#gates}

机器闸全部**只读**（唯一写出去的是 `src/api/openapi.d.ts` 这份生成物，它不进仓库；能改源码的版本是另一个脚本，见下），除最后一道外都跑在 `.github/workflows/frontend.yml`：

| 闸门 | 命令 | 拦什么 |
|---|---|---|
| ESLint | `pnpm run lint`（`task fe:lint:check`） | 代码问题；故意不传 `--fix` —— 会重写工作区的闸门可以在它偷偷修好的违规上退出 0 |
| 组件边界 | `pnpm run lint:boundary` | `src/components/**` 里任何一处 `@/api`、`@/services/*`、`vue-router` 导入（取数那一半只看值导入，`vue-router` 那一半 `import type` 也算，这条非对称判据见 `frontend/scripts/import-boundary-ratchet-core.mjs`）；四条组件原则见 `.claude/rules/architecture.md` |
| 浏览器存储 | `pnpm run lint:storage` | `src/` 里任何一处直接读 `localStorage` / `sessionStorage`（含 `window.localStorage`、`window['localStorage']`、从 `window` 解构出来再用的写法）。它们会抛：Safari 无痕、配额满、根本没有浏览器上下文——一次抛在 `setup()` 里就连带整个渲染。唯一允许碰这两个全局的是 `src/lib/browserStorage.ts`（`storageGet` / `storageSet` / `storageRemove` / `storageGetJson` / `storageSetJson` / `storageGetStrings` / `storageKeys` / `storageAvailable`，都不抛），规则与报文见 `frontend/scripts/browser-storage-ratchet-core.mjs`；测试文件、`src/test/**` 和该模块本身除外。`@vueuse/core` 的 `useStorage` 不算违规 |
| 无障碍 | `pnpm run lint`（同上面 ESLint 那一道） | 所有 `.vue` 模板里的 `eslint-plugin-vuejs-accessibility`（`flat/recommended`，在 `eslint.config.mjs` 里，直接报错、没有基线；只改了一处：`label-has-for` 认「包住控件」或「`for` 指向控件」任一种）违规：能点却没有键盘路径的元素、没绑名字的表单控件和 label、没有 alt 的图片、写错或缺必需属性的 ARIA。它只读静态模板，焦点顺序、对话框的焦点管理、对比度都看不到 |
| 全局回退 | `pnpm run lint:global-fallback` | 组件里对某个名字的裸调用（`foo()`），而树的某个模块自己声明了同一个名字却没把它交出去——两半都读：`<script setup>` 里落的全局引用，以及模板里的调用。模板里的名字不在任何作用域里，`eslint` 的配置又注入了浏览器全局、TS 侧由 `lib.dom` 声明同名成员，所以别的闸门一个都看不见。2026-10-16 #1618 拆 `DesignImage.vue` 时 `blur()` 就是这样没导出、落到 `window.blur` 里一声不响。规则与报文在 `frontend/scripts/global-fallback-ratchet-core.mjs`、`frontend/scripts/check-global-fallback-ratchet.mjs`。**不是棘轮**：落地那天全树 752 个组件、957 个模块 0 命中，没有存量可冻，也就没有 `--update` |
| 设计 token | `pnpm run lint:style` | 写死颜色（hex、颜色名、数值型 `rgb()`/`hsl()`），一处就红；格式、排序、命名不门控，`lint:style:fix` 保留自动修复 |
| 类型 | `pnpm run typecheck` | `vue-tsc --noEmit` 的任何一条报错；跑之前先由 `scripts/typecheck.mjs` 生成 `src/api/openapi.d.ts`，它给后端要一份现造的 OpenAPI 文档（`uv run python -m scripts.export_openapi --stdout`）再拿它生成。**两份都不进仓库**：文档 1.8MB、类型 1.4MB，每改一条路由两份一起重写，任何两个都动路由的分支就会在这儿撞车。代价是这个检查要后端的 Python 环境（uv、解释器、应用依赖），所以 `check` 作业现在也装后端依赖。文档的摘要也不进仓库：只改后端的 PR 跑不跑前端套件，由 Required CI 的 `api` job 定，它在基点和这次改动上各造一份文档逐字节比较，变了就跑（见 [CI 设计](/dev/ci#required)） |
| 棘轮自己的单测 | `pnpm run test:ratchet` | `scripts/*.test.mjs`（node:test 地盘，不是 vitest 的）。要跑几分钟，本地只在改了 `scripts/`、`eslint*.config.mjs` 或某份 `*-baseline.json` 时跑，而且只跑对应的那个文件（`node --test scripts/<名字>.test.mjs`）。CI 在每次前端改动上跑全套，其中几份也读树里别的文件（`src/` 里的 `mdi-` 图标名、`index.html`、`demo.html`、`nginx.conf`、`src/styles/fonts.css`、`src/views/demo/`、`tsconfig.app.json`），所以普通改动也可能在 CI 上让它变红 |
| 渲染对比度 | `pnpm --dir e2e exec playwright test contrast-ratchet` | 公开页（`/`、`/solutions`、`/download`）两个主题下**渲染出来**的每一段文字和每一个图标字形，任何一处对比度不足（正文 4.5:1，大字与图标字形 3:1），以及任何量不出来的实例——一个选择器下既有能测的段落、又有压在渐变上的段落时，两边都报，不会因为前者过线就把后者丢掉。量的是画出来的东西，所以继承色、半透明叠色、`::before` 画的字形都算在内——这三样正是源码里看不出来的那部分，也是这道闸门立在 `fe-` 其他颜色闸门之外的原因（`.mjs` 头里写了为什么不用 axe-core）。规则、判定和报文在 `frontend/scripts/contrast-ratchet-core.mjs`，没有基线。它跑在 e2e 那套里：量对比度要一个真浏览器，而 Playwright 在 `e2e/`，所以命令与其他闸门不同，见 `e2e/tests/contrast-ratchet.spec.ts` |

组件边界、浏览器存储、类型、渲染对比度、无障碍、设计 token 都没有基线，有一处就红。读 eslint 的 JSON 报告这件事由 `frontend/scripts/eslint-ratchet-json.mjs` 一处做（按规则 id 计数、把绝对路径变成相对路径、把解析不了的文件单列为「判不了」），边界、存储两道都从这里取。脚本都显式解析 `node_modules/.bin` 下的二进制而不是信 PATH：**一个只是缺失的 vue-tsc / stylelint 不能长得像一次干净的检查**；一个非零退出但解析不出任何诊断，是崩溃而不是「零违规」。

仓库根还有一道：调色板（`color="grey-*"`、`bg-white` 这类固定色），在 `.claude/scripts/check-repo-rules.sh`，没有基线，一处就红。`docs/design-system.md` §7 列了没有闸门、只能靠 review 的部分（圆角、排版、文案、动效）。

## 测试约定 {#tests}

- 单测是 vitest，`environment: 'happy-dom'`，setup 文件 `src/test/setup-network.ts` —— 它把没被 mock 的 `fetch` 直接判失败（「Mock every HTTP request used by this test」，MSW 的拦截在它之上），跟 mock 哪个请求栈无关；CI 跑 `vitest run --dir src`。
- 测试文件与源文件同目录、同名前缀（`ChatPanel.mentionMenu.spec.ts`、`WorkPanel.tabs.spec.ts`），一个行为一个文件。
- `@/api` 会被 `vi.mock` 掉，所以请求栈本身的测试（`apiRetry.spec.ts`、`api401Retry.spec.ts`、`apiTokenRefresh.spec.ts`、`apiEdgeErrorPage.spec.ts`）单独摆一份。
- `scripts/` 下的是 `node --test` 的地盘（`pnpm run test:ratchet`），不是 vitest 的：它们匹配 vitest 默认的 include glob，混进去会让整个 run 以 "No test suite found" 挂掉。
- 端到端是 Playwright（`e2e/`），它自己在 `e2e/playwright.config.ts` 里复现 `/api` 前缀的剥法。

## 边界与坑 {#traps}

- **请求层只剩一套，`@/api`；底座是两条。** `src/api/spaces.ts` 走 axios（`axiosHttp.ts`），其余域模块走 `fetch`（`http.ts`）。改一个接口先看它落在哪个模块：axios 那条有上传进度和刷新后重放，fetch 那条的 GET 有读预算和 502–504/520–530 重试，两者的失败形状也不同。
- **`axiosErrorMessage` 只认 `ApiError`，别的错误一律返回调用方给的 fallback。** 它以前还认旧栈的 `BusinessError`，那段已经删了。要按状态码分辨（例如把 409 和别的分开展示），读 `ApiError.status`，不要对 message 做子串匹配。
- **后端的错误体几乎都写着 `retryable: false`，所以几乎都不重试。** `api/http.ts` 的 GET 只在状态码是 502–504、520–530 且 `error.retryable !== false` 时重试。后端把它设成 `true` 的只有 `ForgeRateLimitedError`、`ForgeUnreachableError`（`backend/app/domain/project/forge.py`，都是 503）和限流的 429（`app/core/request_limits.py`，429 不在上面的状态码里）。结果是：后端自己答出的 503（比如 `GatewayUnavailableError`）不重试，会重试的只有边缘返回的非 JSON 页面、没有错误体的 5xx 和这两类代码仓库错误。想让一类错误被重试，在它的错误类上设 `retryable = True`。
- **`App.vue` 之外还有第二个入口。** 改全局样式或插件时，`demo-main.ts` 那条路只装了 vuetify 和 i18n：依赖路由、登录态或任何 store 的东西在那里都没有，而文档的每次构建都会跑到它。
- **`NotFound` 通配必须最后。** 前面插一条落不进任何 match 的路由，被它吃掉的表现是「页面打不开」，看起来像后端 404。
- **折起来的必须还在。** `collapseNotices` 的硬约束是「信息不能丢，只能收起来」；新增一档折叠时，原文必须仍然在展开区里，不能只留一行摘要。
- **`meta.in_room` 缺省是露面。** 不写这一格的事件会进聊天区 —— 想让机器自己的输出不占屏，必须显式写 `false`，而不是指望某个作者的默认。
- **棘轮基线只能减。** 用 `--update` 降基线，不要为了让 PR 过而手工往 `*-baseline.json` 里加条目；加进去的那一条永远不会自己消失。
