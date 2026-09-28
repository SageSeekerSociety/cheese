import type { TaskRouteNames } from '@/lib/shellRouteNames'

/**
 * 新外壳里的路由名。**名字写在这里，路径写在 `routes.ts`** —— 页面里跳转只认名字
 * （`shellRouteNames.ts` 里那一套 provide 的就是它），换路径时不用翻页面。
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

// 这里原来还有一组 `BOARD_ANALYTICS_ROUTE_NAMES`（新外壳里那六格老分析页的路由名）。
// 第七批之后整板看板换成了这块板自己的页面，那六条子路由连同这一组名字一起没了 ——
// `shellRouteNames.ts` 的 `AnalyticsRouteNames` 仍在，老树那九页照用它（不 provide
// 就是老树那一套）。
