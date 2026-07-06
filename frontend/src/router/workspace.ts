import type { RouteRecordRaw } from 'vue-router'

// 知是 2.0 workspace. Experimental: only reachable with ?experimental=true, so it
// ships dark until we choose to surface it. Takes the full viewport (its own rail
// replaces the global chrome).
const WorkspaceRoutes: RouteRecordRaw = {
  path: '/workspace/:projectId?',
  name: 'Workspace',
  component: () => import('@/views/workspace/WorkspaceView.vue'),
  props: true,
  meta: {
    title: '工作区',
    hideAppBar: true,
  },
  beforeEnter: (to, _from, next) => {
    if (to.query.experimental === 'true') next()
    else next({ name: 'Home' })
  },
}

export default WorkspaceRoutes
