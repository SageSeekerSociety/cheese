import type { RouteRecordRaw } from 'vue-router'

import { createRouter, createWebHistory, START_LOCATION } from 'vue-router'

import AccountRoutes from './account'
import { canonicalNavigation } from './addressHistory'
import { requireEmail } from './emailRequired'
import FeedbackRoutes from './feedback'
import HomeRoutes from './home'
import { legacyProjectRedirects } from './legacyProjectPaths'
import LegalRoutes from './legal'
import { carryLoginRedirect, leaveAppForPagesItHasNot } from './loginRedirect'
import QuestionRoutes from './question'
import SharedRoutes from './shared'
import SpacesRoutes from './spaces'
import TeamsRoutes from './teams'
import UserRoutes from './user'
import { workspaceRoutes } from './workspaceRoutes'

import { isUuid, noteSignedOut, routeIds } from '@/lib/addresses'
import { preloadPdfViewer } from '@/lib/pdfPreload'
import { rememberPageBeforeSettings } from '@/lib/settingsReturn'
import { installTopicTransitions } from '@/lib/viewTransition'
import { myId } from '@/me'
import { prefetchNewestBlocks } from '@/query/blocks'
import { prefetchPersonalRoute } from '@/query/homeNav'
import { keys } from '@/query/keys'
import { prefetchProjectSubpage, prefetchProjectTopics } from '@/query/project'
import { prefetchPreview, prefetchThread } from '@/query/room'
import { expectRoom, readEarly } from '@/query/snapshot'
import { recoverNavigations } from '@/services/staleBuild'
import { useFeedbackStore } from '@/stores/feedback'
import { usePageTitleStore } from '@/stores/title'
import { handSignInToApp } from '@/views/account/appSignIn'

// 个人的几页（设备、连接、批准设备、打开预览/网站）不是底栏上的一级目的地，是从头像
// 菜单或一条链接推进来的一层：手机上收起底栏、顶栏给 ← 回首页。桌面上左边有 rail，
// 这几页照旧不画 ←。
const PERSONAL_PAGE = { hideTabs: true, backTo: 'HomeHub', backOnPhoneOnly: true } as const

