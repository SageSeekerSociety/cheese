import type { InjectionKey, Ref } from 'vue'
import type { LayoutPanelWidth } from '@/components/layout-area'

import { rememberedWidth, rememberWidth } from '@/components/layout-area'

// 侧栏的宽度：首页、项目、空间、个人设置四处共用一个数。拖过哪一处，到别处也是这个
// 宽度——各记各的话，从项目切到空间侧栏会跳一下。记在这台浏览器上，下次打开还是那样。
//
// 宽档里侧栏是外壳布局区（layouts/AppShell.vue）钉住的一格，拖的是那一格的分隔线；窄档
// （960–1180）里它是浮层，拖它的右缘。两处读写的是布局层里同一个数（`LayoutPanel.width`，
// 上下限也只在那里算），所以在一处拖过，另一处当场就是这个宽度。首屏骨架（index.html）
// 读同一个键。

export const SIDEBAR_WIDTH: LayoutPanelWidth = {
  initial: 280,
  min: 190,
  max: 480,
  storageKey: 'cheesex.sidebarWidth',
}

/** 宽档里侧栏收成一条没有：所有页面同一个状态，记在这台设备上（`LayoutPanel.foldKey`）。 */
export const SIDEBAR_FOLD_KEY = 'cheesex.sidebarFolded'

/** 宽档里侧栏那一格此刻的宽度（外壳量出来、交给侧栏的抽屉）；0 是还没量出来。 */
export const SIDEBAR_CELL_WIDTH: InjectionKey<Readonly<Ref<number>>> = Symbol('sidebarCellWidth')

/** 侧栏现在的宽度：拖到哪里就是哪里，宽档和窄档同一个数。 */
export function sidebarWidth(): Readonly<Ref<number>> {
  return rememberedWidth(SIDEBAR_WIDTH)
}

/** 窄档浮层拖右缘时改宽度。`persist` 关掉时只改这一次的数，松手再记一次。 */
export function setSidebarWidth(width: number, persist = true): number {
  return rememberWidth(SIDEBAR_WIDTH, width, persist)
}
