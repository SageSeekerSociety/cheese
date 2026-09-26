import type { RouteRecordRaw } from 'vue-router'

/**
 * 老的空间树（`/spaces/:spaceId/…`）。
 *
 * **2026-09-26 起它不再是「进去」的落点。** 进一块板一律落在新题目板
 * （`/spaces/:id/board`，见 `lib/courseNav.ts` 的 `spaceEntryRoute`）；这一棵现在的
 * 身份是**管理面与课程面**：题目板头部那块下拉带人来的「编辑信息」「管理员设置」，
 * 以及课那几屏（`course/*`，题目板外壳里那格「课程」）。
 *
 * **下面这几屏已经被题目板接手了，但一个都没删**：题目列表 → 题目板首页、我的发布 /
 * 我的参与 → 「我的」、公告板 → 「公告」、审核题目 → 「审核」、题目详情 / 发题 /
 * 九个分析页 → 第五批包进新外壳的同名页。留着的理由不是舍不得，是**删了会留死链，
 * 而那些链来自不会退场的屏**：
 * - `SpaceSidebar.vue`（老树自己的侧栏）每一格都指着它们；
 * - 课那几屏指着它们 —— `course/Assignments.vue` 的发题按钮走 `SpacesDetailPublishTask`，
 *   课程面是要长期在的；
 * - 老树内部互相指着（`detail/Tasks.vue` → 详情/我的参与、`MyPublishing.vue` → 发题、
 *   新外壳的 `board/routes.ts` 把 `SpacesDetailTasksList` 当作详情页的 `backTo`）；
 * - 已经发出去的地址（书签、通知里的链接）没有重定向会直接 404。
 *
 * 所以退场的次序是：**先在老树内部把这些指针对到题目板上**，再删页。这一步没做，
 * 因为「老树内部那一批指针」和「老树要不要整个收成一条重定向」是同一件事的两半，
 * 得一起定 —— 属于下一批。
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
    // 这一棵下面还挂着 SpaceSidebar，手机上它是抽屉，所以顶栏给汉堡。
    drawer: true,
  },
  redirect: { name: 'SpacesDetailTasks' },
  children: [
    {
      path: 'announcements',
      name: 'SpacesAnnouncements',
      component: () => import('@/views/spaces/detail/Announcements.vue'),
    },
    {
      path: 'tasks',
      name: 'SpacesDetailTasks',
      component: () => import('@/layouts/spaces/SpacesTasks.vue'),
      redirect: { name: 'SpacesDetailTasksList' },
      meta: {
        title: '题目',
        icon: { type: 'icon', value: 'mdi-cube-outline' },
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
          components: {
            default: () => import('@/views/spaces/detail/PublishTask.vue'),
            header: () => import('@/components/common/PageHeader.vue'),
          },
          meta: {
            titleKey: 'tasks.publish.title',
            backTo: 'SpacesDetailTasksList',
          },
        },
        {
          path: ':taskId',
          name: 'SpacesDetailTasksDetail',
          components: {
            default: () => import('@/views/tasks/Detail.vue'),
            header: () => import('@/components/common/PageHeader.vue'),
          },
          meta: {
            title: '题目',
            backTo: 'SpacesDetailTasksList',
          },
          children: [
            {
              path: '',
              name: 'TasksDetail',
              component: () => import('@/views/tasks/detail/Overview.vue'),
              meta: {
                title: '题目概览',
                disableBreadcrumbLink: true,
              },
            },
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
    // 一门课自己的几屏。题目板是不是课看 `Space.isCourse`（服务端按默认分组声明
    // 的壳算的），能不能看见哪几格看 `lib/courseNav.ts`。这六条路由**一次加齐**：
    // 教学单元 / 作业与验收 / 学生与分组 / 小测 由后面的任务填组件，它们不再动这个
    // 文件，也不再动侧栏。
    //
    // 注意 `SpacesCourseHome` 一条路由两种人看：老师看到课程总览，学生看到我的
    // 课程。分叉在页面里按 `space.admins` 判，不按地址分叉 —— 同一个人今天教书、
    // 明天可能只是学员，地址不该因为「你是谁」而变。
    {
      path: 'course',
      name: 'SpacesCourseHome',
      component: () => import('@/views/spaces/course/CourseHome.vue'),
    },
    {
      path: 'course/units',
      name: 'SpacesCourseUnits',
      component: () => import('@/views/spaces/course/Units.vue'),
    },
    {
      path: 'course/assignments',
      name: 'SpacesCourseAssignments',
      // 老师：作业与验收；学生：本周任务。分叉同 SpacesCourseHome。
      component: () => import('@/views/spaces/course/CourseWork.vue'),
    },
    {
      path: 'course/people',
      name: 'SpacesCoursePeople',
      component: () => import('@/views/spaces/course/People.vue'),
    },
    {
      path: 'course/quiz',
      name: 'SpacesCourseQuiz',
      component: () => import('@/views/spaces/course/Quiz.vue'),
    },
    {
      path: 'course/team',
      name: 'SpacesCourseTeam',
      component: () => import('@/views/spaces/course/Team.vue'),
    },
    // 课程模板的配置（模块开关 + 教学参数）。放在「设置」那一块下 —— 它是老师配
    // 这门课的地方，不是一个课程页；侧栏那一条也只对课程里出现。
    {
      path: 'course/settings',
      name: 'SpacesCourseSettings',
      component: () => import('@/views/spaces/course/CourseSettings.vue'),
    },
    {
      path: 'tasks/audit',
      name: 'SpacesDetailAuditTasks',
      component: () => import('@/views/spaces/detail/AuditTask.vue'),
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
        icon: { type: 'icon', value: 'mdi-chart-line' },
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
          // 学习读的是学生项目里的对话，上面五格读的是赛题与报名表 —— 两套数据，
          // 所以筛选那一栏里它只认时间，学生与知识点是这一格自己的。
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
      component: () => import('@/views/spaces/detail/ManageInviteCodes.vue'),
    },
    {
      path: 'discussions',
      name: 'SpacesDetailDiscussions',
      component: () => import('@/views/spaces/detail/Discussions.vue'),
      meta: {
        titleKey: 'spaces.discussions.title',
      },
    },
    {
      path: 'discussions/create',
      name: 'SpacesDetailCreateDiscussion',
      component: () => import('@/views/spaces/detail/CreateDiscussion.vue'),
      meta: {
        titleKey: 'spaces.discussions.createDiscussion',
        backTo: 'SpacesDetailDiscussions',
      },
    },
    {
      path: 'discussions/:discussionId',
      name: 'SpacesDetailDiscussionItem',
      component: () => import('@/views/spaces/detail/DiscussionItem.vue'),
      meta: {
        titleKey: 'spaces.discussions.detailTitle',
        backTo: 'SpacesDetailDiscussions',
      },
    },
  ],
} as RouteRecordRaw
