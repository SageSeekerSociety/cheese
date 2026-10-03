import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useStickToBottom } from '../useStickToBottom'

// jsdom has no layout: the pane's box is set by hand, and a "resize" is the
// observer callback the browser would run after the new layout.
let resized: () => void
beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(cb: () => void) {
        resized = cb
      }
      observe() {}
      disconnect() {}
    }
  )
})
afterEach(() => vi.unstubAllGlobals())

function pane(box: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
  const el = document.createElement('div')
  Object.defineProperty(el, 'scrollHeight', { get: () => box.scrollHeight })
  Object.defineProperty(el, 'clientHeight', { get: () => box.clientHeight })
  Object.defineProperty(el, 'scrollTop', {
    get: () => box.scrollTop,
    set: (v: number) => (box.scrollTop = Math.min(v, box.scrollHeight - box.clientHeight)),
  })
  return el
}

async function mount(el: HTMLElement) {
  const scope = effectScope()
  scope.run(() => useStickToBottom(ref(el), 48))
  await nextTick()
  return scope
}

describe('a log whose pane changes size', () => {
  it('stays on its newest line when the reader was there', async () => {
    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 }
    const el = pane(box)
    await mount(el)
    el.dispatchEvent(new Event('scroll'))

    // Zoom in: the pane gets shorter in CSS px and every line wraps taller.
    box.clientHeight = 400
    box.scrollHeight = 2600
    el.dispatchEvent(new Event('scroll'))
    resized()

    expect(box.scrollTop).toBe(2200)
  })

  it('leaves a reader who scrolled up where they were', async () => {
    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 600 }
    const el = pane(box)
    await mount(el)
    el.dispatchEvent(new Event('scroll'))

    box.clientHeight = 400
    box.scrollHeight = 2600
    resized()

    expect(box.scrollTop).toBe(600)
  })
})
