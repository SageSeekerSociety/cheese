import type { AnalyticsRouteNames, TaskRouteNames } from '@/lib/shellRouteNames'

/**
 * 新外壳里那两串路由名。**名字写在这里，路径写在 `routes.ts`** —— 页面里跳转只
 * 认名字（`shellRouteNames.ts` 里那一套 provide 的就是它），换路径时不用翻页面。
 */
export const BOARD_TASK_ROUTE_NAMES: TaskRouteNames = {
  detail: 'SpaceBoardTaskDetail',
  overview: 'SpaceBoardTaskOverview',
  participants: 'SpaceBoardTaskParticipants',
  submissions: 'SpaceBoardTaskSubmissions',
  submit: 'SpaceBoardTaskSubmit',
  aiAdvice: 'SpaceBoardTaskAIAdvice',
  // 改题页这一批**没搬**：它还是老树里那一页，点「编辑」会离开外壳。收它进来是
  // 下一批的事，在那之前宁可让人跳出去，也不要给一个假的「还在外壳里」。
  edit: 'TasksEdit',
  spaceHome: 'SpaceBoardHome',
}

/** 发完题落到哪一页。老树是「我发布的」，新外壳是它自己的「我的」—— 那一页里
 *  「我发布的」是其中一块，所以在这一点上它比老页面更全。 */
export const BOARD_PUBLISH_DONE_ROUTE = 'SpaceBoardMine'

export const BOARD_ANALYTICS_ROUTE_NAMES: AnalyticsRouteNames = {
  overview: 'SpaceBoardAnalyticsOverview',
  alerts: 'SpaceBoardAnalyticsAlerts',
  publishers: 'SpaceBoardAnalyticsPublishers',
  tasks: 'SpaceBoardAnalyticsTasks',
  participants: 'SpaceBoardAnalyticsParticipants',
  learning: 'SpaceBoardAnalyticsLearning',
}
