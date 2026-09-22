import type { RouteRecordRaw } from 'vue-router'

/**
 * 反馈与管理后台的路由。都在这里，预览入口（`src/proto-feedback.ts`）也直接用它。
 *
 * 用户侧三条都是**顶层**的，没有一个挂在 /projects/:id 下面 —— 反馈说的是平台本身，
 * 跟「我现在在哪个项目里」没有关系，挂在项目下面会让人以为这条反馈只属于那个项目。
 *
 * `/feedback/mine` 写在 `/feedback/:id` **前面**：vue-router 4 的排序本来就把静态段
 * 排在参数段前面（所以顺序其实不决定谁赢），但写在这里读起来是「固定段先声明的」，
 * 以后加 `/feedback/counts` 之类的时候不会有人担心被 `:id` 吃掉。
 *
 * 后台走 `/admin/*` 前缀，对应需求里的「类似 admin.okcheese.com 的独立后台」。
 * 现在它是同构里的一条普通路由（同一个 SPA、同一个构建），因为重点是那两套界面
 * 长得不一样、口径不一样；**真的拆成独立域名是运维/鉴权的事**，不是这一轮要决定的
 * 事，所以这里不为它做任何特殊处理。
 *
 * 后台从「一整页反馈管理」改成了**壳**（`AdminLayout`）：左边分区、右边装模块，现在
 * 装三块。所以 `/admin` 是一条带 `children` 的父路由。
 *
 * `/admin/queue` 和 `/admin/dashboard` 是这一轮新加的两条：
 *
 * - `queue` 是「反馈管理」这个模块改叫「队列」之后的家。换地址而不是原地换内容，因为
 *   「反馈管理」这个名字说的是「一页管所有反馈」，而它其实是按状态往前推的分诊队列，
 *   旁边还站着看板和成员 —— 三个平级的模块挤在同一个名字底下，链接分享出去对不上话。
 * - `dashboard` 是看板，同一层里的第三块。
 * - `/admin/feedback` **留着**，渲染一个薄壳（`AdminFeedbackPage` → `AdminQueuePage`）。
 *   老书签、老通知、别人贴在聊天里的链接都指到这里，删掉就是一个 404；而重定向会把
 *   地址栏里那个旧地址悄悄换掉，用户回头再复制一次时会以为自己记错了。留着它，代价
 *   是六行。
 *
 * `isFullPage: true` 是给顶栏用的：AppBar 取层级里第一个 isFullPage 的标题作为
 * 中间那行字（见 components/common/Navigation/AppBar.vue 的 updateTitle），
 * 不写的话顶栏会一直显示默认的「知是」。
 */
export default [
  {
    path: '/feedback',
    name: 'FeedbackCenter',
    component: () => import('@/views/feedback/FeedbackCenterPage.vue'),
    meta: { title: '反馈中心', isFullPage: true },
  },
  {
    path: '/feedback/mine',
    name: 'FeedbackMine',
    component: () => import('@/views/feedback/FeedbackMinePage.vue'),
    meta: { title: '我的反馈', isFullPage: true },
  },
  {
    path: '/feedback/:id',
    name: 'FeedbackDetail',
    component: () => import('@/views/feedback/FeedbackDetailPage.vue'),
    meta: { title: '反馈详情', isFullPage: true },
  },
  {
    path: '/admin',
    component: () => import('@/views/admin/AdminLayout.vue'),
    // 只写地址不写组件：`/admin` 本身没有内容，直接落进队列 —— 后台里用得最多的那一块。
    redirect: '/admin/queue',
    children: [
      {
        path: 'queue',
        name: 'AdminQueue',
        component: () => import('@/views/admin/AdminQueuePage.vue'),
        meta: { title: '反馈队列', isFullPage: true },
      },
      {
        path: 'dashboard',
        name: 'AdminDashboard',
        component: () => import('@/views/admin/AdminDashboardPage.vue'),
        meta: { title: '看板', isFullPage: true },
      },
      {
        // main 后加的这一块（题目板审核），地址与分区名都跟着它自己的 PR 走。
        path: 'spaces',
        name: 'AdminSpaces',
        component: () => import('@/views/admin/AdminSpacesPage.vue'),
        meta: { title: '题目板审核', isFullPage: true },
      },
      {
        // 老地址，见文件头。渲染的是同一个队列，所以标题也跟它一致 —— 顶栏上那行字
        // 不该因为用户是从哪个链接进来的而变。
        path: 'feedback',
        name: 'AdminFeedback',
        component: () => import('@/views/feedback/AdminFeedbackPage.vue'),
        meta: { title: '反馈队列', isFullPage: true },
      },
      {
        path: 'members',
        name: 'AdminMembers',
        component: () => import('@/views/admin/AdminMembersPage.vue'),
        meta: { title: '成员管理', isFullPage: true },
      },
    ],
  },
] as RouteRecordRaw[]
