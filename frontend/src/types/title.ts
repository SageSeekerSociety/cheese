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
  /** `backTo` 只在手机上算：这一页在桌面上是 rail 或头像菜单直接到的地方，不是谁的
   *  下一层，桌面顶栏不画 ←。 */
  backOnPhoneOnly?: boolean
  /** 「项目这个框」那条记录自己举的手——见 lib/projectEntry 的 projectFrameOf。 */
  projectFrame?: boolean
  /**
   * 这一页能从命令面板直接去。页面自己在路由上声明，面板不另记一份页面清单。带
   * `:projectId` 的路由只在项目里出现，参数取当前项目。
   */
  palette?: {
    /** i18n key；项目的壳换词（「项目」叫「工作」）时跟着换。 */
    label: string
    icon: string
    /** 除 projectId 以外必填的路由参数，例如项目文档默认打开章程。 */
    params?: Record<string, string>
  }
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
