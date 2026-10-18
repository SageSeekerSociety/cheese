import type { RouteRecordRaw } from 'vue-router'

/**
 * 经链接分享的文档（#3774）。`publicLanding`：不登录也能看，不套应用外壳。
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/s/:token',
    name: 'SharedDocument',
    component: () => import('@/views/shared/SharedDocument.vue'),
    props: true,
    meta: { titleKey: 'publicSite.sharedDoc.pageTitle', publicLanding: true },
  },
]

export default routes
