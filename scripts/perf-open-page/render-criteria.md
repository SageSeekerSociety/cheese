# 页面渲染完成判据（Playwright 用）

仓库：`frontend/src`（Vue 3 + vue-router 4，`<script setup>`，TanStack Vue Query）。
「就绪选择器」= 内容真的画到 DOM 里的稳定选择器；「最小文本」= 该选择器内文本长度门槛（≥ N 字）才判就绪。
「排除骨架屏」= 仍在加载时会出现的东西，命中它就不要判就绪。

统一骨架屏组件：`components/common/LoadingSkeleton.vue`，根类 `.skel`，变体类 `skel--chat/list/roster/entry/site/doc/feedback/detail/text`，带 `role="status" aria-busy="true"`。凡是出现 `.skel`（尤其 `.skel--*`）就是还没画好。
冷启动骨架 `#sx-boot-skeleton`（在 index.html，mount 后移除），判断前可忽略。

多数设置/工作区页的加载态是普通 `v-progress-circular`（类 `.v-progress-circular`），没有专门骨架类——排除时按「该转圈还在」= 未就绪。

---

## 表

| # | 路由 | view 源文件 | 就绪选择器 + 最小文本 / 排除骨架 | onMounted 的 API（串行？） | 先画缓存？ |
|---|------|----|----|----|----|
| 1 | `/inbox` | `views/InboxView.vue` | `.inbox__list`（或行 `.inbox-item`），文本 ≥ 8；空/加载共用 `.inbox__quiet`（内含 `v-progress-circular`）——命中 `.inbox__quiet .v-progress-circular` 即未就绪 | `listAwaitingMe()` → GET `/awaiting-me`；`if(!store.projectsSettled) refreshProjects()` → GET `/projects`（后者不 await，近似并行、条件触发） | 部分：项目清单来自持久化 store（先画），待办是新鲜的 |
| 2 | `/users/settings/devices` | `views/MyDevicesView.vue` | 行 `.device`（名字 `.device__name`），文本 ≥ 4；空态 `.settings-empty`（BaseEmptyState）；排除 `.d-flex.justify-center .v-progress-circular` | `desktop.thisDevice()` → 再 `listMyDevices()` → GET `/my/devices` → 再 `listMyTeams()` → GET `/teams/my-teams`（**串行**：devices 完才取 teams；另有 recheckClaudeCode/recheckSandbox） | 否 |
| 3 | `/users/settings/connections` | `views/MyConnectionsView.vue` | 行 `.conn-row[data-integration]` 或 `.conn-row[data-draft]`，文本 ≥ 4；无专门 body 转圈（loading 只挡空态）；空态 BaseEmptyState | `Promise.all([ listMyIntegrations()→GET /me/integrations, listMyMailDrafts('drafted')→GET /me/mail-drafts?status=drafted, listProjects()→GET /projects ])`（**并行**），随后 `feishuAvailability()`（尾随串行） | 否 |
| 4 | `/users/settings/notifications` | `views/user/settings/Notifications.vue`（渲染半 `NotificationsView.vue` 同目录） | `.notif-matrix`（表格，按钮 `.notif-dot`），文本 ≥ 4；排除 `.settings-card.notif__loading .v-progress-circular` | `getNotificationPreferences()` → GET `/notifications/preferences`（单个） | 否 |
| 5 | `/users/settings/profile` | `views/user/settings/Profile.vue`（渲染半 `ProfileView.vue` 同目录） | 未确认（表单类选择器未读到）；`onMounted` 只 `ensureDefaultAvatarId`，用户来自 AccountService（本页无资料 API） | 无页面级资料请求；账号来自 AccountService | 否 |
| 6 | `/users/:handle` | `views/ProfileView.vue` | `.profile__name`（h1，在 `.profile__grid` 内），文本 ≥ 1；**骨架是同文件本地类**：`.profile__grid[aria-busy="true"]` 内含 `.skel.skel--avatar`/`.skel--line`——按 `aria-busy="true"` 排除 | `Promise.all([ getUserProfile()→GET /users/{handle}/profile, getMemberSummary()→GET /projects/{id}/members/{handle}/summary（带 projectId 时）, getUserTopics()→GET /users/{handle}/topics ])`（**并行**） | **是**：useQuery 缓存优先，先照上一次画再后台重取 |
| 7 | `/projects/:projectId/channels/:topicId` | `views/workspace/TopicView.vue`（左列 `TopicChatColumn` → `components/chat/ChatTimeline.vue`） | 消息行 `.im-row[data-mid]`（文本 `.im-text`），≥ 1 行有文本；骨架 `ChatTimeline` 用 `<LoadingSkeleton variant="chat">` = `.skel.skel--chat`，命中即未就绪 | 壳 `ProjectShell` 取 topics；页面 `openPlace()` 先 `roomRowQuery` 再 `getTask`（**串行**）；历史消息由 `ChatTimeline` 拉取 | 是（useQuery 缓存优先，路径未逐条确认） |
| 8 | `/projects/:projectId/channels/:topicId/threads/:threadId` | 未确认（Thread view 文件名/类未读到） | 未确认 | 未确认 | 未确认 |
| 9 | `/projects/:projectId/tasks/:taskId` | 未确认（task 详情 view 未读到） | 未确认 | 未确认 | 未确认 |
| 10 | `/projects/:projectId/channels` | `views/workspace/ChannelBrowse.vue`（渲染半 `ChannelBrowseView.vue`） | `.browse__row[data-testid="channel-browse-row"]`，文本 ≥ 3；排除加载 `.py-8.text-center[role="status"]`（含 v-progress-circular） | `listChannels(projectId)` → GET `/projects/{projectId}/channels`（`watch(projectId, load, {immediate:true})`） | 否（直接 load，非 useQuery） |
| 11 | `/projects/:projectId/channels/:topicId/files` | `views/workspace/ChannelFiles.vue`（渲染半 `ChannelFilesView.vue`） | 行 `.room-files__row`，文本 ≥ 3；空态 `.room-files__empty`；排除加载 `.py-8.text-center` | `listRoomFiles(topicId)` → GET `/topics/{topicId}/files`（`watch(topicId, load, {immediate:true})`） | 否 |
| 12 | `/projects/:projectId/tasks` | `views/workspace/ProjectTasks.vue`（渲染半 `ProjectTasksView.vue`） | 行 `.tasks__row`（分组 `.tasks__group`），文本 ≥ 3；`.tasks__empty` 同时用于 loading/failed/empty（**注意歧义**：需区分文案或等 open 查询 settled） | open 任务读 `openProjectTasksQuery`（缓存），closed 用 `closedProjectTasksQuery`（useInfiniteQuery，`enabled` 绑「看 closed 栏」） | **是**：open 列表走 query 缓存 |
| 13 | `/projects/:projectId/library` | `views/ProjectLibraryView.vue` | 行 `.library-row`（列表 `.library__list`/`.library__rows`），文本 ≥ 3；加载/空态具体类未确认 | `listProjectLibrary(pid,…)` → GET `/projects/{id}/library…` 与 `listProjectDocuments(pid,cursor,50)`（`useLibraryPages`，跨源 **Promise.all 并行**；PAGE=50） | 是（走 composable/useQuery，缓存优先） |
| 14 | `/projects/:projectId/docs/:kind` | `views/ProjectDocsView.vue` | 按 kind：记忆 `.memory-card`、tabs `.docs-tabs`；文本 ≥ 1；排除加载 `.d-flex.justify-center.py-10 .v-progress-circular` | 每 kind 一个 `useQuery(keys.projectDocs(pid,kind))`，queryFn **串行**：`queryClient.fetchQuery(projectQuery(id))` → 再按 kind `getProjectOverview`/`listMemory`（GET `/memory?project_id=`）/`getProjectWeeklies`（GET `/projects/{id}/weeklies`） | **是**：一个 kind 一份缓存，先画旧再重取 |
| 15 | `/projects/:projectId/artifacts/:artifactId` | `views/ProjectArtifactView.vue` | `.artifact__facts`/`.artifact`（正文 `.artifact__main`），文本 ≥ 4；排除加载 `.py-8.text-center`（`v-if=loading && !artifact`） | `getProjectArtifact(projectId, artifactId)` → GET `/projects/{id}/artifacts/{artifactId}`（`watch` immediate） | 否 |
| 16 | `/projects/:projectId/members` | `views/workspace/ProjectMembersView.vue` | 名册类未逐字确认；用 `provideRevealGate()`，未就绪在 `.reveal-gate--waiting` | `listProjectInvitations`；成员来自 `store.members`；`useQuery(projectQuery)`、`useQuery(agentsQuery)` | 是（store/query 缓存） |
| 17 | `/projects/:projectId/settings/:section` | `views/ProjectSettingsView.vue` | 就绪边框 `.settings-page-body`（页名 `.whose__name`；小节 `.page-section`）；未就绪 `.reveal-gate--waiting`；排除 `.d-flex.justify-center.py-10 .v-progress-circular` | `onMounted`: `consumeGithubCallbackNotice(); load(); loadBranchProtection().finally(gate.hold()); loadGithubAccountConnection().finally(gate.hold())`；各栏 composable 按 `section` 启用取数（**显式 reveal gate**） | 是（useQuery） |
| 18 | `/projects/:projectId/search` | `views/ProjectSearchView.vue`（渲染半 `ProjectSearchViewView.vue`） | 行 `.search-row`（分组 `.search-group`/`.search-list`），文本 ≥ 3；排除加载 `[role="status"]` | `searchProjectCounted(...)` → GET `/projects/{id}/context/search?...&with_counts=true`（`watch([projectId,query,kind], load, {immediate:true})`） | 否 |
| 19 | `/projects/:projectId/routines` | `views/ProjectRoutinesView.vue`（渲染半 `ProjectRoutinesViewView.vue`） | 未确认（行类未逐字确认） | `listTopics(projectId)` → GET `/topics?project_id=`（`watch(projectId,…)`）；列表读 `useRoutineList({projectId})` | 部分（routine list 走 composable/缓存，未确认） |
| 20 | `/projects/:projectId/skills` | `views/ProjectSkillsView.vue` | 行 `.skills-row`（分组 `.skills-group`），文本 ≥ 3；排除加载 `.py-8.text-center[role="status"]` | `Promise.all([ listProjectSkills(projectId), queryClient.fetchQuery(projectQuery(projectId)) ])`（**并行**，`watch` immediate） | 部分（projectQuery 有缓存，skills 新鲜） |
| 21 | `/teams` | 父 `views/teams/Index.vue` → 重定向 `home-teams-mine` → `views/teams/Mine.vue`（渲染半 `MineView.vue`） | 行 `.my-team-item`（列表 `.my-teams-list`），文本 ≥ 3；排除 `.d-flex.flex-column.align-center.py-5 .v-progress-circular` | `await fetchMyTeams()` → `TeamsApi.getMyTeams()` GET `/teams/my-teams`（onMounted） | 否 |
| 22 | `/teams/:handle` | `views/teams/Detail.vue`（默认子 `TeamProjects.vue`） | 卡片 `.project-card`（TeamProjects）；成员/资料见 TeamProfile；排除加载 `div.py-10.text-center`（v-progress-circular） | `TeamsApi.detailByHandle(handle)`（`watch(route.params.handle,{immediate})`）→ 子页 `TeamProjects` 再 `listProjects(teamId)`（**串行**：等 team 取到 teamId 才取项目） | 否 |
| 23 | `/spaces` | `views/spaces/Index.vue` | 卡片 `.space-card`；加载态来自 `components/common/InfiniteScroll.vue`：`.skeleton` 内含 v-progress-circular + 「加载中」文案，命中即未就绪 | 空间列表接口（具体路径未确认） | 否 |
| 24 | `/spaces/:spaceId` | `views/spaces/Detail.vue`（子 `layouts/spaces/SpacesTasks.vue` → `views/spaces/detail/Tasks.vue` + PageHeader） | 未确认（Tasks 页列/卡片类未逐字确认）；`settled` 后才 `<router-view>` | `onMounted: getSpace(Number(spaceId))` → `fetchSpace(spaceId)` 再 `fetchCategories()`（**串行**） | 否 |

