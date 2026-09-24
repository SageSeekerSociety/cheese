import type { RouteRecordRaw } from 'vue-router'

export default {
  path: '/',
  name: 'Home',
  components: {
    // 我的工作/空间/小队这几格在手机上是页内分段，在桌面上是左边那条侧栏 ——
    // 两种形态住在同一个外框里，见 layouts/home/Home.vue。
    default: () => import('@/layouts/home/Home.vue'),
    sidebar: () => import('@/components/home/HomeSidebar.vue'),
  },
  // 手机上「我的工作 / 空间 / 小队」那组分段**就在顶栏里**（layouts/home/Home.vue
  // 把它 Teleport 进去），所以这一层不写标题——写了就是同一个词上下叠两次。
  meta: { barSlot: true },
  children: [
    {
      path: '',
      name: 'HomeDefault',
      meta: {
        title: '首页',
        titleKey: 'navigation.home',
        publicLanding: true,
      },
      component: () => import('@/views/home/Landing.vue'),
      // 登录后落地的是**我的工作**（工作页），不是空间列表：第一屏要答的是
      // 「我手上有什么、什么在等我」，而空间是别人开的地方（见 views/home/MyWork.vue
      // 顶上那段）。空间列表没有消失，它是那一页顶部的一排，`/spaces` 这个地址也
      // 原样留着。
      beforeEnter: async () => {
        // AccountService's API client imports the router; load it after route construction.
        const { default: AccountService } = await import('@/services/account')
        // 冷打开时登录态可能还在恢复（过期令牌要先换新），不等就把回访用户
        // 当成生人：先给他看推广页，恢复完再跳走——或者恢复得比推广页挂载
        // 还快，那一跳就没人接，页面就停在推广页上。
        await AccountService.sessionRestored
        return AccountService.loggedIn ? { name: 'HomeWork' } : true
      },
    },
    {
      path: 'about',
      name: 'About',
      meta: {
        title: '了解知是',
        titleKey: 'publicSite.aboutCheese',
        publicLanding: true,
      },
      component: () => import('@/views/home/Landing.vue'),
    },
    {
      // 我的工作：登录后的首页（`/` 把已登录的人送到这儿）。不进任何项目就能看见
      // 我手上的每一个项目、它们的壳、以及每个项目最近在发生什么，点一张卡直接
      // 进去。手机上是这一层的第一格分段。
      path: 'work',
      name: 'HomeWork',
      component: () => import('@/views/home/MyWork.vue'),
      meta: {
        title: '我的工作',
        titleKey: 'navigation.myWork',
        isFullPage: true,
      },
    },
    {
      path: 'spaces',
      name: 'HomeSpaces',
      component: () => import('@/views/spaces/Index.vue'),
      meta: {
        title: '空间',
        titleKey: 'navigation.spaces',
        isFullPage: true,
      },
    },
    {
      path: 'teams',
      name: 'HomeTeams',
      component: () => import('@/views/teams/Index.vue'),
      // 落在「我的」：发现是有意图才去的一段，而从底栏点进来的人是回自己队里。
      redirect: { name: 'HomeTeamsMine' },
      meta: {
        title: '团队',
        titleKey: 'navigation.teams',
        isFullPage: true,
      },
      children: [
        {
          path: 'explore',
          name: 'HomeTeamsExplore',
          component: () => import('@/views/teams/Explore.vue'),
          meta: {
            title: '发现团队',
            isFullPage: true,
          },
        },
        {
          path: 'mine',
          name: 'HomeTeamsMine',
          component: () => import('@/views/teams/Mine.vue'),
          meta: {
            title: '我的团队',
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
