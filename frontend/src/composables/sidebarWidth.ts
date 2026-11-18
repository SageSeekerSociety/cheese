import type { InjectionKey, Ref } from 'vue'
import type { LayoutPanelWidth } from '@/components/layout-area'

import { readWidth } from '@/components/layout-area'

// 侧栏的宽度：首页、项目、空间、个人设置四处共用一个数。拖过哪一处，到别处也是这个
// 宽度——各记各的话，从项目切到空间侧栏会跳一下。记在这台浏览器上，下次打开还是那样。
//
// 宽档里侧栏是外壳布局区（layouts/AppShell.vue）钉住的一格，拖的是那一格的分隔线，记
// 宽度的是布局层（`LayoutPanel.width`）；窄档（960–1180）里它是浮层，按同一个数打开。
// 首屏骨架（index.html）读同一个键。

export const SIDEBAR_WIDTH: LayoutPanelWidth = {
  initial: 280,
  min: 190,
  max: 480,
  storageKey: 'cheesex.sidebarWidth',
}

/** 宽档里侧栏那一格此刻的宽度（外壳量出来、交给侧栏的抽屉）；0 是还没量出来。 */
export const SIDEBAR_CELL_WIDTH: InjectionKey<Readonly<Ref<number>>> = Symbol('sidebarCellWidth')

/** 浮层侧栏打开时的宽度：宽档里最后拖成的那个。 */
export function sidebarWidth(): number {
  return readWidth(SIDEBAR_WIDTH)
}
