// What each row does the moment it appears: new arrivals fade in, a message you
// just sent rises out of the composer, and a jump target flashes. A page pulled
// back in does nothing: it lands above the viewport while the reader may still
// be scrolling into it, and rows that start transparent show as a blank pane. The sets are read by the template (class bindings) and
// cleared by the row's own `animationend` — see ChatTimeline.
//
// Lifted verbatim out of ChatPanel. The two sets the panel itself fills
// (`unseen`, `editing`) are passed in so both sides hold the same one.
import type { Ref } from 'vue'

import { nextTick, reactive, ref, watch } from 'vue'

export interface TimelineMotionDeps {
  atBottom: Ref<boolean>
  hasNewer: Ref<boolean>
  /** Blocks that arrived while the reader was scrolled up (the pill counts them). */
  unseen: Ref<string[]>
  /** Outbox rows leaving because their text went back to the composer. */
  editing: Set<string>
  scrollRef: Ref<HTMLElement | null>
  backToNewest: () => void
}

export function useTimelineMotion(deps: TimelineMotionDeps) {
  const { atBottom, hasNewer, unseen, editing, scrollRef, backToNewest } = deps

  // 此刻才进来的那几条消息（不是打开房间时读出来的历史）。它们进来时淡入一下：新
  // 消息落在底部，这一下说的是「刚来的是这条」；读历史时演，一屏同时浮上来几十条，
  // 什么也说明不了。历史快照合并完之前（`historyChanges` 还在）进来的也不算——那是
  // 打开房间时的补齐。自己发的不算：发件箱那一行早已在屏幕上，换成落库的那一条时
  // 再淡入一次就是一闪。
  const arrived = reactive(new Set<string>())

  // 自己刚发的那几条（发件箱里的 client id）：从输入框的方向升上来。打开房间时从草稿
  // 里恢复出来的发件箱不算——那几条一直在，不是此刻发的。
  const sentNow = reactive(new Set<string>())
  // 发件箱那一行换成落库的那一条时，淡的那一档慢慢恢复，而不是一下跳亮。
  const delivered = reactive(new Set<string>())

  watch(atBottom, (bottom) => {
    if (bottom && !hasNewer.value) unseen.value = []
  })
  function jumpToUnseen() {
    const first = unseen.value[0]
    if (hasNewer.value) backToNewest()
    else scrollRef.value?.scrollTo({ top: scrollRef.value.scrollHeight, behavior: 'smooth' })
    unseen.value = []
    if (first) flash(first)
  }

  // 淡入演完就把这一条从 `arrived` 里拿掉：class 一直挂着的话，它的 animation 会压住
  // 之后要演的那一下（跳过来的闪一下、同类事件又来一次的亮一下）。Vue 给 scoped 的
  // keyframes 名字加了后缀，所以比前缀。
  function settleArrival(e: AnimationEvent, id: string) {
    if (e.animationName.startsWith('tl-arrive')) arrived.delete(id)
    if (e.animationName.startsWith('tl-delivered')) delivered.delete(id)
  }
  function settleSent(e: AnimationEvent, clientId: string) {
    if (e.animationName.startsWith('tl-sent')) sentNow.delete(clientId)
  }
  function outboxLeave(el: Element, done: () => void) {
    const clientId = (el as HTMLElement).dataset.cid
    if (clientId && editing.delete(clientId)) collapseLeave(el, done)
    else done()
  }

  // 一行离开时先收拢自己的高度再走，下面的东西平滑地补上来，而不是等它淡完一下子
  // 跳过去（设计系统 §9.2）。
  function collapseLeave(el: Element, done: () => void) {
    const row = el as HTMLElement
    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      done()
    }
    row.style.height = `${row.offsetHeight}px`
    row.style.overflow = 'hidden'
    requestAnimationFrame(() => {
      row.style.height = '0'
      row.style.marginTop = '0'
      row.style.paddingTop = '0'
      row.style.paddingBottom = '0'
      row.style.opacity = '0'
    })
    row.addEventListener('transitionend', (ev) => ev.target === row && finish())
    setTimeout(finish, 400) // transitionend 不来（减弱动效、元素被提前拿走）也得收尾
  }

  // 跳到某一条之后，让它闪一下：滚动停下来的那一刻，眼睛要知道落在哪一行。
  const flashId = ref<string | null>(null)
  let flashTimer: ReturnType<typeof setTimeout> | undefined
  function flash(id: string) {
    clearTimeout(flashTimer)
    flashId.value = null
    void nextTick(() => {
      flashId.value = id
      flashTimer = setTimeout(() => (flashId.value = null), 1600)
    })
  }

  return {
    arrived,
    sentNow,
    delivered,
    flashId,
    flash,
    settleArrival,
    settleSent,
    outboxLeave,
    jumpToUnseen,
  }
}