const routes: RouteRecordRaw[] = [
  {
    name: 'team-join',
    path: '/team-invites/:token',
    component: () => import('@/views/TeamInviteView.vue'),
    props: true,
    meta: { titleKey: 'work.teamProfile.joinTitle', isFullPage: true },
  },
  AccountRoutes,
  ...LegalRoutes,
  ...SharedRoutes,
  HomeRoutes,
  UserRoutes,
  QuestionRoutes,
  SpacesRoutes,
  TeamsRoutes,
  {
    name: 'CreditPlans',
    path: '/plans',
    component: () => import('@/views/CreditPlans.vue'),
    meta: { titleKey: 'usage.plans.title', isFullPage: true, hideTabs: true, backTo: 'HomeHub' },
  },
  // --- CheeseX (agent workspace) routes ---------------------------------
  // Our grafted views navigate internally by these route names; they must be
  // registered here (the merged app uses main's router).
  //
  // These sat at the SINGULAR `/project` for one reason, recorded in the comment
  // this replaces: "main's dead /projects int table is retired". It was not
  // retired for long — migration c9f2a3b40e15 dropped it, 41224effe32b brought
  // it back — so the singular stopped being a choice and became an accident,
  // leaving the browser telling two generations apart by one letter. #370 ends
  // that: the workspace takes the plural, so the API and the address bar read
  // the same.
  //
  // Old `/project/...` links keep working through ./legacyProjectPaths.
  ...legacyProjectRedirects,
  workspaceRoutes,
  // 反馈：/feedback、/feedback/mine、/feedback/:id，以及后台壳 /admin/*
  // （/admin/queue、/admin/dashboard、/admin/members，外加指向队列的旧地址
  // /admin/feedback）。
  // 都是顶层路由，必须挂在下面的 NotFound 通配**之前**，否则会被它吃掉
  // —— 通配吃掉的直接后果是这几页打不开，而「打不开」看起来像后端 404。
  ...FeedbackRoutes,
  {
    name: 'preview-open',
    path: '/previews/:topicId',
    component: () => import('@/views/PreviewOpenView.vue'),
    meta: { titleKey: 'navigation.pages.openPreview', isFullPage: true, ...PERSONAL_PAGE },
  },
  {
    name: 'site-open',
    path: '/sites/:projectId',
    component: () => import('@/views/SiteOpenView.vue'),
    meta: { titleKey: 'navigation.pages.openSite', isFullPage: true, ...PERSONAL_PAGE },
  },
  {
    // The docs site signs its readers in through here (views/DocsSignIn.vue).
    name: 'docs-signin',
    path: '/docs-signin',
    component: () => import('@/views/DocsSignIn.vue'),
    meta: { titleKey: 'navigation.pages.openDocs', isFullPage: true, ...PERSONAL_PAGE },
  },
  {
    name: 'my-archived-projects',
    path: '/my/archived-projects',
    component: () => import('@/views/MyArchivedProjectsView.vue'),
    meta: { titleKey: 'navigation.userMenu.archivedProjects', isFullPage: true, ...PERSONAL_PAGE },
  },
  {
    // Device-flow approval landing page: `cheesehost auth login` prints a
    // `<frontend>/connect?code=…` link; the signed-in human lands here to bind
    // the machine to a project and mint its agent (fusion-design §5). Registered
    // here because the merged app uses main's router.
    name: 'connect',
    path: '/connect',
    component: () => import('@/views/ConnectView.vue'),
    meta: { titleKey: 'navigation.pages.connect', isFullPage: true, ...PERSONAL_PAGE },
  },
  {
    // 新手引导：一个项目都没有、也没走过引导的人登录后落在这里（router/home.ts
    // `landingForMember`）；帮助菜单「新手引导」带 `?again=1` 再走一遍。
    name: 'welcome',
    path: '/welcome',
    component: () => import('@/views/welcome/Welcome.vue'),
    // 整屏：引导期间不露项目栏和顶栏，人只看这一件事。
    meta: { titleKey: 'welcome.title', isFullPage: true, hideTabs: true, hideAppBar: true },
    props: (to) => ({ again: to.query.again === '1' }),
    beforeEnter: async (to) => {
      const { default: AccountService } = await import('@/services/account')
      await AccountService.sessionRestored
      return AccountService.loggedIn ? true : { name: 'SignIn', query: { redirect: to.fullPath } }
    },
  },
  {
    name: 'market',
    path: '/market',
    component: () => import('@/views/MarketView.vue'),
    // 「市场」在 ⌘K 里也找得到（`pages.palette.ts` 只认带 `palette` 的路由）；名字复用
    // 标题那一份，图标和桌面 rail 那一格同一颗。
    meta: {
      titleKey: 'navigation.pages.market',
      isFullPage: true,
      palette: { label: 'navigation.pages.market', icon: 'mdi-storefront-outline' },
    },
  },
  {
    name: 'NotFound',
    path: '/:pathMatch(.*)*',
    component: () => import('@/views/404.vue'),
    meta: {
      titleKey: 'navigation.pages.notFound',
    },
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  // Only the public pages scroll the document; the workspace locks it and scrolls
  // its own panes, which this leaves alone. Moving to another public page starts
  // at its top (or at its #anchor), Back returns to where the reader was, and
  // leaving the public pages for work drops whatever scroll they held.
  scrollBehavior(to, from, savedPosition) {
    if (!to.meta.publicLanding && !from.meta.publicLanding) return false
    if (savedPosition) return savedPosition
    if (to.hash) return { el: to.hash }
    if (to.path !== from.path) return { top: 0 }
    return false
  },
})

router.beforeEach(carryLoginRedirect)
router.beforeEach(handSignInToApp(() => !!myId()))
requireEmail(router, async () => (await import('@/services/account')).default)
router.beforeEach(leaveAppForPagesItHasNot)
// 项目框里的地址落地成短的那一种（`/projects/<短名>/tasks/318`），见 lib/addresses。
router.beforeEach((to) => (myId() ? canonicalNavigation(to, router.options.history) : noteSignedOut(to)))

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

router.afterEach((to, from, failure) => {
  const store = usePageTitleStore()
  store.triggerUpdate()
  if (!failure) rememberPageBeforeSettings(to, from)
})

