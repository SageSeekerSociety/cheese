import type { RouteRecordRaw } from 'vue-router'

// 「我的 Agent」管理页。单独成文件，避免与 workspace 路由的并行编辑冲突。
// 内测功能：仅在带 `?exp=true` 的内测态下可达（同 router/workspace.ts 的 expOnly），
// 普通链接直接访问会被重定向到首页。
const expOnly = (to: { query: Record<string, unknown> }, _from: unknown, next: (loc?: object) => void) => {
  if (to.query.exp === 'true') next()
  else next({ name: 'Home' })
}

const AgentsRoutes: RouteRecordRaw[] = [
  {
    path: '/my-agents',
    name: 'MyAgents',
    component: () => import('@/views/agents/MyAgentsView.vue'),
    meta: {
      title: '我的 Agent',
      requiresAuth: true,
    },
    beforeEnter: expOnly,
  },
]

export default AgentsRoutes
