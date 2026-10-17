// 走之前那一句确认（参考物写的是 `Discard your annotations?`）。
//
// 从 DesignImage.vue 里搬出来的：弹框画在那一层（它的 DOM 还在组件模板里），但「谁来问、
// 问了几次、答了以后怎么办」这一套是独立的。导航守卫（路由、关页签、切文件）要的是
// 一个同步登记的 `confirmDiscard`，所以登记和写笔画落在同一拍（`flush: 'sync'`）。
import type { Ref } from 'vue'

import { nextTick, onBeforeUnmount, ref, watchEffect } from 'vue'

import { clearAnnotationGuard, setAnnotationGuard } from './annotationDiscard'

export interface DesignDiscardGuardOptions {
  /** 这一格正显示着没有；收起来的那几页也在跑，但它们没在看图，不该拦住别人。 */
  active: () => boolean
  /** 此刻真会丢东西（画了、且还没发）。 */
  wouldLoseStrokes: () => boolean
  /** 确认丢掉时清空笔画。 */
  dropAnnotations: () => void
  /** 弹框冒出来之前焦点在哪，从它的 ownerDocument 读。 */
  pane: Ref<HTMLElement | null>
}

export function useDesignDiscardGuard(options: DesignDiscardGuardOptions) {
  const { pane } = options
  const discardPrompt = ref(false)
  let resolveDiscard: ((go: boolean) => void) | null = null
  /**
   * 还开着的那一次确认。
   *
   * 弹框开着的时候可能又来一次导航（双击页签、先点页签再点关闭、快捷键与点击几乎同时
   * 触发两次 `setTab`/`closeFile`）。没有它的话 `confirmDiscard` 会把上一次的 resolver
   * 顶掉，第一次那一下永远等不到答复，它该做的切换/关闭就静默丢了。存着这一份，第二次
   * 直接拿同一个 Promise：一次回答答住两次。
   */
  let discardPending: Promise<boolean> | null = null
  /** 「留下」那颗按钮：弹出来时先把焦点给它。 */
  const discardKeep = ref<HTMLButtonElement | null>(null)
  /** 弹框那张卡：焦点陷阱围着它转，别把字落到后面没被盖住的界面上。 */
  const discardCard = ref<HTMLElement | null>(null)
  /** 弹框冒出来之前焦点在哪：关了以后还回去，免得键盘用的人被打回页首。 */
  const discardOrigin = ref<HTMLElement | null>(null)

  /**
   * 确认了就丢笔画，同时立刻把登记处撤掉——`WorkPanel` 那边「关文件」是「先确认、
   * 再换页签」，换页签时还会再问一次；这里同步撤掉，第二次才不会又弹一遍。
   */
  function confirmDiscard(): Promise<boolean> {
    // 已经开着就复用那一次：多来几次导航，同一个答复答住，不各自挂一个悬着的 Promise。
    if (discardPending) return discardPending
    discardPending = new Promise<boolean>((resolve) => {
      resolveDiscard = resolve
    })
    discardOrigin.value = (pane.value?.ownerDocument.activeElement as HTMLElement | null) ?? null
    discardPrompt.value = true
    void nextTick(() => discardKeep.value?.focus({ preventScroll: true }))
    return discardPending
  }
  const discardGuard = { confirmDiscard }
  function answerDiscard(go: boolean) {
    discardPrompt.value = false
    const resolve = resolveDiscard
    resolveDiscard = null
    discardPending = null
    const origin = discardOrigin.value
    discardOrigin.value = null
    // 关掉之后把焦点还回原来那个地方（还在的话）。
    if (origin?.isConnected) origin.focus({ preventScroll: true })
    if (go) {
      options.dropAnnotations()
      clearAnnotationGuard(discardGuard)
    }
    resolve?.(go)
  }
  /** 弹框里只有两颗按钮：Tab 到头绕回去，别让焦点漏到后面那份界面上。 */
  function trapDiscardFocus(event: KeyboardEvent) {
    const card = discardCard.value
    if (!card) return
    const focusables = Array.from(card.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
    if (!focusables.length) return
    const at = focusables.indexOf(event.target as HTMLButtonElement)
    const next = event.shiftKey ? (at <= 0 ? focusables.length - 1 : at - 1) : at === focusables.length - 1 ? 0 : at + 1
    event.preventDefault()
    focusables[next]?.focus()
  }

  // 只有「此刻在看着的、且有没发出去的笔画」的那一块才登记：收起来的页签（v-show 留着
  // 的）也在跑，但它们没在看，不该拦住别人。
  // flush: 'sync' —— 这道登记是被同步读的（路由守卫、关页签、切文件都在同一拍里读它），
  // 落在一拍之后的刷新队列里就等于「刚画完那一瞬间还没登记」。同步跑，登记和写笔画是同一拍。
  watchEffect(
    () => {
      if (options.active() && options.wouldLoseStrokes()) setAnnotationGuard(discardGuard)
      else clearAnnotationGuard(discardGuard)
    },
    { flush: 'sync' }
  )
  onBeforeUnmount(() => {
    clearAnnotationGuard(discardGuard)
  })

  return { discardPrompt, discardKeep, discardCard, discardOrigin, confirmDiscard, answerDiscard, trapDiscardFocus }
}