// 工作台外壳的频道清单和页面代码同时去取。
//
// 它只看地址上的项目 id，可发得太晚了：那个请求由 `ProjectShell` 挂载时发起，于是在
// 「项目这一跳的 chunk 下完」之后才轮到它。冷开一个项目页实测 1040 ms 才出发（正是
// 框架挂上那一刻），清单自己还要 200 ms，侧栏和页面上的房间行又在等它。这里趁 chunk
// 还在下就让它走（`query/project` 的 `prefetchProjectTopics`）。
//
// 只补一次：已经站在同一个项目里了，框架还挂着、清单自己会重读，不重复发。短名地址
// 在上面 `canonicalAddress` 那条守卫里已经换成了 UUID；换不出来的（没有这个项目、
// 看不到）这里不发。
router.beforeEach((to, from) => {
  if (!myId()) return
  prefetchPersonalRoute(to.name, to.params)
  if (!to.matched.some((record) => record.meta?.projectFrame === true)) return
  const ids = routeIds(to.params)
  const projectId = ids.projectId
  if (!projectId || !isUuid(projectId)) return
  prefetchProjectSubpage(to.name, projectId, ids, to.query)
  const fromProjectId = from.matched.some((record) => record.meta?.projectFrame === true)
    ? routeIds(from.params).projectId
    : undefined
  if (fromProjectId === projectId) return
  prefetchProjectTopics(projectId)
})

// 房间的消息和房间页的代码同时去取。不在这里起头的话，消息要等房间页那一串
// chunk 下完、ChatPanel 挂上之后才开始请求，两段等待首尾相接，冷打开一个房间时
// 那条最大的消息晚半秒以上才出现。ChatPanel 挂上来时跟着这一条走，不另发一次；
// 手上那份还新鲜就不取（`query/blocks`）；没登录就什么都不发。任务页读的是任务
// 自己那段对话（任务的 id），不是它所在的频道。
router.beforeEach((to, from) => {
  if ((to.name !== 'workspace-topic' && to.name !== 'workspace-task' && to.name !== 'workspace-thread') || !myId())
    return
  const ids = routeIds(to.params)
  const conversation = to.name === 'workspace-task' ? ids.taskId : ids.topicId
  if (!conversation || !isUuid(conversation)) return
  // 房间里会点开文档预览：趁浏览器空闲把 pdf.js 先取下来（只取一次）。**排在消息这一条
  // 后面**：pdf.js 主体加 worker 有半兆，而这一跳里浏览器正是在等网络，`requestIdleCallback`
  // 所谓「空闲」指 CPU 不忙，于是它当场就发出去，和这一屏真正要等的那次消息请求同时在飞
  // （实测两条重叠，消息那次被拖长到 400 ms 以上）。等消息落地再取：那时第一眼已经看到了。
  void prefetchNewestBlocks(conversation)
    .catch(() => {})
    .finally(() => preloadPdfViewer())
  if (to.name === 'workspace-thread' && ids.threadId && isUuid(ids.threadId)) {
    void prefetchThread(ids.threadId)
  }
  // 房间的名册、任务、置顶这些随订阅一起来（query/snapshot）：页面上读它们的先等快照。
  if (ids.topicId && isUuid(ids.topicId)) expectRoom(conversation, ids.topicId)
  // 冷打开时订阅连得晚，快照赶不上消息：提议卡和消息一起先去问，不然它比消息晚一个来回。
  if (from === START_LOCATION) {
    readEarly(keys.roomFeedbackProposals(conversation), () => useFeedbackStore().loadProposals(conversation))
  }
})

// 「这件任务当前预览是哪一份」也和页面代码同时去问。它和消息一样不依赖任务页的代码：
// 等那一串 chunk 下完、任务数据回来，面板才挂得上，而它一挂上就要这份答案。这里先让
// 它出发，面板到手时通常已经在缓存里了（`query/room`）。失败没有人看得见
// ——它是顺手做的事。只问任务：频道没有「预览」那一格。地址上还是编号、认不出是哪
// 一件的，交给面板挂上来之后自己问。
router.beforeEach((to) => {
  if (to.name !== 'workspace-task' || !myId()) return
  const taskId = routeIds(to.params).taskId
  if (taskId && isUuid(taskId)) void prefetchPreview(taskId)
})

// A lazily imported view is fetched at navigation time, so a release that
// lands while this tab is open turns the next click into a rejected import
// rather than a page the user can see. Load the page the click was going to.
recoverNavigations(router)

// 宽屏上话题之间切换的淡入淡出（lib/viewTransition.ts）。
installTopicTransitions(router)

export default router
