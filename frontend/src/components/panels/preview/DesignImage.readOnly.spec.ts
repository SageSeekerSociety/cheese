// 只读预览（资料库、题目材料、私聊文件、交付的某一版）不摆画图工具条，框一块按 Esc 能收回。
//
// 这两件是同一处漏接线的两头：这些预览由 `FileBytesPreview` / `ArtifactVersionPreview`
// 提供 `PREVIEW_MARKABLE = false`，它们不接 `annotate`，也没有定位卡——画出来的笔画没有
// 地方交，「加入对话」永远是灰的；框出来的一块没有卡替它接 Esc，框住之后什么也撤不掉。
// 所以：工具条不摆（它画的每一样都没有去处），框选留着但归这一格自己管。
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { PREVIEW_MARKABLE } from '@/composables/panelPreview/previewMode'

import DesignImage from './DesignImage.vue'

import { setLocale } from '@/i18n'
import { nextMillisecond } from '@/test/nextMillisecond'

let observed: Map<Element, ResizeObserverCallback>
beforeEach(() => {
  setLocale('zh-CN')
  observed = new Map()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(element: Element) {
        observed.set(element, this.callback)
      }
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** 一张「已经加载好」的图：几何全靠 DOM 替身，和别的预览用例同一套做法。 */
async function painted(ui: ReturnType<typeof render>) {
  await waitFor(() => expect(ui.container.querySelector('.design-image__pane img')).toBeTruthy())
  const pane = ui.container.querySelector('.design-image__pane') as HTMLElement
  const image = pane.querySelector('img')!
  Object.defineProperty(pane, 'clientWidth', { configurable: true, value: 532 })
  Object.defineProperties(image, {
    complete: { configurable: true, value: true },
    naturalWidth: { configurable: true, value: 1000 },
    naturalHeight: { configurable: true, value: 500 },
  })
  image.getBoundingClientRect = () => ({ left: 10, top: 20, width: 500, height: 250 }) as DOMRect
  await waitFor(() => expect(observed.has(pane)).toBe(true))
  observed.get(pane)!([], {} as ResizeObserver)
  await fireEvent.load(image)
}

function mount(markable: boolean) {
  return render(DesignImage, {
    props: { src: 'blob:library', alt: '示意图.png', identity: 'v1' },
    global: { provide: { [PREVIEW_MARKABLE as symbol]: markable } },
  })
}

/** 在图上拖出一块：原图 (100,100)-(300,200)，显示比例 1:2。 */
async function frameARegion(ui: ReturnType<typeof render>) {
  const overlay = ui.getByRole('group', { name: '图上的一块' })
  overlay.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 7, clientX: 60, clientY: 70 })
  await fireEvent.pointerUp(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
  await nextMillisecond()
}

const selection = (ui: ReturnType<typeof render>) => ui.container.querySelector('.design-image__selection')
const toolbar = (ui: ReturnType<typeof render>) => ui.container.querySelector('.sketch-toolbar')

it('只读预览不摆画图工具条：那里没有地方交标注', async () => {
  const ui = mount(false)
  await painted(ui)
  expect(toolbar(ui)).toBeNull()
})

it('有地方交标注的预览照旧摆出工具条', async () => {
  const ui = mount(true)
  await painted(ui)
  expect(toolbar(ui)).toBeTruthy()
})

it('只读预览框一块之后按 Esc 收回，不再需要拖一个新框把它顶掉', async () => {
  const ui = mount(false)
  await painted(ui)
  await frameARegion(ui)
  expect(selection(ui)).toBeTruthy()
  await fireEvent.keyDown(window, { key: 'Escape' })
  expect(selection(ui)).toBeNull()
})

it('没有框的时候 Esc 交给浏览器（退出全屏这类事还归它管）', async () => {
  const ui = mount(false)
  await painted(ui)
  const event = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(false)
})
