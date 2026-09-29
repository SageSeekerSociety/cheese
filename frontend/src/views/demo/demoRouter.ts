// 演示页没有路由，但房间里有几个真组件（平台事件行的「去看看」链接）用 router-link。
// 给它们一个什么都不导航的内存路由：链接照样画出来，点了也不离开演示。
//
// 光有通配路由不够：router-link 写的是 { name: 'workspace-running' }（预览页签上
// 那个「发布网站」），按名字解析的路由在表里找不到就**抛**（不是警告），整个渲染就
// 断在那儿。名字补全，落点还是那个空的通配页 —— 链接画得出来，点不动。
//
// 看板那六格（`AnalyticsNavigationTabs` 的页签，名字见 `shellRouteNames.ts`）同理：
// 那一排「总览 / 告警 / 出题人 / …」在组件预览站上也得画得出来。
import { createMemoryHistory, createRouter } from 'vue-router'

import { OLD_ANALYTICS_ROUTE_NAMES } from '@/lib/shellRouteNames'

const blank = { render: () => null }

/** 老树那一套看板名字，各给一条不会被走到的路径（落点是通配页）。 */
const analytics = Object.entries(OLD_ANALYTICS_ROUTE_NAMES).map(([key, name]) => ({
  path: `/spaces/:spaceId/analytics/${key}`,
  name,
  component: blank,
}))

export function demoRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/running', name: 'workspace-running', component: blank },
      ...analytics,
      { path: '/:any(.*)*', component: blank },
    ],
  })
}
