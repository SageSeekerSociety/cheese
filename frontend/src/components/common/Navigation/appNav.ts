// 桌面上「首页 + 项目」这一份导航，从外壳交给侧栏和顶栏。
//
// 以前它是左边那一列 64px 的方块栏（LeftAppRail）：首页一格、一个项目一格、「＋」一格，
// 底下问芝士和头像。2026-11-16 那一列拿掉了（蔡松洋定的方向一）：项目切换器住进侧栏头，
// 写你在哪儿；首页是它下面的一行，带等你处理的总件数（首页那一格的侧栏不画这一行）；问芝士和头像住进侧栏底。没有侧栏
// 的页面、平板上侧栏收起时，首页、问芝士和头像临时回到顶栏。格子怎么来（顺序、件数、落点、快捷键）还是 destinations.ts 算，
// 外壳算一次、在这里交出去，侧栏和顶栏只画。
import type { Component, ComputedRef, InjectionKey } from 'vue'
import type { Project } from '@/cx_types'
import type { DropEdge } from '@/lib/projectOrder'
import type { NavGenericItem, NavItem } from './types'

import { inject, provide } from 'vue'

export interface AppNav {
  /** destinations.railItems 排好的那一列：首页、分隔线、项目、「＋新建项目」。 */
  items: ComputedRef<NavGenericItem[]>
  /** 项目本身（团队名这类格子上没有的字段从这里取）。 */
  projects: ComputedRef<Project[]>
  /** 把 movedId 放到 targetId 的这一边。顺序归外壳保管，这里只报告动作。 */
  reorder: (movedId: string, targetId: string, edge: DropEdge) => void
  loggedIn: ComputedRef<boolean>
  /** 侧栏头和侧栏底那两行的组件（SidebarFrameTop、SidebarFrameFoot）。由外壳交进来而不是
   *  让 SecondaryNavigation 自己 import：那两样要读账号和芝士面板，侧栏一 import 它们，
   *  每一处用侧栏的画面就不再能只靠 props 画出来（scene-ratchet）。 */
  frame: { top: Component; foot: Component }
  /** 收起侧栏（侧栏头上那颗按钮）。只有侧栏是布局区的一格时（宽档）才有。 */
  foldSidebar: ComputedRef<(() => void) | null>
  /** 展开收起的侧栏（页头最左那颗按钮）。只有宽档里侧栏收着时才有。 */
  unfoldSidebar: ComputedRef<(() => void) | null>
}

const KEY: InjectionKey<AppNav> = Symbol('app-nav')

export function provideAppNav(nav: AppNav): void {
  provide(KEY, nav)
}

/** 没有外壳（单测里单独挂一个侧栏）时是 null：侧栏照常画，只是头和底那几样不画。 */
export function useAppNav(): AppNav | null {
  return inject(KEY, null)
}

/** 首页那一格（侧栏上「首页」那一行）。 */
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
