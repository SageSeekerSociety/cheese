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

/** 目录里有几件是用 name 指路的（协议那两句、反馈详情、小队主页、房间里的一次对话），它们各要一条真名字。 */
const catalogTargets = [
  { path: '/legal/terms', name: 'LegalTerms', component: blank },
  { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
  { path: '/feedback/:id', name: 'FeedbackDetail', component: blank },
  { path: '/teams/:handle', name: 'TeamsDetailDefault', component: blank },
  { path: '/teams/:handle/credits', name: 'TeamsDetailCredits', component: blank },
  { path: '/projects/:projectId', name: 'workspace-project', component: blank },
  { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: blank },
]

export function demoRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/overview', name: 'workspace-overview', component: blank },
      ...analytics,
      ...catalogTargets,
      { path: '/:any(.*)*', component: blank },
    ],
  })
}
