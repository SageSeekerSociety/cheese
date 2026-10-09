import type { RouteLocationRaw, RouteRecordRaw } from 'vue-router'

/** 登录的人该落在哪：上次待的项目，读不到清单就落在待办。
 *
 *  根路由守卫用它，会话恢复层（components/common/SessionRestoreGate.vue）也用它
 *  ——弱网下恢复成功时首屏那次导航早就结束了，得把这份决定重走一遍。 */
export async function landingForMember(): Promise<RouteLocationRaw> {
  const [{ queryClient }, { projectsQuery }, { workspaceProject }, { lastOpenedProjectId }] = await Promise.all([
    import('@/query/client'),
    import('@/query/projects'),
    import('@/components/common/Navigation/destinations'),
    import('@/stores/workspace'),
  ])
  try {
    // 和左边栏读的是同一份：刚读过就不再问。
    const projects = await queryClient.fetchQuery(projectsQuery())
    const projectId = workspaceProject(projects, null, lastOpenedProjectId())
    if (projectId) return { name: 'workspace-project', params: { projectId } }
  } catch {
    // 项目清单读不到时落在待办上：那一页不依赖这份清单也能用。
  }
  return { name: 'inbox' }
}

// 推广页（了解知是、方案、下载）是写给还没装上的人看的。桌面 app 里的人已经装上了，
// 这几页在 app 的窗口里既没有意义、也没有回去的路：一律回到首页那一格，由它决定
// 落在工作台还是登录页。app 里对应的东西各有去处——「关于知是」对话框（版本与检查
// 更新）、用户菜单的「在手机上使用」，推广页本身在浏览器里打开。
async function awayFromMarketingInApp(): Promise<RouteLocationRaw | true> {
  const { inDesktopApp } = await import('@/lib/desktopApp')
  return inDesktopApp() ? { name: 'HomeDefault' } : true
}

export default {
  path: '/',
  name: 'Home',
  components: {
    // 桌面上左边是首页侧栏（待办、团队、空间）；手机上没有侧栏，同一份目录是底栏
    // 「首页」那一格的整页（HomeHub）。
    default: () => import('@/layouts/home/Home.vue'),
    sidebar: () => import('@/views/home/HomeSidebar.vue'),
  },
  children: [
    {
      path: '',
      name: 'HomeDefault',
      meta: {
        titleKey: 'navigation.home',
        publicLanding: true,
      },
      component: () => import('@/views/home/Landing.vue'),
      // 登录后落回上次待的那个项目：每天的第一件事是接着干活。一个项目都没有的人
      // 没有地方可回，落在待办上——那一页给他新建项目、用邀请码加入空间两条路。
      beforeEnter: async () => {
        // AccountService's API client imports the router; load it after route construction.
        const { default: AccountService } = await import('@/services/account')
        // 冷打开时登录态可能还在恢复（过期令牌要先换新），不等就把回访用户
        // 当成生人：先给他看推广页，恢复完再跳走——或者恢复得比推广页挂载
        // 还快，那一跳就没人接，页面就停在推广页上。
        await AccountService.sessionRestored
        if (AccountService.loggedIn) return await landingForMember()
        // 桌面 app 是已经装上的人在用，推广页对他没有意义：没登录就直接去登录。
        const { inDesktopApp } = await import('@/lib/desktopApp')
        return inDesktopApp() ? { name: 'SignIn' } : true
      },
    },
    {
      path: 'about',
      name: 'About',
      meta: {
        titleKey: 'publicSite.aboutCheese',
        publicLanding: true,
      },
      beforeEnter: awayFromMarketingInApp,
      component: () => import('@/views/home/Landing.vue'),
    },
    {
      // 写给把知是引进来的一方：学校、企业、科研团队。首页写给做项目的人。
      path: 'solutions',
      name: 'Solutions',
      meta: {
        titleKey: 'publicSite.solutionsPage.title',
        publicLanding: true,
      },
      beforeEnter: awayFromMarketingInApp,
      component: () => import('@/views/home/Solutions.vue'),
    },
    {
      // 桌面 app 和手机的下载页。登录与否都能打开：用户菜单的「下载客户端」也到这里。
      path: 'download',
      name: 'Download',
      meta: {
        titleKey: 'publicSite.downloadPage.title',
        publicLanding: true,
      },
      beforeEnter: awayFromMarketingInApp,
      component: () => import('@/views/home/Download.vue'),
    },
    {
      // 首页那一格点开就是这一页：等你处理的事，加上提到你、回复你的动态。
      name: 'inbox',
      path: 'inbox',
      component: () => import('@/views/InboxView.vue'),
      meta: {
        titleKey: 'navigation.inbox',
        isFullPage: true,
        palette: { label: 'navigation.inbox', icon: 'mdi-inbox-outline' },
      },
    },
    {
      // 手机底栏「首页」那一格：团队和空间的目录。桌面上这份目录常驻在侧栏里。
      name: 'HomeHub',
      path: 'home',
      component: () => import('@/views/home/HomeHub.vue'),
      meta: {
        titleKey: 'navigation.home',
        isFullPage: true,
      },
    },
    {
      path: 'spaces',
      name: 'HomeSpaces',
      component: () => import('@/views/spaces/Index.vue'),
      meta: {
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
        titleKey: 'navigation.teams',
        isFullPage: true,
      },
      children: [
        {
          path: 'explore',
          name: 'HomeTeamsExplore',
          component: () => import('@/views/teams/Explore.vue'),
          meta: {
            titleKey: 'navigation.pages.teamsExplore',
            isFullPage: true,
          },
        },
        {
          path: 'mine',
          name: 'HomeTeamsMine',
          component: () => import('@/views/teams/Mine.vue'),
          meta: {
            titleKey: 'navigation.pages.teamsMine',
            isFullPage: true,
          },
        },
        {
          path: 'pending',
          name: 'HomeTeamsPending',
          component: () => import('@/views/teams/Pending.vue'),
          meta: {
            titleKey: 'navigation.pages.teamsPending',
            isFullPage: true,
          },
        },
      ],
    },
  ],
} as RouteRecordRaw
