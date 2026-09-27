/**
 * 空间新界面的路由。
 *
 * **它是并存的一棵，不是替换**：老的空间页仍在 `/spaces/:id/{tasks,analytics,…}` 上
 * 原样服务，这一棵挂在 `/spaces/:id/board` 下面。等新界面把老页面接手过去，这些
 * 地址再往上收一层 —— 因此这里全部用**命名路由**，路径前缀只在这一个文件里出现，
 * 收的时候改一行。
 *
 * `/spaces/:spaceId` 那一条是带 `sidebar` 具名视图的嵌套路由，而这一条是**顶层**的：
 * `board` 不是它的子路径，所以不会被它接住，也就不会套上老侧栏。新界面有自己的
 * 外壳（`SpaceBoardShell.vue`）。
 *
 * **题目详情、发题、整板看板在第五批收进来了**：那三处背后是成熟功能（按
 * `submissionSchema` 出表单的提交、逐版评审、AI 建议、PDF 生成、九个看板页），
 * 所以老页面**一个字没改**，各由 `pages/` 下一层包着挂进这棵树 —— 每层文件顶部
 * 写明它补的是什么（provide 路由名、页头、pinia 的 space store）。老地址照常
 * 在原处服务，退场是下一批的事。
 */
import type { RouteLocationNormalized, RouteRecordRaw } from 'vue-router'

import { isManager, loadBoard } from './store'

/** 管理员那三页唯一的一道门槛：在不在管理员名单里。
 *
 *  写成 `beforeEnter` 而不是在页面里判，是因为**直接输地址**也要挡住（原型里
 *  `router.beforeEach` 做的是同一件事）。守卫必须先等空间装好 —— 角色是从
 *  `space.admins` 算出来的，没装好之前谁都是 MEMBER。 */
async function managerOnly(to: RouteLocationNormalized) {
  const spaceId = Number(to.params.spaceId)
  await loadBoard(spaceId)
  if (!isManager.value) return { name: 'SpaceBoardHome', params: { spaceId } }
  return true
}

export const SpaceBoardRoutes: RouteRecordRaw = {
  path: '/spaces/:spaceId/board',
  name: 'SpaceBoard',
  component: () => import('./SpaceBoardShell.vue'),
  props: (route) => ({ spaceId: Number(route.params.spaceId) }),
  meta: { isFullPage: true, backTo: 'SpacesDetailTasksList' },
  redirect: { name: 'SpaceBoardHome' },
  children: [
    {
      path: '',
      name: 'SpaceBoardHome',
      component: () => import('./pages/BoardHome.vue'),
    },
    {
      path: 'mine',
      name: 'SpaceBoardMine',
      component: () => import('./pages/Mine.vue'),
    },
    {
      path: 'publish',
      name: 'SpaceBoardTaskPublish',
      component: () => import('./pages/TaskPublish.vue'),
      meta: { backTo: 'SpaceBoardHome' },
    },
    {
      // 题目详情这一条**带着五格**（概览 / 提交记录 / 参与者 / 交作业 / 启星研导），
      // 和它底下那一页自己带的 `router-view` 对上：名字都在 `routeNames.ts` 里，
      // 页面里跳转只认名字，所以老树那几条同名格子与这几条互不打架。
      //
      // `backTo` 声明的是一块板上的上一层（题目板首页），顶栏那颗 ← 走的是它；
      // 老树里这一步是面包屑，新树没有面包屑（那几条路由没有 `meta.title`）。
      path: 'tasks/:taskId',
      name: 'SpaceBoardTaskDetail',
      component: () => import('./pages/TaskDetail.vue'),
      meta: { backTo: 'SpaceBoardHome' },
      children: [
        {
          path: '',
          name: 'SpaceBoardTaskOverview',
          component: () => import('@/views/tasks/detail/Overview.vue'),
        },
        {
          path: 'submissions',
          name: 'SpaceBoardTaskSubmissions',
          component: () => import('@/views/tasks/detail/Submissions.vue'),
        },
        {
          path: 'participants',
          name: 'SpaceBoardTaskParticipants',
          component: () => import('@/views/tasks/detail/Participants.vue'),
        },
        {
          path: 'submit',
          name: 'SpaceBoardTaskSubmit',
          component: () => import('@/views/tasks/detail/Submit.vue'),
          meta: { backTo: 'SpaceBoardTaskOverview' },
        },
        {
          path: 'ai-advice',
          name: 'SpaceBoardTaskAIAdvice',
          component: () => import('@/views/tasks/detail/AIAdvice.vue'),
        },
      ],
    },
    {
      path: 'analytics',
      name: 'SpaceBoardAnalytics',
      component: () => import('./pages/Analytics.vue'),
      redirect: { name: 'SpaceBoardAnalyticsOverview' },
      // 整板看板是管理员那一格，直接输地址也要挡住 —— 与「审核」「成员」同一道门槛。
      beforeEnter: managerOnly,
      children: [
        {
          path: '',
          name: 'SpaceBoardAnalyticsOverview',
          component: () => import('@/views/spaces/detail/analytics/Overview.vue'),
        },
        {
          path: 'alerts',
          name: 'SpaceBoardAnalyticsAlerts',
          component: () => import('@/views/spaces/detail/analytics/Alerts.vue'),
        },
        {
          path: 'publishers',
          name: 'SpaceBoardAnalyticsPublishers',
          component: () => import('@/views/spaces/detail/analytics/Publishers.vue'),
        },
        {
          path: 'tasks',
          name: 'SpaceBoardAnalyticsTasks',
          component: () => import('@/views/spaces/detail/analytics/Tasks.vue'),
        },
        {
          path: 'participants',
          name: 'SpaceBoardAnalyticsParticipants',
          component: () => import('@/views/spaces/detail/analytics/Participants.vue'),
        },
        {
          path: 'learning',
          name: 'SpaceBoardAnalyticsLearning',
          component: () => import('@/views/spaces/detail/analytics/Learning.vue'),
        },
      ],
    },
    {
      path: 'insights/:taskId',
      name: 'SpaceBoardTaskInsights',
      component: () => import('./pages/TaskInsights.vue'),
      props: true,
      meta: { backTo: 'SpaceBoardHome' },
    },
    {
      path: 'announcements',
      name: 'SpaceBoardAnnouncements',
      component: () => import('./pages/Announcements.vue'),
    },
    {
      path: 'review',
      name: 'SpaceBoardReview',
      component: () => import('./pages/Review.vue'),
      beforeEnter: managerOnly,
    },
    {
      path: 'members',
      name: 'SpaceBoardMembers',
      component: () => import('./pages/Members.vue'),
      beforeEnter: managerOnly,
    },
  ],
}
