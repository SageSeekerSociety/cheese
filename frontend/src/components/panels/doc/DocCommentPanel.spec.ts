// @vitest-environment jsdom
import { defineComponent, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DocCommentPanel from './DocCommentPanel.vue'

import { setLocale } from '@/i18n'

const WIDTH_KEY = 'cheese:docs:comments-width'
let paneWidth: number
let serial = 0
let frames: Map<number, FrameRequestCallback>
let frameId: number
function flushFrames() {
  const pending = [...frames.values()]
  frames.clear()
  pending.forEach((fn) => fn(0))
}
beforeEach(() => {
  setLocale('zh-CN')
  paneWidth = 1000
  frames = new Map()
  frameId = 0
  localStorage.removeItem(WIDTH_KEY)
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => {
    frames.set(++frameId, fn)
    return frameId
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id))
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    }
  )
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = this.classList.contains('doc-reading') ? paneWidth : 300
    return { x: 0, y: 0, left: 0, top: 0, width, right: width, height: 600, bottom: 600, toJSON: () => ({}) } as DOMRect
  })
})
afterEach(() => {
  cleanup()
  localStorage.removeItem(WIDTH_KEY)
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
})
async function mountPanel(sendComment = vi.fn(async (): Promise<void> => undefined)) {
  const panel = ref<InstanceType<typeof DocCommentPanel> | null>(null)
  const topicId = ref(`panel-owning-${++serial}`)
  const view = render(
    defineComponent({
      setup() {
        return () =>
          h(
            DocCommentPanel,
            {
              ref: panel,
              topicId: topicId.value,
              author: 'reader',
              openId: null,
              comments: [],
              anchorNodes: [],
              sendComment,
            },
            { default: () => h('div', { class: 'doc-body' }, '正文') }
          )
      },
    }),
    { global: { plugins: [createVuetify({ components, directives })] } }
  )
  await nextTick()
  flushFrames()
  await nextTick()
  return { ...view, panel, topicId, sendComment }
}
function pointer(el: HTMLElement, type: string, x: number, id = 7) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperties(event, { button: { value: 0 }, clientX: { value: x }, pointerId: { value: id } })
  el.dispatchEvent(event)
}
function capture(el: HTMLElement) {
  const ids = new Set<number>()
  const set = vi.fn((id: number) => ids.add(id))
  const release = vi.fn((id: number) => ids.delete(id))
  Object.defineProperties(el, {
    setPointerCapture: { configurable: true, value: set },
    hasPointerCapture: { configurable: true, value: (id: number) => ids.has(id) },
    releasePointerCapture: { configurable: true, value: release },
  })
  return { set, release }
}
async function remeasure() {
  window.dispatchEvent(new Event('resize'))
  flushFrames()
  await nextTick()
}
function current(el: HTMLElement) {
  return Number(el.getAttribute('aria-valuenow'))
}

describe('actual comment panel', () => {
  it('uses actual pane width, not a wide window, and keeps the document mounted', async () => {
    paneWidth = 390
    const f = await mountPanel()
    f.panel.value!.toggle()
    await nextTick()
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(document.querySelector('[data-comments-drawer]')).not.toBeNull()
    expect(screen.getByText('正文')).toBeTruthy()
    const separator = screen.getByRole('separator')
    expect(separator.getAttribute('aria-orientation')).toBe('vertical')
    expect(separator.tabIndex).toBe(0)
    expect(current(separator)).toBeLessThanOrEqual(374)
    paneWidth = 1000
    await remeasure()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('complementary')).toBeTruthy()
  })

  it('steps by 8/32, persists width, and double-click resets with the pane clamp', async () => {
    const f = await mountPanel()
    f.panel.value!.toggle()
    await nextTick()
    const separator = screen.getByRole('separator')
    expect(current(separator)).toBe(340)
    await fireEvent.keyDown(separator, { key: 'ArrowLeft' })
    expect(current(separator)).toBe(348)
    await fireEvent.keyDown(separator, { key: 'ArrowLeft', shiftKey: true })
    expect(current(separator)).toBe(380)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('380')
    f.unmount()
    const next = await mountPanel()
    next.panel.value!.toggle()
    await nextTick()
    expect(current(screen.getByRole('separator'))).toBe(380)
    paneWidth = 300
    await remeasure()
    await fireEvent.dblClick(screen.getByRole('separator'))
    expect(current(screen.getByRole('separator'))).toBe(284)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('284')
  })

  it('captures pointers, restores preference on cancel, and commits only on pointerup', async () => {
    localStorage.setItem(WIDTH_KEY, '500')
    paneWidth = 390
    const f = await mountPanel()
    f.panel.value!.toggle()
    await nextTick()
    const separator = screen.getByRole('separator')
    const calls = capture(separator)
    document.body.style.cursor = 'crosshair'
    document.body.style.userSelect = 'text'
    pointer(separator, 'pointerdown', 400)
    pointer(separator, 'pointermove', 450)
    await nextTick()
    expect(current(separator)).toBe(324)
    pointer(separator, 'pointercancel', 450)
    await nextTick()
    expect(calls.set).toHaveBeenCalledWith(7)
    expect(calls.release).toHaveBeenCalledWith(7)
    expect(document.body.style.cursor).toBe('crosshair')
    expect(document.body.style.userSelect).toBe('text')
    expect(localStorage.getItem(WIDTH_KEY)).toBe('500')
    paneWidth = 1000
    await remeasure()
    expect(current(separator)).toBe(500)
    pointer(separator, 'pointerdown', 400)
    pointer(separator, 'pointermove', 450)
    pointer(separator, 'pointerup', 450)
    await nextTick()
    expect(current(separator)).toBe(450)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('450')
  })

  it('releases capture and restores body styles on unmount during dragging', async () => {
    const remove = vi.spyOn(window, 'removeEventListener')
    const f = await mountPanel()
    f.panel.value!.toggle()
    await nextTick()
    const separator = screen.getByRole('separator')
    const calls = capture(separator)
    document.body.style.cursor = 'crosshair'
    pointer(separator, 'pointerdown', 400)
    pointer(separator, 'pointermove', 300)
    f.unmount()
    expect(calls.release).toHaveBeenCalledWith(7)
    expect(document.body.style.cursor).toBe('crosshair')
    expect(localStorage.getItem(WIDTH_KEY)).toBeNull()
    expect(remove).toHaveBeenCalledWith('resize', expect.any(Function))
  })

  it('hides without discarding and refuses panel/composer close or discard while sending', async () => {
    let finish!: () => void
    const send = vi.fn(
      () =>
        new Promise<void>((done) => {
          finish = done
        })
    )
    const f = await mountPanel(send)
    await f.panel.value!.open({ anchorId: 'a', quote: '原文' })
    await nextTick()
    let input = screen.getByRole('textbox') as HTMLTextAreaElement
    await fireEvent.update(input, '草稿')
    expect(f.panel.value!.close()).toBe(true)
    f.panel.value!.toggle()
    await nextTick()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('草稿')
    await fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
    expect(screen.queryByRole('textbox')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '继续草稿' }))
    input = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(input.value).toBe('草稿')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1))
    expect(f.panel.value!.close()).toBe(false)
    expect((screen.getByRole('button', { name: '关闭输入' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '丢弃草稿' }) as HTMLButtonElement).disabled).toBe(true)
    await fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.getByRole('textbox')).toBe(input)
    await fireEvent.update(input, '发送期间的新稿')
    finish()
    await waitFor(() => expect(f.panel.value!.busy).toBe(false))
    expect(input.value).toBe('发送期间的新稿')
    expect(f.panel.value!.close()).toBe(true)
  })
})