「未确认」的行（8、9、13 部分、16、19、23、24 等）我按现有信息只能给到文件名/大致位置，选择器或接口路径没读到确切值——需要再读对应 view 文件补。

---

## 问题 A：登录页（`views/account/SignIn.vue`）

- 用户名输入框：`#signin-username`（`name="username"`，`autocomplete="username webauthn"`）
- 密码输入框：`#signin-password`（PasswordField 组件）
- 提交按钮：`button.account-submit`（`<BaseButton type="submit" class="account-submit">`）
- 登录成功判据：`login()` 成功后调 `router.replace(await landingAfterSignIn(...))`，落到登录后的落地页。落地页在外壳里，且登录页本身 `meta.hideAppBar:true`（不画顶部 rail/bar）。所以**登录后出现、登录页没有的稳定标志**：应用左栏 `.app-rail`（`components/common/Navigation/LeftAppRail.vue` 根类，`v-if="!hideAppBar"`）。等 `document.querySelector('.app-rail')` 出现即视为已登录进外壳。
  - 备选/更严：等路由不再是 `/account/signin`（读上面 B 拿到的 router 的 `currentRoute.value.name !== 'SignIn'`）。
  - 注意 `leaveSignInWhenSignedIn`（router/loginRedirect.ts）做的是反向：已登录访问 `/account/signin` 会被重定向离开。

