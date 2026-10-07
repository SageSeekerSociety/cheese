import type { RouteLocationNormalizedLoaded } from 'vue-router'

import { routeIds } from './addresses'

/**
 * 这条路由是不是「项目框」里的一层（`meta.projectFrame` 由框那条记录声明）。
 *
 * 只读 `matched` 和 `params`，所以窄成这两项：调用方递整份 `route`，或递
 * `useNavigation()` 的位置快照，同一份判断都成立。
 */
export function projectFrameOf(route: Pick<RouteLocationNormalizedLoaded, 'matched' | 'params'>): string | null {
  if (!route.matched.some((r) => r.meta?.projectFrame === true)) return null
  return routeIds(route.params).projectId || null
}
