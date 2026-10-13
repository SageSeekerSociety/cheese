import type { Ref } from 'vue'

import { onBeforeUnmount, ref, watch } from 'vue'

// Put the returned ref on a marker element right under the list; `onReach` runs each
// time the marker scrolls into view. The caller removes the marker (v-if) when there is
// nothing more to load, which also stops the watching.
export function useLoadMoreSentinel(onReach: () => void): Ref<HTMLElement | null> {
  const sentinel = ref<HTMLElement | null>(null)
  let observer: IntersectionObserver | undefined
  watch(sentinel, (el) => {
    observer?.disconnect()
    if (!el || typeof IntersectionObserver === 'undefined') return
    observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) onReach()
    })
    observer.observe(el)
  })
  onBeforeUnmount(() => observer?.disconnect())
  return sentinel
}
