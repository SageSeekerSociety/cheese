import type { RouteRecordRaw } from 'vue-router'

export default {
  path: '/workspace',
  name: 'Workspace',
  component: () => import('@/views/connector/WorkspaceView.vue'),
  meta: {
    title: '项目',
    isFullPage: true,
  },
} as RouteRecordRaw
