import type { RouteLocationNormalizedLoaded } from 'vue-router'

/** 这条路由是不是「项目框」里的一层（`meta.projectFrame` 由框那条记录声明）。 */
export function projectFrameOf(route: RouteLocationNormalizedLoaded): string | null {
  if (!route.matched.some((r) => r.meta?.projectFrame === true)) return null
  const id = route.params.projectId
  return typeof id === 'string' && id ? id : null
}
