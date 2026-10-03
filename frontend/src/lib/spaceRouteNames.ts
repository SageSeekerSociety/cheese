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

/** 空间设置里的「资料库」那一页。参考资料选择器里「上传到资料库」照它跳 ——
 *  清单里没找着要的那份课件时，出口就在手边。
 *
 *  写的是路径不是路由名：这一条只是给不认得这块板的组件指路，而按名寻址要调用方
 *  也装着一个认识那个名字的路由器，不值的。路径与 `router/spaces.ts` 的那一条一致。 */
export const spaceLibraryPath = (spaceId: number | string) => `/spaces/${spaceId}/manage/settings/materials`
