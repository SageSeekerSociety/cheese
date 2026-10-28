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
          // 团队的资料库：团队自己的文档（#3774）和文件在一张表里，和项目的资料库一样。个人
          // 名下的就是自己的资料库。打开的一份文档挂在它下面，侧栏的「资料库」也就亮着。
          path: 'library',
          component: RouterPassThrough,
          children: [
            {
              path: '',
              name: 'TeamsDetailLibrary',
              component: () => import('@/views/teams/detail/Library.vue'),
            },
            {
              // 其中一份，整页打开：不套团队页的外框，页头写「某团队的资料库 / 标题」。
              path: 'docs/:docId',
              name: 'TeamsDocument',
              component: () => import('@/views/teams/TeamDocument.vue'),
              props: true,
            },
          ],
        },
        {
          // 文档原先单独一页，并进资料库以后发出去的旧链接照旧能用。
          path: 'docs',
          redirect: (to) => ({ name: 'TeamsDetailLibrary', params: { handle: to.params.handle } }),
        },
        {
          path: 'docs/:docId',
          redirect: (to) => ({ name: 'TeamsDocument', params: { handle: to.params.handle, docId: to.params.docId } }),
        },
        // 设置：盖在整个窗口上的一层（Detail.vue 画 `TeamSettings`），和个人、空间、项目的
        // 设置同一个外框。每一栏照旧是自己的地址；`settings` 在桌面上落到第一栏，手机上是目录。
        {
          path: 'settings',
          name: 'TeamsDetailSettings',
          component: RouterPassThrough,
          meta: { settingsOverlay: true, hideTabs: true },
        },
        {
          // 名称、头像、简介和团队地址。
          path: 'profile',
          name: 'TeamsDetailProfile',
          component: () => import('@/views/teams/detail/Profile.vue'),
          meta: { settingsOverlay: true, hideTabs: true },
        },
        {
          path: 'members',
          name: 'TeamsDetailMembers',
          component: () => import('@/views/teams/detail/Members.vue'),
          meta: { settingsOverlay: true, hideTabs: true },
        },
        {
          path: 'compute',
          name: 'TeamsDetailCompute',
          component: () => import('@/views/teams/detail/Compute.vue'),
          meta: { settingsOverlay: true, hideTabs: true },
        },
        {
          path: 'models',
          name: 'TeamsDetailModels',
          component: () => import('@/views/teams/detail/Models.vue'),
          meta: { settingsOverlay: true, hideTabs: true },
        },
        {
          // Credits belong to the team, including the personal team.
          path: 'credits',
          name: 'TeamsDetailCredits',
          component: () => import('@/views/teams/detail/Credits.vue'),
          meta: { settingsOverlay: true, hideTabs: true },
        },
        {
          // 所有者在这里转让或解散团队，其他成员在这里退出。
          path: 'leave',
          name: 'TeamsDetailLeave',
          component: () => import('@/views/teams/detail/Leave.vue'),
          meta: { settingsOverlay: true, hideTabs: true },
        },
      ],
    },
  ],
} as RouteRecordRaw
