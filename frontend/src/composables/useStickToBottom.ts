import type { Ref } from 'vue'

import { nextTick, onScopeDispose, ref, watch } from 'vue'

import { shouldKeepPinning } from '../lib/siteLog'

/**
 * 读者视口顶上那一行，以及它离容器顶边多远。内容换过之后照它把位置放回去：
 * 浏览器在换内容时可能已经把 scrollTop 夹到了别处（离屏的行退回估计高度、内容变矮，
 * scrollTop 跟着被夹），只有那一行还在，才知道他原来读的是哪儿。
 */
export interface ScrollAnchor {
  /** 那一行的 id；视口里一行都没有时是 null。 */
  id: string | null
  /** 那一行的顶边离容器顶边多少像素（可以是负的：读在半行上）。 */
  offset: number
  /** 记下它时的 scrollTop，那一行已经不在这一窗里时退回到这里。 */
  top: number
}

export interface StickToBottom {
  /** 读者是不是停在末尾。只由他自己的滚动改，不在某一次更新那一刻量。 */
  following: Ref<boolean>
  /** 钉到末尾（打开这一格、换视角、读者本来就停在末尾时）。 */
  scrollToTail(): void
  /** 记下此刻视口顶上那一行。 */
  captureAnchor(): ScrollAnchor
  /** 内容换过之后，把那一行放回原来的位置。 */
  restoreAnchor(anchor: ScrollAnchor): void
}

// A log that was showing its newest line keeps showing it when its box changes
// size. Zooming (Cmd +/-) or resizing the window changes the pane's height and
// re-wraps every line, while `scrollTop` stays the same number — so the reader
// who was at the bottom is left somewhere in the middle.
//
// Whether the reader was at the bottom is recorded from their own scrolling, not
// measured when an update arrives. Measured then it is already too late: a
// re-render puts the off-screen rows back to their content-visibility estimate,
// the content above the reader gets shorter, the browser clamps `scrollTop` down
// with it, and that clamp reads as "the reader is at the bottom" — so the next
// refresh (every 3s here) scrolls them all the way down. See the scroll handler
// below: a movement that came with a height change is the layout's, unless the
// reader's own wheel/touch/pointer input came with it.
export function useStickToBottom(scrollRef: Ref<HTMLElement | null>, threshold: number): StickToBottom {
  const following = ref(true)
  let contentHeight = 0
  let viewport = 0
  // 读者自己的输入（滚轮、触屏、按住滚动条）到过一次、还没被下一次 scroll 认领。
  let readerScrolled = false

  const noteReader = (): void => {
    readerScrolled = true
  }

  function measure(el: HTMLElement): void {
    contentHeight = el.scrollHeight
    viewport = el.clientHeight
  }

  // 布局自己挪的那一下不算读者挪的：内容或视口的高度在这一拍里变过，而读者的输入
  // 没有到过，就只更新量到的尺寸，不改跟随状态。内容变矮把他夹上去，不等于他回到
  // 了末尾；内容变高把 scrollTop 带下去，也不等于他离开了末尾。
  const onScroll = (): void => {
    const el = scrollRef.value
    if (!el) return
    const relaid = el.scrollHeight !== contentHeight || el.clientHeight !== viewport
    if (relaid && !readerScrolled) {
      measure(el)
      return
    }
    following.value = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
    readerScrolled = false
    measure(el)
  }

  const observer = new ResizeObserver(() => {
    const el = scrollRef.value
    if (!el) return
    if (following.value && el.clientHeight) el.scrollTop = el.scrollHeight
    measure(el)
  })

  function rowsIn(el: HTMLElement): HTMLElement[] {
    return Array.from(el.querySelectorAll<HTMLElement>('[data-record-id]'))
  }

  function captureAnchor(): ScrollAnchor {
    const el = scrollRef.value
    if (!el) return { id: null, offset: 0, top: 0 }
    const boxTop = el.getBoundingClientRect().top
    for (const row of rowsIn(el)) {
      const rect = row.getBoundingClientRect()
      // 整个在视口上方的那几行跳过；第一行盖住了视口顶边的，就是读者读着的那一行。
      if (rect.bottom <= boxTop) continue
      const id = row.dataset.recordId ?? null
      if (id) return { id, offset: rect.top - boxTop, top: el.scrollTop }
    }
    return { id: null, offset: 0, top: el.scrollTop }
  }

  function restoreAnchor(anchor: ScrollAnchor): void {
    const el = scrollRef.value
    if (!el) return
    const row = anchor.id ? rowsIn(el).find((r) => r.dataset.recordId === anchor.id) : undefined
    if (row) {
      const delta = row.getBoundingClientRect().top - el.getBoundingClientRect().top - anchor.offset
      if (delta) el.scrollTop += delta
    } else {
      el.scrollTop = anchor.top
    }
    measure(el)
  }

  // A single `scrollTop = scrollHeight` at nextTick does NOT work here, which is
  // how this shipped broken the first time: the panel renders its spinner first,
  // so the container is one viewport tall with nothing to scroll, the assignment
  // clamps to 0, and the timeline lays out underneath — leaving the reader on the
  // oldest entry, the exact bug this exists to fix. Measured on the deployed page:
  // scrollHeight 500 at +40ms, 2066 at +120ms, scrollTop 0 throughout.
  // So keep re-pinning while the height is still moving (see shouldKeepPinning),
  // and stop the moment the reader scrolls away (the next frame checks `following`).
  function scrollToTail(): void {
    // 叫它的是「打开／换视角」这一类要求：即使读者刚才还翻在上面，这一下也跟回去。
    following.value = true
    let lastHeight = -1
    let frames = 0
    const pin = (): void => {
      const el = scrollRef.value
      if (!el || !following.value) return
      el.scrollTop = el.scrollHeight
      measure(el)
      if (shouldKeepPinning(el.scrollHeight, lastHeight, frames)) {
        lastHeight = el.scrollHeight
        frames += 1
        requestAnimationFrame(pin)
      }
    }
    void nextTick(() => requestAnimationFrame(pin))
  }

  watch(
    scrollRef,
    (el, old) => {
      if (old) {
        old.removeEventListener('scroll', onScroll)
        old.removeEventListener('wheel', noteReader)
        old.removeEventListener('touchmove', noteReader)
        old.removeEventListener('pointerdown', noteReader)
      }
      observer.disconnect()
      if (!el) return
      // 换了个滚动容器＝这一格重新挂上了：打开它就停在最新的一条。
      following.value = true
      readerScrolled = false
      measure(el)
      el.addEventListener('scroll', onScroll, { passive: true })
      el.addEventListener('wheel', noteReader, { passive: true })
      el.addEventListener('touchmove', noteReader, { passive: true })
      el.addEventListener('pointerdown', noteReader, { passive: true })
      observer.observe(el)
    },
    { immediate: true }
  )

  onScopeDispose(() => {
    const el = scrollRef.value
    el?.removeEventListener('scroll', onScroll)
    el?.removeEventListener('wheel', noteReader)
    el?.removeEventListener('touchmove', noteReader)
    el?.removeEventListener('pointerdown', noteReader)
    observer.disconnect()
  })

  return { following, scrollToTail, captureAnchor, restoreAnchor }
}
