import type { Ref } from 'vue'

import { nextTick, onBeforeUnmount, watch } from 'vue'

/**
 * 浮层关掉时把键盘焦点还给打开它的那一处。
 *
 * 打开的那一刻记下当时拿着焦点的元素（`document.activeElement`），关掉时如果它还
 * 在文档里（`isConnected`）就把它重新聚焦。参照 commands/palette/CommandPalette.vue
 * 里手写的那一套，抽成一个组合式函数。
 *
 * 适用于「打开/关闭由自己说了算」的浮层，比如自己画的对话框、没有 activator 的
 * v-bottom-sheet。Vuetify 的 v-dialog / v-overlay 只在**有 activator** 时才在关掉
 * （Esc）时替你把焦点还回去，没有 activator 的不管；v-menu 也只在 Tab 移出或进子
 * 菜单时才还。这些地方交给它自己，别用这个组合式函数去抢。
 *
 * @param open 浮层是否开着。挂载即开着（关掉就把整个组件卸载）的浮层传 `ref(true)`。
 * @param fallback 打开它的那一处已经不在文档里时的兜底：一个返回可聚焦容器的函数。
 *   省略就不还（焦点掉到 body 上）。只在「打开它的东西随这次操作一起没了」时才用
 *   得到，比如弹窗是从某个列表行里的按钮开的、而那一行在弹窗关掉前已经被删掉。
 */
export function useFocusReturn(open: Ref<boolean>, fallback?: () => HTMLElement | null): void {
  let returnFocus: HTMLElement | null = null

  function restore(): void {
    const el = returnFocus
    returnFocus = null
    // 等这一轮渲染跑完再还：Vuetify 的对话框在 isActive 变假的那一瞬间才摘掉自己的
    // 焦点陷阱（retainFocus），早一步还回去会被它一把抢回对话框里。
    nextTick(() => {
      if (el?.isConnected) {
        el.focus?.()
        return
      }
      // 打开它的那一处没了：退到调用方给的兜底容器，别让焦点掉到 body 上。
      const container = fallback?.()
      if (container?.isConnected) container.focus?.()
    })
  }

  watch(
    open,
    (isOpen) => {
      if (isOpen) {
        returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      } else {
        restore()
      }
    },
    { immediate: true }
  )

  // 关掉等于卸载的浮层（设置外框、AI 输入框）走不到上面的 watch：卸载时补一次。
  onBeforeUnmount(restore)
}
