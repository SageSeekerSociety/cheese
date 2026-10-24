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
    set: (v: number) => (box.scrollTop = Math.max(0, Math.min(v, box.scrollHeight - box.clientHeight))),
  })
  return el
}

async function mount(el: HTMLElement) {
  const scope = effectScope()
  const stick = scope.run(() => useStickToBottom(ref(el), 48))!
  await nextTick()
  return { scope, stick }
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

describe('who decides that the reader is at the end', () => {
  it('stops following when the reader scrolls up, and follows again at the end', async () => {
    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 }
    const el = pane(box)
    const { stick } = await mount(el)
    el.dispatchEvent(new Event('scroll'))
    expect(stick.following.value).toBe(true)

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))
    expect(stick.following.value).toBe(false)

    box.scrollTop = 1500
    el.dispatchEvent(new Event('scroll'))
    expect(stick.following.value).toBe(true)
  })

  it('does not read the browser clamping the reader up as him returning to the end', async () => {
    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 }
    const el = pane(box)
    const { stick } = await mount(el)
    el.dispatchEvent(new Event('scroll'))

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))
    expect(stick.following.value).toBe(false)

    // A re-render puts the off-screen rows back to their content-visibility
    // estimate: the content above the reader gets shorter, the browser clamps
    // scrollTop down with it, and the event arrives at the new (short) bottom.
    box.scrollHeight = 700
    box.scrollTop = 200
    el.dispatchEvent(new Event('scroll'))

    expect(stick.following.value).toBe(false)
  })

  it('lets the reader scroll away even when the content moves in the same beat', async () => {
    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 }
    const el = pane(box)
    const { stick } = await mount(el)
    el.dispatchEvent(new Event('scroll'))
    expect(stick.following.value).toBe(true)

    // A new row grows the log while the reader is scrolling up with the wheel.
    box.scrollHeight = 2200
    box.scrollTop = 900
    el.dispatchEvent(new Event('wheel'))
    el.dispatchEvent(new Event('scroll'))

    expect(stick.following.value).toBe(false)
  })

  it('stops pinning the moment the reader scrolls away', async () => {
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frames.push(cb)
      return frames.length
    })
    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 }
    const el = pane(box)
    const { stick } = await mount(el)
    el.dispatchEvent(new Event('scroll'))

    stick.scrollToTail()
    await nextTick()
    frames.shift()?.(0)
    expect(box.scrollTop).toBe(1500)

    // Between two frames of the pin the reader scrolls up: the next frame must
    // not pull them back down.
    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))
    frames.shift()?.(0)

    expect(box.scrollTop).toBe(600)
  })
})

describe('putting the row the reader was on back where it was', () => {
  it('restores the first visible row after a re-render clamped scrollTop', async () => {
    const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 600 }
    const el = pane(box)
    el.getBoundingClientRect = () => ({ top: 0, bottom: 500, left: 0, right: 0, width: 0, height: 500 }) as DOMRect
    // 20 rows, 100px each, laid out at i * 100 from the top of the log.
    for (let i = 0; i < 20; i += 1) {
      const row = document.createElement('div')
      row.dataset.recordId = String(i)
      row.getBoundingClientRect = () => {
        const top = i * 100 - box.scrollTop
        return { top, bottom: top + 100, left: 0, right: 0, width: 0, height: 100 } as DOMRect
      }
      el.append(row)
    }
    const { stick } = await mount(el)

    // The reader's viewport starts at row 6.
    const anchor = stick.captureAnchor()
    expect(anchor.id).toBe('6')

    // The content shrinks while the rows are re-estimated and the browser clamps
    // scrollTop to the new bottom; then the real heights come back.
    box.scrollHeight = 700
    box.scrollTop = 200
    box.scrollHeight = 2000

    stick.restoreAnchor(anchor)

    expect(box.scrollTop).toBe(600)
  })
})
