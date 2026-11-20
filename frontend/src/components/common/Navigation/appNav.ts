// 桌面上「首页 + 项目 + 空间」这一份导航，从外壳交给顶栏和侧栏。
//
// 桌面的框分三层（docs/design-system.md §1.4）：最上面一条顶栏，左起是侧栏开关、「首页」
// 和你所在的去处（项目、空间或管理后台，点开换去处），中间是搜索和问芝士，右边是帮助、头像；下面
// 是侧栏，只放当前去处自己的导航；再往右是页面。顶栏上的东西不随换页、收起侧栏挪位置。
// 格子怎么来（顺序、件数、落点、快捷键）还是 destinations.ts 算，外壳算一次、在这里交
// 出去，顶栏和侧栏只画。
import type { ComputedRef, InjectionKey } from 'vue'
import type { Project } from '@/cx_types'
import type { NavTarget } from '@/lib/navTarget'
import type { DropEdge } from '@/lib/projectOrder'
import type { NavGenericItem, NavItem } from './types'

import { inject, provide } from 'vue'

/** 去处列表里的一个空间。 */
export interface NavSpace {
  id: number
  title: string
  /** 上传过的头像地址；没有就是空串，画按 id 派生的首字母。 */
  avatar: string
  to: NavTarget
}

/** 顶栏最左那颗开关管的侧栏。这一页没有侧栏时外壳给 null。 */
export interface NavSidebar {
  /** 侧栏此刻看得见吗（宽档里没收起，平板上浮层开着）。 */
  shown: boolean
  toggle: () => void
}

export interface AppNav {
  /** destinations.railItems 排好的那一列：首页、分隔线、项目、「＋新建项目」。 */
  items: ComputedRef<NavGenericItem[]>
  /** 项目本身（团队名这类格子上没有的字段从这里取）。 */
  projects: ComputedRef<Project[]>
  /** 我加入的空间，去处列表里排在项目下面。 */
  spaces: ComputedRef<NavSpace[]>
  /** 地址落在哪个空间里；不在空间里是 null。 */
  currentSpace: ComputedRef<NavSpace | null>
  /** 既不是项目也不是空间的去处（管理后台）的名字；不在那里是 null。 */
  currentArea: ComputedRef<string | null>
  /** 把 movedId 放到 targetId 的这一边。顺序归外壳保管，这里只报告动作。 */
  reorder: (movedId: string, targetId: string, edge: DropEdge) => void
  loggedIn: ComputedRef<boolean>
  sidebar: ComputedRef<NavSidebar | null>
}

const KEY: InjectionKey<AppNav> = Symbol('app-nav')

export function provideAppNav(nav: AppNav): void {
  provide(KEY, nav)
}

/** 没有外壳（单测里单独挂一个侧栏）时是 null。 */
export function useAppNav(): AppNav | null {
  return inject(KEY, null)
}

/** 首页那一格（顶栏上的「首页」）。 */
export function homeItemOf(items: NavGenericItem[]): NavItem | null {
  return (items.find((i) => i.type === 'item' && i.icon === 'cheese') as NavItem | undefined) ?? null
}

/** 项目格子，按画出来的顺序。 */
export function projectItemsOf(items: NavGenericItem[]): NavItem[] {
  return items.filter((i): i is NavItem => i.type === 'item' && !!i.projectId)
}

/** 「＋新建项目」那一格（只有动作，没有去处）。 */
export function addItemOf(items: NavGenericItem[]): NavItem | null {
  return (items.find((i) => i.type === 'item' && i.add) as NavItem | undefined) ?? null
}
