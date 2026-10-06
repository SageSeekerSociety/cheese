import type { RouteLocationNormalized, RouteLocationRaw, RouteRecordRaw } from 'vue-router'

/**
 * `/spaces/:spaceId/manage/*` 的门：只有这个空间的所有者与管理员能进。
 *
 * 在这之前管理那一段（待审核、成员、数据、设置）没有任何守卫 —— 非管理员把地址
 * 直接敲进来就进得去，页面自己去拉只有管理员能读的接口，换回来一个 403，屏幕上
 * 是一个原始报错框。现在判据提到路由层：不是管理员就领到 `SpaceManageDenied`，
 * 画的是一扇说清楚「为什么进不去、下一步去哪」的门（和项目页那扇同一个形状，
 * 组件是 `components/common/AccessNotice.vue`）。
 *
 * 判据是这个空间的管理员名单（`useSpaceStore` 的 `isManager`，和侧栏「管理」那一栏
 * 的显隐同源，不是另抄一份）。名单随这块板的详情一起来，所以这里按需先取一次 ——
 * 和页面外壳（`views/spaces/Detail.vue`）取的是同一份、同一条路
 * （`useSpaceData.fetchSpace`）。取数失败它自己会提示；这里只按「没拿到 = 不是
 * 管理员」往下判，于是不存在、也不属于我的板同样进不了管理那一段。
 *
 * 动态 `import()`：`@/network/api` 那条链反过来依赖路由（见
 * `network/Interceptors/hooks/refreshToken.ts`），静态导入会在建路由时成环。守卫
 * 第一次跑到时整张依赖图早已就绪，所以这一句是懒的、不是慢的。
 */
export async function spaceManageGuard(to: RouteLocationNormalized): Promise<RouteLocationRaw | true> {
  const spaceId = Number(to.params.spaceId)
  if (!Number.isInteger(spaceId) || spaceId <= 0) return true

  const [{ useSpaceStore }, { useSpaceData }, { default: account }] = await Promise.all([
    import('@/stores/space'),
    import('@/composables/useSpaceData'),
    import('@/services/account'),
  ])
  // 「我是谁」在冷打开、访问令牌过期时要等会话恢复完才落定；不等的话，一个真管理员
  // 会被自己刚打开的那一页挡在门外。恢复完好后 `isManager` 才读得到正确的名单比对。
  await account.sessionRestored
  const store = useSpaceStore()
  if (store.currentSpace?.id !== spaceId) await useSpaceData().fetchSpace(spaceId)
  if (store.isManager) return true
  return { name: 'SpaceManageDenied', params: { spaceId: String(spaceId) } }
}

/**
 * 一个空间（`/spaces/:spaceId/…`）。侧栏是 `SpaceSidebar.vue`，每一页的标题行由
 * 页面自己的 `PageHeader` 画，和侧栏顶那一行等高。
 */
