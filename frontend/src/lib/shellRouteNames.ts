import type { InjectionKey } from 'vue'

import { inject } from 'vue'

/**
 * 题目详情页与数据看板里，那些**只在两棵树之间不同**的路由名。
 *
 * 这两组页面被两棵路由树同时挂载：
 *
 * - 老的空间树 `/spaces/:spaceId/tasks/…`（`router/spaces.ts`），名字是 `TasksDetail`
 *   这一套；
 * - 新的题目板外壳 `/spaces/:spaceId/board/…`（`views/spaces/board/routes.ts`），
 *   名字是 `SpaceBoardTaskOverview` 那一套。
 *
 * 路由名在整个应用里唯一，一棵树一套名字，所以页面里写死任何一个都会把另一棵树的
 * 人送到别处去 —— 在题目板里点「提交记录」，人却掉回老页面，外壳就白套了。
 *
 * 做法：**由挂载它的那一棵树 provide，默认是老树那一套**。老树那边一个字不用改
 * （不 provide 就是默认值），新外壳在包一层的地方 provide 自己那套。页面里从此
 * 不出现路由名字面量。
 */
export interface TaskRouteNames {
  /** 题目详情这条路由本身（不是它下面哪一格）。浏览器标题挂在这个名字上 ——
   *  `usePageTitle` 按路由名取动态标题，换个名字就取不到了。 */
  detail: string
  /** 题目概览。 */
  overview: string
  /** 参与者管理（出题人与管理员才看得见的那一格）。 */
  participants: string
  /** 提交记录。 */
  submissions: string
  /** 交作业。 */
  submit: string
  /** 启星研导（AI 建议）。 */
  aiAdvice: string
  /** 改题。 */
  edit: string
  /** 这块板的门户 —— 面包屑里「回到这块板」那一格。老树是空间首页（题目列表），
   *  新外壳是它的「空间」首页。 */
  spaceHome: string
}

export const TASK_ROUTE_NAMES: InjectionKey<TaskRouteNames> = Symbol('task-route-names')

/** 老树那一套。新外壳不 provide 时就会掉到这里，所以它必须与 `router/spaces.ts`
 *  里现在的名字逐字一致。 */
export const OLD_TASK_ROUTE_NAMES: TaskRouteNames = {
  detail: 'SpacesDetailTasksDetail',
  overview: 'TasksDetail',
  participants: 'TasksParticipants',
  submissions: 'TasksSubmissions',
  submit: 'TasksSubmit',
  aiAdvice: 'TasksAIAdvice',
  edit: 'TasksEdit',
  spaceHome: 'SpacesDetail',
}

export function useTaskRouteNames(): TaskRouteNames {
  return inject(TASK_ROUTE_NAMES, OLD_TASK_ROUTE_NAMES)
}

/**
 * 数据看板那六格。**只有老树那一套了**：新外壳里 `/board/analytics` 换成了这块板
 * 自己的看板（`views/spaces/board/pages/Analytics.vue`），不再把老页面套进去，
 * 于是没有谁 provide 新名字 —— 留下来的这个注入键与那份兜底名单，服务的就是老树
 * 那九页自己（`SpacesDetailAnalytics*`）。等老树退场时，这一整块可以一起走。
 */
export interface AnalyticsRouteNames {
  overview: string
  alerts: string
  publishers: string
  tasks: string
  participants: string
  learning: string
}

export const ANALYTICS_ROUTE_NAMES: InjectionKey<AnalyticsRouteNames> = Symbol('analytics-route-names')

export const OLD_ANALYTICS_ROUTE_NAMES: AnalyticsRouteNames = {
  overview: 'SpacesDetailAnalyticsOverview',
  alerts: 'SpacesDetailAnalyticsAlerts',
  publishers: 'SpacesDetailAnalyticsPublishers',
  tasks: 'SpacesDetailAnalyticsTasks',
  participants: 'SpacesDetailAnalyticsParticipants',
  learning: 'SpacesDetailAnalyticsLearning',
}

export function useAnalyticsRouteNames(): AnalyticsRouteNames {
  return inject(ANALYTICS_ROUTE_NAMES, OLD_ANALYTICS_ROUTE_NAMES)
}

/**
 * 发完题之后落到哪一页。
 *
 * 老树是「我发布的」，新外壳是它自己的「我的」—— 同一个动作在两棵树里各有一个
 * 说得通的目的地，所以也由挂着它的那一棵树说。
 */
export const PUBLISH_DONE_ROUTE: InjectionKey<string> = Symbol('publish-done-route')

export const OLD_PUBLISH_DONE_ROUTE = 'SpacesDetailMyPublishing'

export function usePublishDoneRoute(): string {
  return inject(PUBLISH_DONE_ROUTE, OLD_PUBLISH_DONE_ROUTE)
}
