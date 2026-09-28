import type { MenuAction } from './menuAction'

import { computed, shallowRef } from 'vue'

/** 页面交给手机顶栏的一项操作（由 PageAction 登记）。 */
export interface TopBarAction extends MenuAction {
  /** 这一页的主操作：顶栏右边一颗图标按钮。其余的收进 ⋯。 */
  primary?: boolean
}

// 手机顶栏右边那一簇：当前页的操作。整个应用只有一条顶栏，所以这份清单是模块级
// 的，不进 pinia（页面组件的单测不必为此装一个 store）。
//
// 每个 PageAction 各登记各的一条（id 各不相同），各自撤掉——换页时新页的登记和旧
// 页的撤销都在同一轮渲染之后跑，谁先谁后不一定，「全部清空」会把新页刚登记的也一
// 起清掉。按 id 排序：id 是组件建出来的顺序，也就是它们在模板里的顺序。
const entries = shallowRef<{ id: number; action: () => TopBarAction }[]>([])

export const topBarActions = computed(() => entries.value.map((entry) => entry.action()))

export function registerTopBarAction(id: number, action: () => TopBarAction) {
  const rest = entries.value.filter((entry) => entry.id !== id)
  entries.value = [...rest, { id, action }].sort((a, b) => a.id - b.id)
}

export function unregisterTopBarAction(id: number) {
  if (entries.value.some((entry) => entry.id === id)) entries.value = entries.value.filter((entry) => entry.id !== id)
}
