import type { RouteRecordRaw } from 'vue-router'

import RouterPassThrough from '@/layouts/RouterPassThrough.vue'

export default {
  path: '/teams',
  component: RouterPassThrough,
  meta: {
    title: '小队',
  },
  children: [
    {
      path: ':teamId',
      name: 'TeamsDetail',
      component: () => import('@/views/teams/Detail.vue'),
      children: [
        {
          // 项目 is the team's default tab (项目归团队, v4). Channels/discussions
          // were retired with them — project topics are the conversation surface.
          path: '',
          name: 'TeamsDetailDefault',
          component: () => import('@/views/teams/detail/TeamProjects.vue'),
        },
        {
          path: 'members',
          name: 'TeamsDetailMembers',
          component: () => import('@/views/teams/detail/Members.vue'),
        },
        {
          path: 'knowledge',
          name: 'TeamsDetailKnowledge',
          component: () => import('@/views/teams/detail/Knowledge.vue'),
        },
        {
          path: 'compute',
          name: 'TeamsDetailCompute',
          component: () => import('@/views/teams/detail/Compute.vue'),
        },
      ],
    },
  ],
} as RouteRecordRaw
