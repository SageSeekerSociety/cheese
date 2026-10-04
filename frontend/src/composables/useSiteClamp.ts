import type { Ref } from 'vue'

import { onScopeDispose, ref, watch } from 'vue'

import { overflowsClamp } from '../lib/siteLog'

// Which rendered 现场 entries actually overflow the 12-line clamp, measured from
// the DOM rather than guessed from the text.
//
// `isLongSiteEntry` counts characters and lines, but it cannot know how wide
// this panel is: at a wide width it calls a 900-character paragraph long and
// clamps nothing (展开 then opens nothing), and at a narrow width a modest
// paragraph can overflow with no button to open it. `scrollHeight` on an
// `overflow: hidden` box is still the full content height, so reading the real
// box answers the question the button and the clamp both ask.
//
// Width is the input that decides where the lines wrap, so the answer is only
// valid for the current size — resizing the panel has to be re-measured, not
// just re-rendering the content. A ResizeObserver on the scroll container
// covers that; everything else (a new entry, an expand) is coalesced to one
// read per frame rather than one full `getComputedStyle` sweep per update.
export function useSiteClamp(scrollRef: Ref<HTMLElement | null>) {
  const overflowing = ref<Set<string>>(new Set())
  // Until a real measurement happens the caller keeps the content heuristic.
  // There is no layout in a unit test, so this stays false and the template
  // stays deterministic there.
  const measured = ref(false)
  let frame = 0

  function measure(): void {
    const root = scrollRef.value
    // A hidden panel (a tab that is not on screen) has no height to measure;
    // every scrollHeight would read 0 and wrongly clear every clamp. Leaving
    // `measured` false keeps the heuristic until the panel is shown.
    if (!root || root.clientHeight === 0) return
    const next = new Set<string>()
    let sawLayout = false
    // `Array.from` rather than `for … of`: this repo's tsconfig lib has no
    // dom.iterable, so a NodeList is not iterable to the type checker.
    for (const body of Array.from(root.querySelectorAll<HTMLElement>('.site-msg__body[data-site-body]'))) {
      const id = body.dataset.siteBody
      if (!id) continue
      const lineHeight = Number.parseFloat(getComputedStyle(body).lineHeight)
      if (!Number.isFinite(lineHeight) || lineHeight <= 0) continue
      sawLayout = true
      if (overflowsClamp(body.scrollHeight, lineHeight)) next.add(id)
    }
    if (!sawLayout) return
    measured.value = true
    // Replace only when the membership changed: a re-render driven by this
    // assignment must not schedule another read forever.
    if (next.size !== overflowing.value.size || [...next].some((id) => !overflowing.value.has(id))) {
      overflowing.value = next
    }
  }

  // One read per frame, however many updates land in it.
  function schedule(): void {
    if (frame) return
    frame = requestAnimationFrame(() => {
      frame = 0
      measure()
    })
  }

  const observer = new ResizeObserver(schedule)

  watch(
    scrollRef,
    (el) => {
      observer.disconnect()
      if (!el) return
      observer.observe(el)
      schedule()
    },
    { immediate: true }
  )

  onScopeDispose(() => {
    if (frame) cancelAnimationFrame(frame)
    observer.disconnect()
  })

  return { overflowing, measured, schedule }
}
