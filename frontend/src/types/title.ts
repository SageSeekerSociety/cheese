import type { RouteLocationNormalized, RouteRecordNormalized } from 'vue-router'

export interface RouteIcon {
  type: 'icon' | 'image'
  value: string
}

export interface RouteMetaTitle {
  title?: string
  titleKey?: string
  icon?: RouteIcon
  isDynamic?: boolean
  getDynamicTitle?: (route: RouteLocationNormalized) => string
  /** 一条没有名字的记录（比如项目这个框）用哪个键去取动态标题。有名字的记录用
   *  自己的名字，不需要它。 */
  dynamicTitleKey?: string
  disableBreadcrumbLink?: boolean
  isFullPage?: boolean
  backTo?: string
  /** 「项目这个框」那条记录自己举的手——见 lib/projectEntry 的 projectFrameOf。 */
  projectFrame?: boolean
}

export interface RouteHierarchyItem {
  path: string
  originalPath: string
  name: string | symbol | null | undefined
  title: string
  meta: RouteMetaTitle
  isDynamic: boolean
  route: RouteRecordNormalized
  params: Record<string, string>
}

export interface BreadcrumbItem {
  title: string
  icon?: RouteIcon
  path: string
  name: string | symbol | null | undefined
  isLast: boolean
  isClickable: boolean
}

declare module 'vue-router' {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface RouteMeta extends RouteMetaTitle {}
}
