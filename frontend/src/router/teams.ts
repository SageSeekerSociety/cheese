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
          // 团队的文档（#3774）：只属于团队、不属于哪个项目的那些。个人名下的就是自己的文档。
          // 打开的一份挂在它下面，侧栏的「文档」也就亮着。
          path: 'docs',
          component: RouterPassThrough,
          children: [
            {
              path: '',
              name: 'TeamsDetailDocs',
              component: () => import('@/views/teams/detail/Documents.vue'),
            },
            {
              // 其中一份，整页打开：不套团队页的外框，页头写「某团队的文档 / 标题」。
              path: ':docId',
              name: 'TeamsDocument',
              component: () => import('@/views/teams/TeamDocument.vue'),
              props: true,
            },
          ],
        },
        {
          path: 'compute',
          name: 'TeamsDetailCompute',
          component: () => import('@/views/teams/detail/Compute.vue'),
        },
        {
          path: 'models',
          name: 'TeamsDetailModels',
          component: () => import('@/views/teams/detail/Models.vue'),
        },
        {
          // Credits belong to the team, including the personal team.
          path: 'credits',
          name: 'TeamsDetailCredits',
          component: () => import('@/views/teams/detail/Credits.vue'),
        },
      ],
    },
  ],
} as RouteRecordRaw
