import type { RouteRecordRaw } from 'vue-router'

// 知是 2.0 群聊工作区 + 现场全屏页。内测功能：仅在带 `?exp=true` 的内测态下可达（见
// useExperimental / 路由粘性守卫），普通链接看不到，便于升级生产后只做内测。
const expOnly = (to: { query: Record<string, unknown> }, _from: unknown, next: (loc?: object) => void) => {
  if (to.query.exp === 'true') next()
  else next({ name: 'Home' })
}

const WorkspaceRoutes: RouteRecordRaw[] = [
  {
    // 现场全屏：整页一个 Claude Code、无边框（新标签打开）。放在带 projectId 的路由之前、
    // 段数不同不会冲突，但显式在前更稳。
    path: '/workspace/scene/:deviceId/:sid',
    name: 'WorkspaceScene',
    component: () => import('@/views/workspace/SceneFullView.vue'),
    meta: { title: '现场', hideAppBar: true },
    beforeEnter: expOnly,
  },
  {
    // 聊天是全局功能（与项目无关），所以是无参路由 /chat。
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/chat/ChatView.vue'),
    meta: { title: '聊天' },
    beforeEnter: expOnly,
  },
  {
    // 项目文档工作区：左侧文档树、右侧 markdown 编辑器。projectId 可选，缺省时先选项目。
    path: '/documents/:projectId?',
    name: 'ProjectDocuments',
    component: () => import('@/views/workspace/ProjectView.vue'),
    meta: { title: '项目文档' },
    beforeEnter: expOnly,
  },
]

export default WorkspaceRoutes