## 问题 B：`#app.__vue_app__.config.globalProperties.$router` 能不能拿到 Vue Router 实例？

**能，这条路径成立。** 依据：`main.ts` 里 `const app = createApp(App)` → `registerPlugins(app)` → `app.mount('#app')`；`plugins/index.ts` 里 `registerPlugins` 明确执行 `app.use(i18n).use(vuetify).use(router).use(pinia).use(createDialogPlugin)`，即 **`app.use(router)` 确实被调用**。vue-router 4 的 install 会写 `app.config.globalProperties.$router = router`（同时定义 `$route`）；Vue 3 `mount(container)` 会给容器挂 `container.__vue_app__ = app`。所以：

```js
const app = document.querySelector('#app').__vue_app__
const router = app.config.globalProperties.$router   // 就是 frontend/src/router 导出的同一个实例
await router.isReady()
app.config.globalProperties.$route                   // 当前路由（响应式）
```

更稳的替代拿法（不依赖 globalProperties 内部键）：路由实例在 `src/router/index.ts` 里 `createRouter(...)` 后 `export default router`；若脚本里能 import，直接 `import router from '@/router'`。但 Playwright 页面上下文里用上面那段 `__vue_app__.config.globalProperties.$router` 是可用且推荐的。

---

证据均来自只读阅读：`frontend/src/{main.ts,App.vue}`、`frontend/src/plugins/index.ts`、`frontend/src/router/{index.ts,account.ts,workspaceRoutes.ts,home.ts,user.ts,teams.ts,spaces.ts}` 及各 view/组件（未改动任何文件）。