export default {
  path: '/spaces/:spaceId',
  name: 'SpacesDetail',
  components: {
    default: () => import('@/views/spaces/Detail.vue'),
    sidebar: () => import('@/components/spaces/SpaceSidebar.vue'),
  },
  meta: {
    isFullPage: true,
    backTo: 'HomeSpaces',
    // 手机上 SpaceSidebar 是抽屉，所以顶栏给汉堡。
    drawer: true,
  },
  redirect: { name: 'SpacesDetailTasks' },
  children: [
    {
      path: 'announcements',
      name: 'SpacesAnnouncements',
      component: () => import('@/views/spaces/detail/Announcements.vue'),
      meta: { titleKey: 'spaces.detail.announcements' },
    },
    {
      path: 'tasks',
      name: 'SpacesDetailTasks',
      component: () => import('@/layouts/spaces/SpacesTasks.vue'),
      redirect: { name: 'SpacesDetailTasksList' },
      meta: {
        titleKey: 'navigation.pages.spaceTasks',
      },
      children: [
        {
          path: '',
          name: 'SpacesDetailTasksList',
          components: {
            default: () => import('@/views/spaces/detail/Tasks.vue'),
            header: () => import('@/components/common/PageHeader.vue'),
          },
        },
        {
          path: 'publish',
          name: 'SpacesDetailPublishTask',
          // 页头由页面自己画：那两条路的切换放在页头的操作区里。
          component: () => import('@/views/spaces/detail/PublishTask.vue'),
          meta: {
            titleKey: 'tasks.publish.title',
            backTo: 'SpacesDetailTasksList',
          },
        },
        {
          // 题目详情：页面本身画题目名、主操作与页签，页签内容是下面这几条子路由，
          // 地址各自不变。浏览器标题里的题目名挂在 `dynamicTitleKey` 上（这一层没有名字）。
          path: ':taskId',
          component: () => import('@/views/tasks/Detail.vue'),
          meta: {
            dynamicTitleKey: 'TaskShell',
            backTo: 'SpacesDetailTasksList',
          },
          children: [
            {
              path: '',
              name: 'TasksDetail',
              component: () => import('@/views/tasks/detail/Brief.vue'),
            },
            {
              path: 'submissions',
              name: 'TasksSubmissions',
              component: () => import('@/views/tasks/detail/Submissions.vue'),
              meta: { titleKey: 'navigation.pages.taskSubmissions' },
            },
            {
              path: 'submit',
              name: 'TasksSubmit',
              component: () => import('@/views/tasks/detail/Submit.vue'),
              meta: { titleKey: 'navigation.pages.taskSubmit', backTo: 'TasksDetail' },
            },
            {
              path: 'participants',
              name: 'TasksParticipants',
              component: () => import('@/views/tasks/detail/Roster.vue'),
              meta: { titleKey: 'navigation.pages.taskParticipants' },
            },
            {
              path: 'insights',
              name: 'TasksInsights',
              component: () => import('@/views/tasks/detail/Insights.vue'),
              meta: { titleKey: 'navigation.pages.taskData' },
            },
          ],
        },
        {
          path: ':taskId/edit',
          name: 'TasksEdit',
          component: () => import('@/views/tasks/Edit.vue'),
          meta: {
            titleKey: 'navigation.pages.taskEdit',
            backTo: 'TasksDetail',
          },
        },
      ],
    },
    // 管理那一段（所有者与管理员）：都在 manage/ 下，侧栏「管理」四格各对一条。
    // 这四条都挂着 `spaceManageGuard`（见文件头）：非管理员直接被领到下面那条
    // `SpaceManageDenied`，不再靠页面自己拉到 403 才报错。
    {
      // 非管理员被守卫领到的落脚处。它自己**不挂**守卫（挂了会自己领自己），画的
      // 是「你没有管理权限 + 回到题目列表」那扇门。
      path: 'manage/denied',
      name: 'SpaceManageDenied',
      component: () => import('@/views/spaces/detail/ManageDenied.vue'),
    },
    {
      path: 'manage/audit',
      name: 'SpacesDetailAuditTasks',
      component: () => import('@/views/spaces/detail/AuditTask.vue'),
      beforeEnter: spaceManageGuard,
    },
    {
      path: 'manage/members',
      name: 'SpacesDetailMembers',
      component: () => import('@/views/spaces/detail/Members.vue'),
      beforeEnter: spaceManageGuard,
    },
    {
      path: 'manage/analytics',
      name: 'SpacesDetailAnalytics',
      component: () => import('@/views/spaces/detail/analytics/Index.vue'),
      redirect: { name: 'SpacesDetailAnalyticsOverview' },
      beforeEnter: spaceManageGuard,
      meta: {
        titleKey: 'navigation.pages.spaceAnalytics',
      },
      children: [
        {
          path: '',
          name: 'SpacesDetailAnalyticsOverview',
          component: () => import('@/views/spaces/detail/analytics/Overview.vue'),
        },
        {
          path: 'alerts',
          name: 'SpacesDetailAnalyticsAlerts',
          component: () => import('@/views/spaces/detail/analytics/Alerts.vue'),
        },
        {
          path: 'publishers',
          name: 'SpacesDetailAnalyticsPublishers',
          component: () => import('@/views/spaces/detail/analytics/Publishers.vue'),
        },
        {
          path: 'tasks',
          name: 'SpacesDetailAnalyticsTasks',
          component: () => import('@/views/spaces/detail/analytics/Tasks.vue'),
        },
        {
          path: 'participants',
          name: 'SpacesDetailAnalyticsParticipants',
          component: () => import('@/views/spaces/detail/analytics/Participants.vue'),
        },
        {
          // 学习读的是成员项目里的对话，上面五格读的是赛题与报名表 —— 两套数据，
          // 所以筛选那一栏里它只认时间，成员与知识点是这一格自己的。
          path: 'learning',
          name: 'SpacesDetailAnalyticsLearning',
          component: () => import('@/views/spaces/detail/analytics/Learning.vue'),
        },
      ],
    },
    {
      // 设置：盖在整个窗口上的一层，每一栏是一条子路由，可以单独链接。这一条自己的
      // 地址在桌面上落到「基本信息」，手机上是目录（settings/Index.vue）。
      path: 'manage/settings',
      name: 'SpacesDetailSettings',
      component: () => import('@/views/spaces/detail/settings/Index.vue'),
      beforeEnter: spaceManageGuard,
      meta: { titleKey: 'spaces.settings.title', settingsOverlay: true, hideTabs: true },
      children: [
        {
          path: 'basic',
          name: 'SpacesDetailSettingsBasic',
          component: () => import('@/views/spaces/detail/settings/BasicInfo.vue'),
        },
        {
          // 分类与话题是两页现成的管理页，并排画在这一栏里：设置页给话题留了一个具名出口。
          path: 'categories',
          name: 'SpacesDetailSettingsCategories',
          components: {
            default: () => import('@/views/spaces/detail/ManageCategories.vue'),
            topics: () => import('@/views/spaces/detail/ManageTopics.vue'),
          },
        },
        {
          path: 'templates',
          name: 'SpacesDetailSettingsTemplates',
          component: () => import('@/views/spaces/detail/ManageTemplates.vue'),
        },
        {
          path: 'invite-codes',
          name: 'SpacesDetailSettingsInviteCodes',
          component: () => import('@/views/spaces/detail/InviteCodes.vue'),
        },
        {
          path: 'domain-groups',
          name: 'SpacesDetailSettingsDomainGroups',
          component: () => import('@/views/spaces/detail/ManageDomainGroups.vue'),
        },
        {
          path: 'guidance',
          name: 'SpacesDetailSettingsGuidance',
          component: () => import('@/views/spaces/detail/settings/SpaceGuidance.vue'),
        },
        {
          path: 'materials',
          name: 'SpacesDetailSettingsMaterials',
          component: () => import('@/views/spaces/detail/settings/SpaceMaterials.vue'),
        },
        {
          // 模板的新建和编辑是「题目模板」下一级的整页表单，画在同一层里。
          path: 'templates/create',
          name: 'SpacesDetailCreateTemplate',
          meta: { backTo: 'SpacesDetailSettingsTemplates' },
          component: () => import('@/views/spaces/detail/TemplateForm.vue'),
        },
        {
          path: 'templates/:templateIndex/edit',
          name: 'SpacesDetailEditTemplate',
          meta: { backTo: 'SpacesDetailSettingsTemplates' },
          component: () => import('@/views/spaces/detail/TemplateForm.vue'),
        },
      ],
    },
  ],
} as RouteRecordRaw
