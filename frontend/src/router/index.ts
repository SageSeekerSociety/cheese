import type { RouteRecordRaw } from 'vue-router'

import { createRouter, createWebHistory } from 'vue-router'

import AccountRoutes from './account'
import AssistantRoutes from './assistant'
import HomeRoutes from './home'
import ProjectsRoutes from './projects'
import QuestionRoutes from './question'
import SpacesRoutes from './spaces'
import TeamsRoutes from './teams'
import UserRoutes from './user'

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
  // --- CheeseX (agent workspace) routes ---------------------------------
  // Our grafted views navigate internally by these route names; they must be
  // registered here (the merged app uses main's router). Paths are prefixed
  // at /project (singular; main's dead /projects int table is retired) and
  // /spaces. App.vue rail items link by path (/project/:id) which
  // resolves to `workspace-project`.
  {
    name: 'workspace-project',
    path: '/project/:projectId',
    component: () => import('@/views/WorkspaceView.vue'),
    props: true,
    meta: { title: '项目工作台', isFullPage: true },
  },
  {
    name: 'overview',
    path: '/project/:projectId/overview',
    component: () => import('@/views/OverviewView.vue'),
    props: true,
    meta: { title: '总览', isFullPage: true },
  },
  {
    name: 'calendar',
    path: '/project/:projectId/calendar',
    component: () => import('@/views/CalendarView.vue'),
    props: true,
    meta: { title: '日历', isFullPage: true },
  },
  {
    name: 'project-charter',
    path: '/project/:projectId/charter',
    component: () => import('@/views/ProjectDocsView.vue'),
    props: true,
    meta: { title: '项目章程', isFullPage: true },
  },
  {
    name: 'project-decisions',
    path: '/project/:projectId/decisions',
    component: () => import('@/views/ProjectDocsView.vue'),
    props: true,
    meta: { title: '决策记录', isFullPage: true },
  },
  {
    name: 'project-weeklies',
    path: '/project/:projectId/weeklies',
    component: () => import('@/views/ProjectDocsView.vue'),
    props: true,
    meta: { title: '周报', isFullPage: true },
  },
  {
    name: 'project-settings',
    path: '/project/:projectId/settings',
    component: () => import('@/views/ProjectSettingsView.vue'),
    props: true,
    meta: { title: '项目设置', isFullPage: true },
  },
  {
    name: 'member',
    path: '/project/:projectId/members/:handle',
    component: () => import('@/views/MemberView.vue'),
    props: true,
    meta: { title: '成员', isFullPage: true },
  },
  {
    name: 'my-devices',
    path: '/my/devices',
    component: () => import('@/views/MyDevicesView.vue'),
    meta: { title: '我的设备', isFullPage: true },
  },
  {
    name: 'market',
    path: '/market',
    component: () => import('@/views/MarketView.vue'),
    meta: { title: '市场', isFullPage: true },
  },
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
