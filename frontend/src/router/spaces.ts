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
        title: '题目',
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
          path: 'my/publishing',
          name: 'SpacesDetailMyPublishing',
          components: {
            default: () => import('@/views/spaces/detail/member-tasks/MyPublishing.vue'),
            header: () => import('@/components/common/PageHeader.vue'),
          },
          meta: {
            titleKey: 'spaces.detail.myPublishedContests',
            backTo: 'SpacesDetailTasksList',
          },
        },
        {
          path: 'my/participating',
          name: 'SpacesDetailMyParticipating',
          components: {
            default: () => import('@/views/spaces/detail/member-tasks/MyParticipating.vue'),
            header: () => import('@/components/common/PageHeader.vue'),
          },
          meta: {
            titleKey: 'spaces.detail.myJoinedContests',
            backTo: 'SpacesDetailTasksList',
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
          // 题目详情：页面本身画题目、领取与出题人的领取者名单，下面四格是子页。
          path: ':taskId',
          name: 'TasksDetail',
          component: () => import('@/views/tasks/Detail.vue'),
          meta: {
            title: '题目',
            backTo: 'SpacesDetailTasksList',
          },
          children: [
            {
              path: 'submissions',
              name: 'TasksSubmissions',
              component: () => import('@/views/tasks/detail/Submissions.vue'),
              meta: {
                title: '提交记录',
                disableBreadcrumbLink: true,
              },
            },
            {
              path: 'participants',
              name: 'TasksParticipants',
              component: () => import('@/views/tasks/detail/Participants.vue'),
              meta: {
                title: '参与者管理',
                disableBreadcrumbLink: true,
              },
            },
            {
              path: 'submit',
              name: 'TasksSubmit',
              component: () => import('@/views/tasks/detail/Submit.vue'),
              meta: {
                title: '提交',
                backTo: 'TasksDetail',
                disableBreadcrumbLink: true,
              },
            },
            {
              path: 'ai-advice',
              name: 'TasksAIAdvice',
              component: () => import('@/views/tasks/detail/AIAdvice.vue'),
              meta: {
                title: '启星研导',
                disableBreadcrumbLink: true,
              },
            },
          ],
        },
        {
          // 单题分析：出题人和管理员看这道题的领取、提交与走势。
          path: ':taskId/insights',
          name: 'TasksInsights',
          component: () => import('@/views/tasks/Insights.vue'),
          meta: {
            title: '单题分析',
            backTo: 'TasksDetail',
          },
        },
        {
          path: ':taskId/edit',
          name: 'TasksEdit',
          component: () => import('@/views/tasks/Edit.vue'),
          meta: {
            title: '编辑题目',
            backTo: 'TasksDetail',
          },
        },
      ],
    },
    {
      path: 'tasks/audit',
      name: 'SpacesDetailAuditTasks',
      component: () => import('@/views/spaces/detail/AuditTask.vue'),
    },
    {
      path: 'members',
      name: 'SpacesDetailMembers',
      component: () => import('@/views/spaces/detail/Members.vue'),
    },
    {
      path: 'templates',
      name: 'SpacesDetailManageTemplates',
      component: () => import('@/views/spaces/detail/ManageTemplates.vue'),
    },
    {
      path: 'templates/create',
      name: 'SpacesDetailCreateTemplate',
      meta: { backTo: 'SpacesDetailManageTemplates' },
      component: () => import('@/views/spaces/detail/TemplateForm.vue'),
    },
    {
      path: 'templates/:templateIndex/edit',
      name: 'SpacesDetailEditTemplate',
      meta: { backTo: 'SpacesDetailManageTemplates' },
      component: () => import('@/views/spaces/detail/TemplateForm.vue'),
    },
    {
      path: 'select-template',
      name: 'SpacesDetailSelectTemplate',
      meta: { backTo: 'SpacesDetailTasksList' },
      component: () => import('@/views/spaces/detail/SelectTemplate.vue'),
    },
    {
      path: 'analytics',
      name: 'SpacesDetailAnalytics',
      component: () => import('@/views/spaces/detail/analytics/Index.vue'),
      redirect: { name: 'SpacesDetailAnalyticsOverview' },
      meta: {
        title: '数据分析',
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
      path: 'manage/topics',
      name: 'SpacesDetailManageTopics',
      component: () => import('@/views/spaces/detail/ManageTopics.vue'),
    },
    {
      path: 'manage/categories',
      name: 'SpacesDetailManageCategories',
      component: () => import('@/views/spaces/detail/ManageCategories.vue'),
    },
    {
      path: 'manage/domain-groups',
      name: 'SpacesDetailManageDomainGroups',
      component: () => import('@/views/spaces/detail/ManageDomainGroups.vue'),
    },
    {
      path: 'manage/invite-codes',
      name: 'SpacesDetailManageInviteCodes',
      component: () => import('@/views/spaces/detail/InviteCodes.vue'),
    },
  ],
} as RouteRecordRaw
