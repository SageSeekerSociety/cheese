import type { RouteRecordRaw } from 'vue-router'

import RouterPassThrough from '@/layouts/RouterPassThrough.vue'

export default {
  path: '/teams',
  // 团队的几页开在首页侧栏旁边：侧栏里这个团队展开着，点它的哪一样，右边就换哪一样。
  components: {
    default: RouterPassThrough,
    sidebar: () => import('@/views/home/HomeSidebar.vue'),
  },
  meta: {
    titleKey: 'navigation.teams',
  },
  children: [
    {
      // A team is addressed by its handle (`/teams/zhishi`); a personal team by
      // its owner's username.
      path: ':handle',
      // 这一层没有名字：带名字跳到父路由，vue-router 不画 path 为 '' 的那个子页，
      // 落地就只剩页头。进团队页一律用 `TeamsDetailDefault`。浏览器标题里的团队名挂
      // 在 `dynamicTitleKey` 上（Detail.vue 按这个键填）。
      component: () => import('@/views/teams/Detail.vue'),
      // 手机上团队的几页从底栏「首页」那一格的目录进来，← 回到那里。
      meta: { titleKey: 'navigation.teams', dynamicTitleKey: 'TeamsDetail', isFullPage: true, backTo: 'HomeHub' },
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
        {
          // 团队本月的额度用了多少；个人团队没有这一页，个人的在个人设置里。
          path: 'credits',
          name: 'TeamsDetailCredits',
          component: () => import('@/views/teams/detail/Credits.vue'),
        },
      ],
    },
  ],
} as RouteRecordRaw
