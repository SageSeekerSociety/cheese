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
 * **题目详情这一格不再是老树的副本**（第八批）：`/board/tasks/:taskId` 现在是这块板
 * 自己的题目详情页（`pages/TaskDetail.vue`，按 `proto-board/pages/TaskDetail.vue` 的
 * 形状重画：题目卡、B 站视频、带下载次数的材料、四种状态的领取按钮、出题人视角的
 * 领取者名单、右栏的领取进度与走势）。老树那一页**一个字没改**，仍在
 * `/spaces/:id/tasks/:taskId` 上原样服务（见 `router/spaces.ts` 文件头）。底下那四格
 * （提交记录 / 参与者 / 交作业 / 启星研导）还是老页面，因为那背后是按 `submissionSchema`
 * 出表单的提交与逐版评审这类成熟功能；默认那一格（老 `Overview.vue`）撤了 ——
 * 它画的东西这一页的卡片里已经有了，见下面 `tasks/:taskId` 那一条的注释。
 *
 * **发题这一格不再是包着老页面**（第十批）：`/board/publish` 现在是这块板自己的发题页
 * （`pages/TaskPublish.vue`，按 `proto-board/pages/Publish.vue` 的形状重画 —— 页头与那颗
 * 「手写一道 / 从 PDF 生成」、两条路各几张卡、整页两栏网格）。底下那套成熟功能一件没丢：
 * 表单与附件卡片（`components/tasks/TaskForm.vue`、`TaskAttachmentPicker.vue`）原样复用，
 * 两条路走的是同一批真接口（`POST /tasks` 与
 * `POST /tasks/publish/from-pdf/preview|confirm`）；空间、分类、模板那份装配 —— 老树里
 * 由空间壳（`views/spaces/Detail.vue`）替老页装好 —— 现在由这一页自己保证（见那一页
 * 文件头的「装完再挂」）。老树那一页**一个字没改**，仍在 `/spaces/:id/publish` 上原样
 * 服务（见 `router/spaces.ts` 文件头）。
 *
 * **整板看板这一格不再是老树那九页的副本**（第七批）：`/board/analytics` 现在渲染这
 * 块板自己的看板（`pages/Analytics.vue`：六个 KPI、领取与提交走势、题目构成、分类分布、
 * 最热的题、出题人排行、待处理），数字来自老树那九页背后同一组 `/spaces/{id}/analytics/*`
 * 接口。老树那九页**一页没删**，仍在 `/spaces/:id/analytics/*` 上原样服务（见
 * `router/spaces.ts` 文件头）；新看板页脚留了一条走过去的路 —— 逐题、逐人、逐出题人
 * 翻明细去那里。所以这里不再有 `SpaceBoardAnalytics*` 那六条子路由：它们只是把老页面
 * 套进新外壳，留着就是一堆没人渲染的死路由。
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
      // 题目详情这一条**自己就是那一页**（第八批按原型重画，见 `pages/TaskDetail.vue`），
      // 底下只剩四格子路由：提交记录 / 参与者 / 交作业 / 启星研导 —— 页面里一个
      // `router-view` 与它们对上。名字都在 `routeNames.ts` 里，页面里跳转只认名字，
      // 所以老树那几条同名格子与这几条互不打架。
      //
      // 原来还有第五格：默认子路由（`path: ''`）挂的是老树的 `Overview.vue`。这一批
      // 撤了 —— 视频、附件、题目详情、领取现在都在这一页自己的卡片里，再挂一次老概览
      // 就是同一段视频和同一份附件清单在一屏上出现两遍。`BOARD_TASK_ROUTE_NAMES.overview`
      // 指回这一条路由本身，老页面里「返回概览」那颗按钮仍旧落在说得出去的地方。
      //
      // `backTo` 声明的是一块板上的上一层（题目板首页），顶栏那颗 ← 走的是它；
      // 老树里这一步是面包屑，新树没有面包屑（那几条路由没有 `meta.title`）。
      path: 'tasks/:taskId',
      name: 'SpaceBoardTaskDetail',
      component: () => import('./pages/TaskDetail.vue'),
      meta: { backTo: 'SpaceBoardHome' },
      children: [
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
          meta: { backTo: 'SpaceBoardTaskDetail' },
        },
        {
          path: 'ai-advice',
          name: 'SpaceBoardTaskAIAdvice',
          component: () => import('@/views/tasks/detail/AIAdvice.vue'),
        },
      ],
    },
    {
      // 整板看板是管理员那一格，直接输地址也要挡住 —— 与「审核」「成员」同一道门槛。
      path: 'analytics',
      name: 'SpaceBoardAnalytics',
      component: () => import('./pages/Analytics.vue'),
      beforeEnter: managerOnly,
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
