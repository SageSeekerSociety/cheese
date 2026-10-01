/**
 * 题目详情与数据看板里互相跳转用的路由名，写在一处，页面里只认这里的名字。
 * 必须与 `router/spaces.ts` 逐字一致。
 */
export interface TaskRouteNames {
  /** 题目详情的默认页，也就是「说明」页签。 */
  detail: string
  /** 回到题目详情（「返回概览」）。 */
  overview: string
  /** 「领取者」页签（出题人与管理员才看得见）。 */
  participants: string
  /** 「我的提交」页签。 */
  submissions: string
  /** 交作业。 */
  submit: string
  /** 启星研导（AI 建议）。 */
  aiAdvice: string
  /** 改题。 */
  edit: string
  /** 「数据」页签。 */
  insights: string
  /** 这个空间的入口 —— 面包屑里「回到空间」那一格。 */
  spaceHome: string
}

export const TASK_ROUTE_NAMES: TaskRouteNames = {
  detail: 'TasksDetail',
  overview: 'TasksDetail',
  participants: 'TasksParticipants',
  submissions: 'TasksSubmissions',
  submit: 'TasksSubmit',
  aiAdvice: 'TasksAIAdvice',
  edit: 'TasksEdit',
  insights: 'TasksInsights',
  spaceHome: 'SpacesDetail',
}

/** 数据看板那六格。 */
export interface AnalyticsRouteNames {
  overview: string
  alerts: string
  publishers: string
  tasks: string
  participants: string
  learning: string
}

export const ANALYTICS_ROUTE_NAMES: AnalyticsRouteNames = {
  overview: 'SpacesDetailAnalyticsOverview',
  alerts: 'SpacesDetailAnalyticsAlerts',
  publishers: 'SpacesDetailAnalyticsPublishers',
  tasks: 'SpacesDetailAnalyticsTasks',
  participants: 'SpacesDetailAnalyticsParticipants',
  learning: 'SpacesDetailAnalyticsLearning',
}

/** 发完题之后落到哪一页：题目列表的「我发布的」，还没过审的题也在里面。 */
export const publishDoneRoute = (spaceId: number | string) => ({
  name: 'SpacesDetailTasksList',
  params: { spaceId },
  query: { filter: 'publishing' },
})
