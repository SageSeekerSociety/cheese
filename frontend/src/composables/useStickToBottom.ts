import type { Ref } from 'vue'

import { onScopeDispose, watch } from 'vue'

// A log that was showing its newest line keeps showing it when its box changes
// size. Zooming (Cmd +/-) or resizing the window changes the pane's height and
// re-wraps every line, while `scrollTop` stays the same number — so the reader
// who was at the bottom is left somewhere in the middle.
//
// Whether the reader was at the bottom is recorded from their own scrolling and
// read when the size changes; measuring at that moment would ask the layout that
// already moved them. A scroll event that arrives after the size changed but
// before the re-pin is the layout's, not the reader's, so it records nothing.
export function useStickToBottom(scrollRef: Ref<HTMLElement | null>, threshold: number): void {
  let pinned = true
  let height = 0

  const onScroll = (): void => {
    const el = scrollRef.value
    if (!el || el.clientHeight !== height) return
    pinned = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
  }
  const observer = new ResizeObserver(() => {
    const el = scrollRef.value
    if (!el) return
    if (pinned && el.clientHeight) el.scrollTop = el.scrollHeight
    height = el.clientHeight
  })

  watch(
    scrollRef,
    (el, old) => {
      old?.removeEventListener('scroll', onScroll)
      observer.disconnect()
      if (!el) return
      height = el.clientHeight
      el.addEventListener('scroll', onScroll, { passive: true })
      observer.observe(el)
    },
    { immediate: true }
  )

  onScopeDispose(() => {
    scrollRef.value?.removeEventListener('scroll', onScroll)
    observer.disconnect()
  })
}
