---
title: 场景清单
kind: 参考
summary: 前端每一个「场景」——路由页和工作面板——今天能不能脱离后端、路由和 store 单独渲染，一张表列清哪些已经可以、哪些卡在哪一步、一共多少个，以及下一步先动哪一批。
covers:
  - .claude/scripts/arch-metrics.py
  - frontend/src/components/panels/
  - frontend/src/views/demo/catalog.ts
  - frontend/src/views/demo/catalogFixtures.ts
---

# 场景清单 {#scenes}

组件预览站（`/demo/catalog`）能做什么，取决于**谁被注册进去了**；而这个集合是攒出来的，没人知道还剩多少、剩下的差在哪。这一页把它翻过来数一遍：前端一共有多少个能叫「场景」的东西，几个今天就能脱离后端单独跑一遍，剩下的各卡在哪一步。

> 讲：每个路由页和工作面板今天站在哪一档、卡住的原因是什么、一共多少个、下一步先动哪一批。不讲：怎么往目录里加一个组件（三步写在仓库内的 `frontend/AGENTS.md`），A/B/C/D 的判据和怎么跑（见[架构指标](/dev/arch-metrics#metrics)），组件边界那三道闸本身（见[前端结构](/dev/frontend#gates)）。

## 「单独渲染」是什么意思 {#standalone}

**给一组 props，它就能出画面**：不要后端（不 import `@/api`、`@/network`、`@/services/*`，也不经别的模块绕过去），不要路由（不读 `useRoute` / `$route`），不要任何会自己去取数的 store。判据就是[架构指标](/dev/arch-metrics#metrics)那套 A/B/C/D：

| 档 | 什么情况 | 要补什么才能单独跑 |
|---|---|---|
| **A** | 只吃 props 和事件 | 不用补，只差一份形状数据 |
| **B** | 只读应用外壳的 store（`usePageTitleStore`、`useNavigationStore`） | 在外壳里装一个空的同名 store，或者把那两个读点改成 props |
| **C** | 自己取数——直接 import、经中间模块绕、自己写 `fetch`/`axios`——或者读业务 store | 把取数那一段挪出去：外面拿数据，它只收结果（`PanelDoc` → `usePanelDoc.ts` → `PanelDocView` 就是这条路） |
| **D** | 还绑在挂载位置上：读路由、`$parent` / `$root`、事件总线、`provide` / `inject` | 先把「从哪来」改成 props 或事件，再谈数据 |

## 一共多少 {#totals}

2026-09-29 在 `feat/catalog-panel-scenes`（`main@e4e0538d`）上数出来的：**129 个路由页 + 27 个工作面板 SFC**，其中 **19 个（12%）今天就能单独渲染**；目录里现在有 20 项：16 项是更小的零件（`components/` 下的卡片、条、页签、图表），4 项是工作面板（`panel-tabs` 加上这一版挂上的三个视图），**一个路由页都没有**。

| 场景 | 一共 | A 今天就能单独跑 | C 卡在取数 | D 卡在路由 / `$parent` / `inject` |
|---|---|---|---|---|
| 路由页（`src/views/`，router 表里挂上去的每一页） | 129 | 7 | 34 | 88 |
| 工作面板（`src/components/panels/` 下的 SFC） | 27 | 12 | 15 | 0 |

一个场景可以同时踩中好几条（100 页直接 import 了取数模块，86 页读路由），**档位取最差的那条**——所以 D 那一列不是「只差一个路由」。

按目录看那 129 个页面：

| 目录 | 一共 | A | C | D |
|---|---|---|---|---|
| `views（顶层）` | 19 | 1 | 6 | 12 |
| `views/account/` | 17 | 0 | 1 | 16 |
| `views/admin/` | 8 | 0 | 3 | 5 |
| `views/feedback/` | 5 | 1 | 1 | 3 |
| `views/home/` | 4 | 2 | 1 | 1 |
| `views/legal/` | 1 | 0 | 0 | 1 |
| `views/question/` | 4 | 1 | 0 | 3 |
| `views/spaces/` | 43 | 1 | 12 | 30 |
| `views/tasks/` | 7 | 0 | 3 | 4 |
| `views/teams/` | 9 | 0 | 3 | 6 |
| `views/user/` | 5 | 1 | 4 | 0 |
| `views/workspace/` | 7 | 0 | 0 | 7 |

今天就能单独跑的那 7 页：`views/404.vue`、`views/home/Landing.vue`、`views/home/Solutions.vue`、`views/spaces/detail/analytics/Index.vue`、`views/feedback/AdminFeedbackPage.vue`、`views/user/settings/General.vue`、`views/question/DetailAnswerList.vue`。

## 页面 {#pages}

`src/views/` 下被 `src/router/` 挂上去的每一页。「卡在哪」列的是最差的那一条，同一页可能还踩了别的。

| 页面 | 档 | 卡在哪 |
|---|---|---|
| `views/404.vue` | A | 只吃 props 和事件 |
| `views/CalendarView.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/ConnectView.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/InboxView.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/MarketView.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/MyArchivedProjectsView.vue` | C | 直接取数（`api.ts` / `network`）；读 store（workspace） |
| `views/MyConnectionsView.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/MyDevicesView.vue` | C | 直接取数（`api.ts` / `network`）；经 `lib/desktop.ts` 取数 |
| `views/PreviewOpenView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；经 `lib/previewSession.ts` 取数 |
| `views/ProfileView.vue` | C | 经 `composables/useChosenAvatar.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/ProjectArtifactView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（pageTitle） |
| `views/ProjectDocsView.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/ProjectLibraryView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；经 `lib/libraryApi.ts` 取数；读 store（pageTitle） |
| `views/ProjectRoutinesView.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/ProjectSearchView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；经 `views/workspace/search/docs.palette.ts` 取数；经 `views/workspace/search/library.palette.ts` 取数；经 `views/workspace/search/messages.palette.ts` 取数；经 `views/workspace/search/projectDocs.palette.ts` 取数；经 `views/workspace/search/tasks.palette.ts` 取数 |
| `views/ProjectSettingsView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；经 `utils/sudo.ts` 取数；读 store（workspace） |
| `views/ProjectSkillsView.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/SiteOpenView.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/TeamInviteView.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/AddEmail.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/AppSignInFinish.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/AppSignInStart.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/BackToApp.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/OAuthComplete.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/OAuthError.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/OAuthSuccess.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/OAuthVerify.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/PasskeyOffer.vue` | D | 读路由；经 `utils/sudo.ts` 取数；经 `views/account/passkeyEnrollment.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/account/SignIn.vue` | D | 读路由；经 `views/account/passkeyEnrollment.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/account/Verify2FA.vue` | D | 读路由；经 `views/account/passkeyEnrollment.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/account/emailCode/Request.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/emailCode/Verify.vue` | D | 读路由；经 `views/account/passkeyEnrollment.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/account/recover/password/Start.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/account/recover/password/Verify.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/account/signup/Start.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（signup） |
| `views/account/signup/VerifyEmail.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（signup） |
| `views/admin/AdminDashboardPage.vue` | D | 读路由；读 store（feedback） |
| `views/admin/AdminFeaturePage.vue` | D | 读路由；经 `views/admin/features/registry.ts` 取数 |
| `views/admin/AdminFeatureStatsPage.vue` | D | 读路由；经 `views/admin/features/featureApi.ts` 取数；经 `views/admin/features/registry.ts` 取数 |
| `views/admin/AdminLayout.vue` | D | 读路由；读 store（feedback） |
| `views/admin/AdminMembersPage.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/admin/AdminModelsPage.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/admin/AdminQueuePage.vue` | D | 读路由；读 store（feedback） |
| `views/admin/AdminSpacesPage.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/feedback/AdminFeedbackPage.vue` | A | 只吃 props 和事件 |
| `views/feedback/FeedbackCenterPage.vue` | C | 读 store（feedback） |
| `views/feedback/FeedbackDetailPage.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（feedback） |
| `views/feedback/FeedbackMinePage.vue` | D | 读路由；读 store（feedback） |
| `views/feedback/FeedbackSubmitPage.vue` | D | 读路由；读 store（feedback） |
| `views/home/Download.vue` | C | 经 `lib/desktop.ts` 取数；经 `lib/desktopChangelog.ts` 取数 |
| `views/home/Landing.vue` | A | 只吃 props 和事件 |
| `views/home/MyWork.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（workspace） |
| `views/home/Solutions.vue` | A | 只吃 props 和事件 |
| `views/legal/LegalDocumentView.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/question/Ask.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/question/Detail.vue` | D | 读路由；provide()；直接取数（`api.ts` / `network`） |
| `views/question/DetailAnswer.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/question/DetailAnswerList.vue` | A | 只吃 props 和事件 |
| `views/spaces/Detail.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/Index.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（workspace） |
| `views/spaces/JoinCourse.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/board/SpaceBoardShell.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数 |
| `views/spaces/board/pages/Analytics.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/board/pages/Announcements.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/board/pages/BoardHome.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数；读 store（space） |
| `views/spaces/board/pages/Members.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/spaces/board/pages/Mine.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/spaces/board/pages/Review.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数 |
| `views/spaces/board/pages/TaskDetail.vue` | D | 读路由；provide()；经 `views/spaces/board/store.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/spaces/board/pages/TaskInsights.vue` | D | 读路由；经 `views/spaces/board/store.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/spaces/board/pages/TaskPublish.vue` | D | 读路由；provide()；经 `views/spaces/board/store.ts` 取数；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/course/CourseHome.vue` | C | 直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/course/CourseSettings.vue` | D | 读路由；读 store（space） |
| `views/spaces/course/CourseWork.vue` | C | 直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/course/People.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/course/Quiz.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/course/Team.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/course/Units.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/detail/Announcements.vue` | C | 直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/detail/AuditTask.vue` | C | 直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/detail/CreateDiscussion.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/detail/DiscussionItem.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/detail/Discussions.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/detail/ManageCategories.vue` | C | 读 store（space） |
| `views/spaces/detail/ManageDomainGroups.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/detail/ManageInviteCodes.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/detail/ManageTemplates.vue` | D | 读路由；读 store（space） |
| `views/spaces/detail/ManageTopics.vue` | C | 读 store（space） |
| `views/spaces/detail/PublishTask.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/detail/SelectTemplate.vue` | D | 读路由；读 store（space） |
| `views/spaces/detail/Tasks.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/spaces/detail/TemplateForm.vue` | D | 读路由；读 store（space） |
| `views/spaces/detail/analytics/Alerts.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/spaces/detail/analytics/Index.vue` | A | 只吃 props 和事件 |
| `views/spaces/detail/analytics/Learning.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/spaces/detail/analytics/Overview.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/spaces/detail/analytics/Participants.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/spaces/detail/analytics/Publishers.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/spaces/detail/analytics/Tasks.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/spaces/detail/member-tasks/MyParticipating.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/spaces/detail/member-tasks/MyPublishing.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/tasks/Detail.vue` | D | 读路由；读 store（navigation） |
| `views/tasks/Edit.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（space） |
| `views/tasks/detail/AIAdvice.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/tasks/detail/Overview.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/tasks/detail/Participants.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/tasks/detail/Submissions.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/tasks/detail/Submit.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/teams/Detail.vue` | D | 读路由；provide()；直接取数（`api.ts` / `network`） |
| `views/teams/Explore.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/teams/Index.vue` | D | 读路由；直接取数（`api.ts` / `network`） |
| `views/teams/Mine.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/teams/Pending.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/teams/detail/Compute.vue` | D | inject()；直接取数（`api.ts` / `network`） |
| `views/teams/detail/Knowledge.vue` | D | inject()；直接取数（`api.ts` / `network`） |
| `views/teams/detail/Members.vue` | D | 读路由；inject()；直接取数（`api.ts` / `network`） |
| `views/teams/detail/TeamProjects.vue` | D | 读路由；inject()；直接取数（`api.ts` / `network`） |
| `views/user/settings/General.vue` | A | 只吃 props 和事件 |
| `views/user/settings/Profile.vue` | C | 经 `composables/useChosenAvatar.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/user/settings/RealName.vue` | C | 经 `utils/sudo.ts` 取数；经 `composables/useChosenAvatar.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/user/settings/Security.vue` | C | 经 `utils/sudo.ts` 取数；直接取数（`api.ts` / `network`） |
| `views/user/settings/SettingsSidebar.vue` | C | 直接取数（`api.ts` / `network`） |
| `views/workspace/DmView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（pageTitle, workspace） |
| `views/workspace/ProjectMembersView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（workspace） |
| `views/workspace/ProjectShell.vue` | D | 读路由；读 store（pageTitle, workspace） |
| `views/workspace/ProjectSidebar.vue` | D | 读路由；经 `lib/routePrefetch.ts` 取数；读 store（workspace） |
| `views/workspace/RunningWorkView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（workspace） |
| `views/workspace/TopicView.vue` | D | 读路由；直接取数（`api.ts` / `network`）；读 store（workspace） |
| `views/workspace/WorkspaceEntry.vue` | D | 读路由；读 store（workspace） |

## 工作面板 {#panels}

`src/components/panels/` 下的 27 个 SFC，也就是房间那一块工作面板和它五个页签的内容。

| 面板 | 档 | 卡在哪 |
|---|---|---|
| `components/panels/ChangesFileTree.vue` | A | 只吃 props 和事件 |
| `components/panels/PanelCard.vue` | C | 直接取数（`api.ts` / `network`）；经 `components/room/composables/useRoomSocket.ts` 取数 |
| `components/panels/PanelChanges.vue` | C | 经 `composables/usePanelChanges.ts` 取数 |
| `components/panels/PanelChangesView.vue` | A | 只吃 props 和事件 |
| `components/panels/PanelDoc.vue` | C | 经 `composables/usePanelDoc.ts` 取数 |
| `components/panels/PanelDocView.vue` | A | 只吃 props 和事件 |
| `components/panels/PanelOverview.vue` | A | 只吃 props 和事件 |
| `components/panels/PanelPreview.vue` | C | 经 `composables/usePanelPreview.ts` 取数 |
| `components/panels/PanelPreviewView.vue` | C | 经 `lib/previewSession.ts` 取数 |
| `components/panels/PanelProgress.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/PanelSite.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/PanelTabs.vue` | A | 只吃 props 和事件 |
| `components/panels/SiteStatusBar.vue` | A | 只吃 props 和事件 |
| `components/panels/SiteStepOutput.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/TaskProgress.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/TodoChecklist.vue` | A | 只吃 props 和事件 |
| `components/panels/doc/DocComments.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/doc/DocOverlays.vue` | A | 只吃 props 和事件 |
| `components/panels/doc/DocSlashMenu.vue` | A | 只吃 props 和事件 |
| `components/panels/doc/DocSurface.vue` | A | 只吃 props 和事件 |
| `components/panels/doc/OverviewAuto.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/preview/PreviewPages.vue` | A | 只吃 props 和事件 |
| `components/panels/preview/PreviewSheet.vue` | A | 只吃 props 和事件 |
| `components/panels/preview/RevisionList.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/preview/RoomFileEditor.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/preview/RoomFileHistory.vue` | C | 直接取数（`api.ts` / `network`） |
| `components/panels/preview/RoomOutputs.vue` | C | 直接取数（`api.ts` / `network`） |

**三个外壳正在被拆空。** `PanelDoc` / `PanelChanges` / `PanelPreview` 今天都是 C，但它们是「页签的外壳」：取数在 `composables/usePanelDoc.ts`、`usePanelChanges.ts`、`usePanelPreview.ts` 里，外壳把结果整理成 props 交给视图。三个视图（`PanelDocView`、`PanelChangesView`、`PanelPreviewView`）因此都是 A，这一版的目录里已经能单独跑——**拆的方向就是把 C 留在外壳上、把 A 攒出来**。

## 卡在哪一步 {#blockers}

129 个页面里，每条原因各占多少页（一页可以算多笔）：

| 原因 | 页数 | 分成 |
|---|---|---|
| 直接 import 取数模块（`@/api`、`@/network`、`@/services/*`） | 100 | |
| 读路由（`useRoute` / `$route`） | 86 | |
| 读业务 store | 43 | `space` 20、`workspace` 11、`feedback` 7、`pageTitle` 4、`signup` 2、`navigation` 1 |
| 经中间模块绕到取数 | 26 | `views/spaces/board/store.ts` 9、`utils/sudo.ts` 4、`views/account/passkeyEnrollment.ts` 4、`composables/useChosenAvatar.ts` 3、`lib/desktop.ts` 2、`views/admin/features/registry.ts` 2、其余 7 个各 1 |
| `provide()` / `inject()` | 各 4 | |

前两条就是大头，而且它们的修法不一样：

- **直接取数（100 页）** 是「把这一段挪出去」的问题，机械，但要一页一页做。`usePanelDoc.ts` 那条路（外壳取数、视图收 props）是可复制的样板。
- **读路由（86 页）** 是「这个页面怎么知道自己是谁」的问题。`views/spaces/board/` 已经有一套答案（命名路由 + `loadBoard(spaceId)` 在守卫里），`/spaces/:id/board` 那棵树 43 页里 12 页 C、30 页 D，是最大的一块。
- **`views/account/`（17 页里 16 页 D）** 和 **`views/workspace/`（7 页全 D）** 是两块最硬的骨头。前者的输入几乎就是地址里的参数——OAuth 回调码、邀请令牌、验证码——所以先要把这几个参数从 `useRoute` 挪成 props，才谈得上后面的取数。后者 7 页每一页都是「读路由 + 读 workspace store」，`ProjectShell` 那一页本身就是外壳，动它是动整个 /workspace 那棵路由。

## 这份清单怎么来的 {#how}

数字不是手数的：`.claude/scripts/arch-metrics.py` 里的 `grade_frontend` 逐文件跑一遍，取它的等级和理由；页面清单取自 `src/router/` 的 import 图。看板上的 A/B/C/D 是**全部 434 个 `.vue`** 的口径，这里只切出「场景」这一层（路由页 + 面板），所以和看板对不上是正常的。

两个已知偏差写在这里，免得被当成事实用：

- **`import type` 那条边在传递链里还会数。** 规约说 `import type` 不算（构建时就被抹掉，见[架构指标](/dev/arch-metrics#metrics)），`_api_reach` 建图时也确实跳过它；但 `lib/previewSession.ts` 整个文件只有一句 `import type { PreviewSession } from '../api'`，它到 `api.ts` 的这条边仍然进了传递闭包，于是 `PanelPreviewView` 被判成 C——它自己一行取数代码都没有。C 那 34 页里可能混着几个这样的假 C，落地时逐页看一眼。
- **正则不是编译器。** 解析不出来的模块说明符当成外部依赖，不当成一条边，所以真隔着一条解析不出来的链在取数的组件会被判高一档（A 或 B）。

## 下一步 {#next}

1. **先把已经拆出来的三个视图挂上目录**：`PanelChangesView`、`PanelPreviewView`、`PanelDocView`，每个带 loading / 空 / 有数据 / 出错几种状态。这一版已经做了。
2. **A 档的先补目录**，成本几乎为零：7 个页面，加 12 个 A 档面板里还没挂的那 9 个——`ChangesFileTree`、`PanelOverview`、`SiteStatusBar`、`TodoChecklist`、`doc/DocOverlays`、`doc/DocSlashMenu`、`doc/DocSurface`、`preview/PreviewPages`、`preview/PreviewSheet`（另外三个就是这一版挂上的视图，`PanelTabs` 本来就在目录里）。挂上去之后，改外观和改排版就有地方看效果。
3. **C 档按「外壳 / 内容」拆**：取数留在外层 composable，视图只收 props，一次一个页签。
4. **D 档要单独排**，不是一页一页能拆完的：读路由那一批要先定「参数从哪进来」。`views/account/`、`views/workspace/`、`views/spaces/` 三块各要一个方案，动哪块由产品定。
5. **这一版不加闸门。** 要不要给「场景必须能单独渲染」上一条棘轮（像组件边界那样只拦新增），等这张表看过再定；真要上，基线就是这一版这一份。
