import type { RouteRecordRaw } from 'vue-router'

import { createRouter, createWebHistory } from 'vue-router'

import AccountRoutes from './account'
import AgentsRoutes from './agents'
import AssistantRoutes from './assistant'
import ConnectRoutes from './connect'
import HomeRoutes from './home'
import ProjectsRoutes from './projects'
import QuestionRoutes from './question'
import SpacesRoutes from './spaces'
import TeamsRoutes from './teams'
import UserRoutes from './user'
import WorkspaceRoutes from './workspace'

import { usePageTitleStore } from '@/stores/title'

const routes: RouteRecordRaw[] = [
  AccountRoutes,
  AssistantRoutes,
  HomeRoutes,
  UserRoutes,
  ProjectsRoutes,
  QuestionRoutes,
  SpacesRoutes,
  TeamsRoutes,
  ...WorkspaceRoutes,
  ...AgentsRoutes,
  ...ConnectRoutes,
  {
    name: 'Search',
    path: '/search',
    component: () => import('@/views/searches/Index.vue'),
    meta: {
      title: '搜索',
    },
  },
  {
    name: 'NotFound',
    path: '/:pathMatch(.*)*',
    component: () => import('@/views/404.vue'),
    meta: {
      title: '页面未找到',
    },
  },
]

const router = createRouter({
  history: createWebHistory(process.env.BASE_URL),
  routes,
})

router.beforeEach(async (to, from, next) => {
  // 内测态（?exp=true）粘性：从带该参数的页面导航到不带的页面时自动补上，让内测态
  // 在应用内「弹来弹去」保持；用不带该参数的普通链接直接访问则不补，回到非内测态。
  if (to.query.exp !== 'true' && from.query.exp === 'true') {
    next({ ...to, query: { ...to.query, exp: 'true' } })
    return
  }

  const store = usePageTitleStore()
  to.matched.forEach((record) => {
    const meta = record.meta
    if (meta?.getDynamicTitle && !meta.isDynamic) {
      try {
        const title = meta.getDynamicTitle(to)
        if (record.name) {
          store.setDynamicTitle(title, record.name)
        }
      } catch (error) {
        console.error('路由守卫中设置动态标题失败:', error)
      }
    }
  })

  next()
})

router.afterEach(async (to) => {
  const store = usePageTitleStore()
  store.triggerUpdate()
})

export default router
