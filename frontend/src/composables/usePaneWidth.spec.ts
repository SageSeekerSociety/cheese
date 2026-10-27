// 资料库右边那一栏的宽度：按父容器的比例记，拖动、键盘都能调，有上下限，记在浏览器上。
import { ref } from 'vue'
import { beforeEach, expect, it } from 'vitest'

import { usePaneWidth } from './usePaneWidth'

const KEY = 'test.paneShare'

function paneIn(width: number, left = 0) {
  const parent = document.createElement('div')
  parent.getBoundingClientRect = () => ({ left, right: left + width, width }) as DOMRect
  const pane = document.createElement('section')
  parent.appendChild(pane)
  return ref<HTMLElement | null>(pane)
}

beforeEach(() => localStorage.clear())

it('没拖过是一半；拖到哪记到哪，换一次挂载还是那一份', () => {
  const el = paneIn(1000)
  const a = usePaneWidth(KEY, el)
  expect(a.style.value).toEqual({ width: '50.0%' })

  a.startDrag(new PointerEvent('pointerdown', { clientX: 500 }))
  window.dispatchEvent(new PointerEvent('pointermove', { clientX: 400 }))
  window.dispatchEvent(new PointerEvent('pointerup'))
  expect(a.percent.value).toBe(60)

  // 松手以后再动鼠标不改宽度。
  window.dispatchEvent(new PointerEvent('pointermove', { clientX: 100 }))
  expect(a.percent.value).toBe(60)

  expect(usePaneWidth(KEY, paneIn(1000)).percent.value).toBe(60)
})

it('拖不出上下限：列表和预览都留得住', () => {
  const a = usePaneWidth(KEY, paneIn(1000))
  a.set(0.95)
  expect(a.percent.value).toBe(75)
  a.set(0.05)
  expect(a.percent.value).toBe(30)
})

it('左右键各挪 5%，Home 回到一半；别的键不拦', () => {
  const a = usePaneWidth(KEY, paneIn(1000))
  a.onKey(new KeyboardEvent('keydown', { key: 'ArrowLeft' }))
  expect(a.percent.value).toBe(55)
  a.onKey(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
  a.onKey(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
  expect(a.percent.value).toBe(45)
  a.onKey(new KeyboardEvent('keydown', { key: 'Home' }))
  expect(a.percent.value).toBe(50)
  const tab = new KeyboardEvent('keydown', { key: 'Tab', cancelable: true })
  a.onKey(tab)
  expect(tab.defaultPrevented).toBe(false)
})

it('存着的值读不懂就当没记过', () => {
  localStorage.setItem(KEY, 'wide')
  expect(usePaneWidth(KEY, paneIn(1000)).percent.value).toBe(50)
})
