import type { RouteRecordRaw } from 'vue-router'

/**
 * 反馈的路由。五条都在这里，预览入口（`src/proto-feedback.ts`）也直接用它。
 *
 * 五条都是**顶层**的，没有一个挂在 /projects/:id 下面 —— 反馈说的是平台本身，
 * 跟「我现在在哪个项目里」没有关系，挂在项目下面会让人以为这条反馈只属于那个项目。
 *
 * `/feedback/mine` 写在 `/feedback/:id` **前面**：vue-router 4 的排序本来就把静态段
 * 排在参数段前面（所以顺序其实不决定谁赢），但写在这里读起来是「固定段先声明的」，
 * 以后加 `/feedback/counts` 之类的时候不会有人担心被 `:id` 吃掉。
 *
 * 后台那条走 `/admin/*` 前缀，对应需求里的「类似 admin.okcheese.com 的独立后台」。
 * 现在它是同构里的一条普通路由（同一个 SPA、同一个构建），因为重点是那两套界面
 * 长得不一样、口径不一样；**真的拆成独立域名是运维/鉴权的事**，不是这一轮要决定的
 * 事，所以这里不为它做任何特殊处理。
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
    path: '/admin/feedback',
    name: 'AdminFeedback',
    component: () => import('@/views/feedback/AdminFeedbackPage.vue'),
    meta: { title: '反馈管理', isFullPage: true },
  },
  {
    path: '/design/feedback',
    name: 'FeedbackDesign',
    component: () => import('@/views/feedback/FeedbackDesignPage.vue'),
    meta: { title: '数据与架构', isFullPage: true },
  },
] as RouteRecordRaw[]
