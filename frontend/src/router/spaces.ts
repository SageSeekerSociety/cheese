import type { RouteRecordRaw } from 'vue-router'

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
              path: 'ai-advice',
              name: 'TasksAIAdvice',
              component: () => import('@/views/tasks/detail/AIAdvice.vue'),
              meta: { titleKey: 'navigation.pages.taskAdvice' },
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
    {
      path: 'manage/audit',
      name: 'SpacesDetailAuditTasks',
      component: () => import('@/views/spaces/detail/AuditTask.vue'),
    },
    {
      path: 'manage/members',
      name: 'SpacesDetailMembers',
      component: () => import('@/views/spaces/detail/Members.vue'),
    },
    {
      path: 'manage/analytics',
      name: 'SpacesDetailAnalytics',
      component: () => import('@/views/spaces/detail/analytics/Index.vue'),
      redirect: { name: 'SpacesDetailAnalyticsOverview' },
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
    {
      path: 'select-template',
      name: 'SpacesDetailSelectTemplate',
      meta: { backTo: 'SpacesDetailTasksList' },
      component: () => import('@/views/spaces/detail/SelectTemplate.vue'),
    },
  ],
} as RouteRecordRaw
