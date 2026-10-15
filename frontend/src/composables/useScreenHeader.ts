// 手机上这一屏的头部：页面说它叫什么、底下那行小字、或者整行换成它自己的标题行，
// 唯一的那条顶栏（MobileAppBar）照着画。没说的那一屏用路由上的标题。
//
// 头部是数据，不是页面里的一段 DOM：以前页面把自己的标题行 Teleport 进顶栏里那一格，
// 那一格又只在路由声明过的页面上才有，两处对不上就挂不上、卸不掉（手机上从待办进支线
// 再返回卡死，就是这么来的）；标题写进一份全局存储、离开时再擦，组件一复用就留着上一
// 页的。现在谁在屏幕上谁声明，跟着它的组件一起生、一起撤（React Navigation 的
// `setOptions` 是同一个意思）。
//
// 桌面上没有这条顶栏，声明了也没人画；页面自己的页头照旧画在页面里。
import type { Component } from 'vue'

import { computed, onActivated, onDeactivated, onScopeDispose, shallowRef } from 'vue'

export interface ScreenHeader {
  /** 这一屏叫什么；不说就用路由上的标题。 */
  title?: string
  /** 标题下面那一行小字（支线在哪个频道里）。 */
  subtitle?: string
  /** 整行换成这一屏自己的标题行（频道名和成员、项目切换器），连同它要的参数和事件。 */
  content?: { component: Component; props?: Record<string, unknown> }
}

interface Entry {
  read: () => ScreenHeader | null
}

const entries = shallowRef<Entry[]>([])

/** 此刻屏幕上那一屏声明的头部：后声明的在上面，说了 null 的让给下面那一个。 */
export const screenHeader = computed<ScreenHeader | null>(() => {
  for (let i = entries.value.length - 1; i >= 0; i--) {
    const header = entries.value[i].read()
    if (header) return header
  }
  return null
})

/** 这一屏的头部。`read` 随页面状态变；返回 null 就是这会儿不声明（交给路由）。 */
export function useScreenHeader(read: () => ScreenHeader | null): void {
  const entry: Entry = { read }
  const add = () => {
    if (!entries.value.includes(entry)) entries.value = [...entries.value, entry]
  }
  const remove = () => {
    entries.value = entries.value.filter((it) => it !== entry)
  }
  add()
  onActivated(add)
  onDeactivated(remove)
  onScopeDispose(remove)
}
