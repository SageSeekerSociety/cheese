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
 * **题目详情与发题这两条不在这里**：它们暂时指向真平台已有的那两页
 * （`SpacesDetailTasksDetail` / `SpacesDetailPublishTask`）。理由不是偷懒 ——
 * 那两页背后是成熟功能（按 `submissionSchema` 出表单的提交、逐版评审、AI 建议、
 * PDF 生成），在新外壳里重写一遍只会在「读路径」这一批里塞进一堆**写路径**。
 * 详情与发题各自的重做排在后面的批次里。
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
