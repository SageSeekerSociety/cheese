import type { RouteRecordRaw } from 'vue-router'

export default {
  path: '/',
  name: 'Home',
  components: {
    // 空间/小队这两格在手机上是页内分段，在桌面上是左边那条侧栏 —— 两种形态
    // 住在同一个外框里，见 layouts/home/Home.vue。
    default: () => import('@/layouts/home/Home.vue'),
    sidebar: () => import('@/components/home/HomeSidebar.vue'),
  },
  // 手机顶栏在这一层写「首页」，不写子页的名字：空间和小队已经在页内那行分段上
  // 各自写了一遍，顶栏再写一遍就是同一个词上下叠两次（/teams 更是「发现小队」
  // 「小队」「发现」三层同义）。子路由的 title 照常给面包屑和标签页标题用。
  meta: { mobileTitle: '首页' },
  children: [
    {
      path: '',
      name: 'HomeDefault',
      meta: {
        title: '首页',
      },
      redirect: { name: 'HomeSpaces' },
    },
    {
      path: 'spaces',
      name: 'HomeSpaces',
      component: () => import('@/views/spaces/Index.vue'),
      meta: {
        title: '空间',
        isFullPage: true,
      },
    },
    {
      path: 'teams',
      name: 'HomeTeams',
      component: () => import('@/views/teams/Index.vue'),
      redirect: { name: 'HomeTeamsExplore' },
      meta: {
        title: '小队',
        isFullPage: true,
      },
      children: [
        {
          path: 'explore',
          name: 'HomeTeamsExplore',
          component: () => import('@/views/teams/Explore.vue'),
          meta: {
            title: '发现小队',
            isFullPage: true,
          },
        },
        {
          path: 'mine',
          name: 'HomeTeamsMine',
          component: () => import('@/views/teams/Mine.vue'),
          meta: {
            title: '我的小队',
            isFullPage: true,
          },
        },
        {
          path: 'pending',
          name: 'HomeTeamsPending',
          component: () => import('@/views/teams/Pending.vue'),
          meta: {
            title: '申请与邀请',
            isFullPage: true,
          },
        },
      ],
    },
  ],
} as RouteRecordRaw
