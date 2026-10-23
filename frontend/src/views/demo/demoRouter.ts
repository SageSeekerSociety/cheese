// 演示页没有路由，但房间里有几个真组件（平台事件行的「去看看」链接）用 router-link。
// 给它们一个什么都不导航的内存路由：链接照样画出来，点了也不离开演示。
//
// 光有通配路由不够：router-link 写的是 { name: 'workspace-overview' }（预览页签上
// 那个「发布网站」），按名字解析的路由在表里找不到就**抛**（不是警告），整个渲染就
// 断在那儿。名字补全，落点还是那个空的通配页 —— 链接画得出来，点不动。
//
// 看板那六格（`AnalyticsNavigationTabs` 的页签，名字见 `spaceRouteNames.ts`）同理：
// 那一排「总览 / 告警 / 出题人 / …」在组件预览站上也得画得出来。
//
// 组件里的链接现在走 `NavLink`，而它对不认识的 name 是**画成一行点不动的字**（不抛、
// 也不警告）：名字缺了不会红，只会让那一颗在预览站上悄悄退化成死的。所以目录里凡是
// 用 name 指路的，名字都在下面这张表里。
import { createMemoryHistory, createRouter } from 'vue-router'

import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'

const blank = { render: () => null }

/** 老树那一套看板名字，各给一条不会被走到的路径（落点是通配页）。 */
const analytics = Object.entries(ANALYTICS_ROUTE_NAMES).map(([key, name]) => ({
  path: `/spaces/:spaceId/manage/analytics/${key}`,
  name,
  component: blank,
}))

/** 目录中按 name 指路的链接都要有对应路由；路径与正式路由一致，才能画出正确的 href。 */
const catalogTargets = [
  { path: '/home', name: 'HomeHub', component: blank },
  { path: '/account/signin', name: 'SignIn', component: blank },
  { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: blank },
  // 空间设置那一层的七条子路由（`settings/Index.vue` 的 `SECTIONS`，名字与路径照
  // `router/spaces.ts` 的 `manage/settings` 子树）。目录左边的链接按这些名字指路。
  { path: '/spaces/:spaceId/manage/settings/basic', name: 'SpacesDetailSettingsBasic', component: blank },
  { path: '/spaces/:spaceId/manage/settings/categories', name: 'SpacesDetailSettingsCategories', component: blank },
  { path: '/spaces/:spaceId/manage/settings/templates', name: 'SpacesDetailSettingsTemplates', component: blank },
  { path: '/spaces/:spaceId/manage/settings/invite-codes', name: 'SpacesDetailSettingsInviteCodes', component: blank },
  {
    path: '/spaces/:spaceId/manage/settings/domain-groups',
    name: 'SpacesDetailSettingsDomainGroups',
    component: blank,
  },
  { path: '/spaces/:spaceId/manage/settings/guidance', name: 'SpacesDetailSettingsGuidance', component: blank },
  { path: '/spaces/:spaceId/manage/settings/materials', name: 'SpacesDetailSettingsMaterials', component: blank },
  { path: '/users/:handle', name: 'UserPage', component: blank },
  { path: '/users/settings/invites', name: 'UserSettingsInvites', component: blank },
  { path: '/users/settings/compute', name: 'UserSettingsCompute', component: blank },
  { path: '/legal/terms', name: 'LegalTerms', component: blank },
  { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
  { path: '/feedback/:id', name: 'FeedbackDetail', component: blank },
  // 反馈中心两页的画面（`catalogFeedback.ts` 的 `FeedbackCenterPageView` / `FeedbackMinePageView`）
  // 页头那颗「提交」按名字指到提交页。它必须在表里：按名字解析的路由找不到就**抛**，
  // 缺了这一条，两页的画面在预览站上整段渲染不出来。
  { path: '/feedback/new', name: 'FeedbackSubmit', component: blank },
  // 首页那份目录（`catalogHome.ts`）：一个团队摊开来是四样，摊开那一行才画得出这四条；
  // 名字不在表里的话，带 `to` 的行会当场抛，目录在预览站上整段渲染不出来。
  { path: '/teams/explore', name: 'HomeTeamsExplore', component: blank },
  // 小队那一页的三条页签（`teams/IndexView.vue`）：三个名字都在，缺一个整页就画不出来。
  { path: '/teams/mine', name: 'HomeTeamsMine', component: blank },
  { path: '/teams/pending', name: 'HomeTeamsPending', component: blank },
  { path: '/teams/:handle', name: 'TeamsDetailDefault', component: blank },
  { path: '/teams/:handle/members', name: 'TeamsDetailMembers', component: blank },
  { path: '/teams/:handle/settings', name: 'TeamsDetailSettings', component: blank },
  { path: '/teams/:handle/profile', name: 'TeamsDetailProfile', component: blank },
  { path: '/teams/:handle/leave', name: 'TeamsDetailLeave', component: blank },
  { path: '/teams/:handle/library', name: 'TeamsDetailLibrary', component: blank },
  { path: '/teams/:handle/library/docs/:docId', name: 'TeamsDocument', component: blank },
  { path: '/teams/:handle/compute', name: 'TeamsDetailCompute', component: blank },
  { path: '/teams/:handle/models', name: 'TeamsDetailModels', component: blank },
  { path: '/teams/:handle/credits', name: 'TeamsDetailCredits', component: blank },
  { path: '/inbox', name: 'inbox', component: blank },
  // 首页那份目录里「市场」多出来的一行（`HomeNav.vue` 的 `:to="{ name: 'market' }"`）：
  // 名字不在表里，带 `to` 的行会当场抛，目录在预览站上整段渲染不出来。
  { path: '/market', name: 'market', component: blank },
  { path: '/projects/:projectId', name: 'workspace-project', component: blank },
  { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: blank },
  { path: '/plans', name: 'CreditPlans', component: blank },
  { path: '/spaces/:spaceId/tasks/:taskId', name: 'TasksDetail', component: blank },
  { path: '/spaces/:spaceId/tasks/:taskId/participants', name: 'TasksParticipants', component: blank },
  { path: '/spaces/:spaceId/tasks/:taskId/submissions', name: 'TasksSubmissions', component: blank },
  { path: '/spaces/:spaceId/announcements', name: 'SpacesAnnouncements', component: blank },
  { path: '/spaces/:spaceId/library', name: 'SpacesLibrary', component: blank },
  { path: '/projects/:projectId/artifacts/:artifactId', name: 'project-artifact', component: blank },
  { path: '/space-applications/:spaceId', name: 'HomeSpaceApplication', component: blank },
  // 题目那一层（`catalogScenesPages3.ts` 里题卡、单份回答、回答列表三件）互相指路：题卡上
  // 「查看该回答」、回答卡片上面那颗「查看全部 N 条回答」都按名字指。名字与路径照
  // `router/question.ts` 那棵子树；名字缺了按名字解析的那一条会当场抛，整段渲染不出来。
  { path: '/questions/:questionId(\\d+)', name: 'QuestionAnswerList', component: blank },
  { path: '/questions/:questionId(\\d+)/answers/:answerId(\\d+)', name: 'QuestionAnswer', component: blank },
]

export function demoRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/overview', name: 'workspace-overview', component: blank },
      // topicId is optional only so a link that passes one (the app strips it in addresses.ts) isn't warned about.
      { path: '/projects/:projectId/topics/:topicId?/tasks/:taskId', name: 'workspace-task', component: blank },
      { path: '/projects/:projectId/library', name: 'project-library', component: blank },
      ...analytics,
      ...catalogTargets,
      { path: '/:any(.*)*', component: blank },
    ],
  })
}
