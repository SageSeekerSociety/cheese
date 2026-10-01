import type { Ref } from 'vue'
import type { DocLinkTarget } from '../lib/docLinks'

import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

export function useDocLinkPosition(root: Ref<HTMLElement | null>, target: () => DocLinkTarget, changed: () => unknown) {
  const position = ref({ top: 0, left: 0 })
  let observer: ResizeObserver | undefined
  let frame = 0
  let disposed = false
  function measure() {
    frame = 0
    const element = root.value
    const snapshot = target()
    if (!element || snapshot.editor.isDestroyed || snapshot.editor.state.doc !== snapshot.doc) return
    const wrap = element.offsetParent as HTMLElement | null
    if (!wrap) return
    const bounds = wrap.getBoundingClientRect()
    const pane = wrap.closest('.doc-body')?.getBoundingClientRect()
    const rect = snapshot.editor.view.coordsAtPos(snapshot.from)
    const left = Math.max(bounds.left + 8, pane?.left ?? 0)
    const right = Math.min(bounds.right - 8, pane?.right ?? window.innerWidth, window.innerWidth - 8)
    const top = Math.max(pane?.top ?? 0, 8)
    const bottom = Math.min(pane?.bottom ?? window.innerHeight, window.innerHeight - 8)
    const height = element.offsetHeight
    position.value = {
      left: Math.max(left, Math.min(rect.left, right - element.offsetWidth)) - bounds.left,
      top: Math.max(top, Math.min(rect.bottom + 6, bottom - height)) - bounds.top,
    }
  }
  function schedule() {
    if (!disposed && !frame) frame = requestAnimationFrame(measure)
  }
  watch(changed, () => void nextTick(schedule), { flush: 'post' })
  onMounted(() => {
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(schedule)
      if (root.value) {
        observer.observe(root.value)
        const pane = root.value.closest('.doc-body')
        if (pane) observer.observe(pane)
      }
    }
    document.addEventListener('scroll', schedule, true)
    window.addEventListener('resize', schedule)
    schedule()
  })
  onBeforeUnmount(() => {
    disposed = true
    observer?.disconnect()
    if (frame) cancelAnimationFrame(frame)
    document.removeEventListener('scroll', schedule, true)
    window.removeEventListener('resize', schedule)
  })
  return position
}
