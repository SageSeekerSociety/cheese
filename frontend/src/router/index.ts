import type { RouteRecordRaw } from 'vue-router'

import { createRouter, createWebHistory } from 'vue-router'

import AccountRoutes from './account'
import HomeRoutes from './home'
import { legacyProjectRedirects } from './legacyProjectPaths'
import ProjectsRoutes from './projects'
import QuestionRoutes from './question'
import SpacesRoutes from './spaces'
import TeamsRoutes from './teams'
import UserRoutes from './user'

import { usePageTitleStore } from '@/stores/title'

const routes: RouteRecordRaw[] = [
  AccountRoutes,
  HomeRoutes,
  UserRoutes,
  ProjectsRoutes,
  QuestionRoutes,
  SpacesRoutes,
  TeamsRoutes,
  // --- CheeseX (agent workspace) routes ---------------------------------
  // Our grafted views navigate internally by these route names; they must be
  // registered here (the merged app uses main's router).
  //
  // These sat at the SINGULAR `/project` for one reason, recorded in the comment
  // this replaces: "main's dead /projects int table is retired". It was not
  // retired for long — migration c9f2a3b40e15 dropped it, 41224effe32b brought
  // it back — so the singular stopped being a choice and became an accident,
  // leaving the browser telling two generations apart by one letter. #370 ends
  // that everywhere; here 1.0 moved to /team-projects and the workspace takes
  // the plural, so the API and the address bar finally read the same.
  //
  // Old links keep working through ./legacyProjectPaths — which must stay ahead
  // of the workspace routes below, since one of its rules matches the same path
  // shape and is distinguished only by the id being numeric.
  ...legacyProjectRedirects,
  {
    name: 'workspace-project',
    path: '/projects/:projectId',
    component: () => import('@/views/WorkspaceView.vue'),
    props: true,
    meta: { title: '项目工作台', isFullPage: true },
  },
  {
    name: 'overview',
    path: '/projects/:projectId/overview',
    component: () => import('@/views/OverviewView.vue'),
    props: true,
    meta: { title: '总览', isFullPage: true },
  },
  {
    name: 'calendar',
    path: '/projects/:projectId/calendar',
    component: () => import('@/views/CalendarView.vue'),
    props: true,
    meta: { title: '日历', isFullPage: true },
  },
  {
    name: 'project-charter',
    path: '/projects/:projectId/charter',
    component: () => import('@/views/ProjectDocsView.vue'),
    props: true,
    meta: { title: '项目章程', isFullPage: true },
  },
  {
    name: 'project-decisions',
    path: '/projects/:projectId/decisions',
    component: () => import('@/views/ProjectDocsView.vue'),
    props: true,
    meta: { title: '决策记录', isFullPage: true },
  },
  {
    name: 'project-weeklies',
    path: '/projects/:projectId/weeklies',
    component: () => import('@/views/ProjectDocsView.vue'),
    props: true,
    meta: { title: '周报', isFullPage: true },
  },
  {
    name: 'project-settings',
    path: '/projects/:projectId/settings',
    component: () => import('@/views/ProjectSettingsView.vue'),
    props: true,
    meta: { title: '项目设置', isFullPage: true },
  },
  {
    name: 'member',
    path: '/projects/:projectId/members/:handle',
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
    // Device-flow approval landing page: `cheesehost auth login` prints a
    // `<frontend>/connect?code=…` link; the signed-in human lands here to bind
    // the machine to a project and mint its agent (fusion-design §5). Registered
    // here because the merged app uses main's router.
    name: 'connect',
    path: '/connect',
    component: () => import('@/views/ConnectView.vue'),
    meta: { title: '连接设备', isFullPage: true },
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
