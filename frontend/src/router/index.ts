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
import { workspaceRoutes } from './workspaceRoutes'

import { recordEntry } from '@/lib/projectEntry'
import { reloadForNewBuild } from '@/services/staleBuild'
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
  workspaceRoutes,
  {
    name: 'preview-open',
    path: '/previews/:topicId',
    component: () => import('@/views/PreviewOpenView.vue'),
    meta: { title: '打开预览', isFullPage: true },
  },
  {
    name: 'site-open',
    path: '/sites/:projectId',
    component: () => import('@/views/SiteOpenView.vue'),
    meta: { title: '打开网站', isFullPage: true },
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
    // 「待办」: 手机底栏三格之一。桌面同样缺这个页面——今天离它最近的只有顶栏
    // 那颗铃铛的下拉，没有路由、没有页面。
    name: 'inbox',
    path: '/inbox',
    component: () => import('@/views/InboxView.vue'),
    meta: { title: '待办', isFullPage: true },
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

router.afterEach((to, from) => {
  const store = usePageTitleStore()
  store.triggerUpdate()
  // 走进一个项目时，把来路记下来——顶栏那颗 ← 靠它才回得去。名字必须**在这一刻**
  // 抓下来跟路由一起存：等按 ← 的时候再去取，那一页早就卸载了，只能显示一个光秃
  // 秃的箭头。
  recordEntry(to, from, (route) => {
    const dynamic = store.getDynamicTitle(route.name)
    if (dynamic) return dynamic
    // 由深到浅取第一个有标题的祖先：`/teams/:teamId` 自己没有标题，标题在
    // `/teams` 那一层上。
    for (const record of [...route.matched].reverse()) {
      if (record.meta?.title) return record.meta.title
    }
    return ''
  })
})

// A lazily imported view is fetched at navigation time, so a release that
// lands while this tab is open turns the next click into a rejected import
// rather than a page the user can see.
router.onError((error) => {
  reloadForNewBuild(error)
})

export default router
