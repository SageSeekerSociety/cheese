import { onScopeDispose, shallowRef, watchEffect } from 'vue'

/** 页面接管顶栏那颗 ← 时交上来的东西。 */
export interface TopBarBack {
  /** 读屏和长按看到的说法，例如「返回对话」。 */
  label: string
  onBack: () => void
}

// 顶栏的 ← 平常按路由声明的上一层走（`meta.backTo`）。页面里还有一层比路由更近的
// 「上一步」时（话题里从别的页签回到对话），由页面接管这一下。整个应用只有一条顶
// 栏，所以和 topBarActions 一样是模块级的一格。
export const topBarBack = shallowRef<TopBarBack | null>(null)

/**
 * 在当前组件的作用域里接管顶栏的 ←。`get` 返回 null 时交还给路由。组件卸载时
 * 自动交还——只交还自己登记的那一份，换页时新页可能已经登记了自己的。
 */
export function useTopBarBack(get: () => TopBarBack | null) {
  let mine: TopBarBack | null = null
  watchEffect(() => {
    const next = get()
    if (topBarBack.value === mine) topBarBack.value = next
    else if (next) topBarBack.value = next
    mine = next
  })
  onScopeDispose(() => {
    if (mine && topBarBack.value === mine) topBarBack.value = null
    mine = null
  })
}
