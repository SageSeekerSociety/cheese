import type { RouteRecordRaw } from 'vue-router'

/**
 * 用户协议与隐私政策的公开页（#1486）。
 *
 * `publicLanding`：不套应用外壳（App.vue 在这类路由上只渲染 router-view）。
 * 不登录也要能看；更要紧的是，已登录、还没同意新版协议的人从弹窗里点开协议，
 * 这一页不能被同一个弹窗盖住——那个弹窗长在应用外壳里。
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/legal/terms',
    name: 'LegalTerms',
    component: () => import('@/views/legal/LegalDocumentView.vue'),
    props: { document: 'terms' },
    meta: { title: '用户协议', publicLanding: true },
  },
  {
    path: '/legal/privacy',
    name: 'LegalPrivacy',
    component: () => import('@/views/legal/LegalDocumentView.vue'),
    props: { document: 'privacy' },
    meta: { title: '隐私政策', publicLanding: true },
  },
]

export default routes
