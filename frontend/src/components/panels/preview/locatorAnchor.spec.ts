/**
 * 定位框的位置：选中的一段现读矩形，点出来的一处按那一层滚动平移；那一处出了可见区或
 * 面板太窄就不弹（返回 null，框贴底）。坐标换成面板内容坐标（加上面板的 scrollTop）。
 */
import { expect, it } from 'vitest'

import { FLOATING_MIN_WIDTH, measureAnchor } from './locatorAnchor'

function panel({ width = 800, height = 600, scrollTop = 0, top = 100, left = 50 } = {}) {
  const el = document.createElement('div')
  Object.defineProperty(el, 'clientWidth', { value: width })
  Object.defineProperty(el, 'clientHeight', { value: height })
  el.scrollTop = scrollTop
  Object.defineProperty(el, 'scrollTop', { value: scrollTop, writable: true })
  el.getBoundingClientRect = () =>
    ({ top, left, bottom: top + height, right: left + width, width, height, x: left, y: top, toJSON() {} }) as DOMRect
  return el
}
function rangeAt(top: number, bottom: number, left: number) {
  return { getBoundingClientRect: () => ({ top, bottom, left, width: 40, height: bottom - top }) } as unknown as Range
}

it('选中的一段：用它的矩形，换成面板内容坐标', () => {
  const a = measureAnchor(panel({ scrollTop: 300 }), rangeAt(200, 220, 150), null)
  expect(a).toEqual({ top: 400, bottom: 420, left: 100, panelWidth: 800, viewTop: 300, viewBottom: 900 })
})

it('点出来的一处：那一层往下滚了多少，点就往上挪多少', () => {
  const scroller = document.createElement('div')
  scroller.scrollTop = 0
  const pointer = { x: 250, y: 400, scroller, scrollTop: 0, panelScrollTop: 0 }
  Object.defineProperty(scroller, 'scrollTop', { value: 120, writable: true })
  const a = measureAnchor(panel(), null, pointer)
  expect(a?.top).toBe(400 - 120 - 100)
  expect(a?.left).toBe(200)
})

it('那一处滚出了可见区：不弹，框退回贴底', () => {
  expect(measureAnchor(panel(), rangeAt(20, 40, 150), null)).toBeNull()
  expect(measureAnchor(panel(), rangeAt(800, 820, 150), null)).toBeNull()
})

it('面板太窄：不弹', () => {
  expect(measureAnchor(panel({ width: FLOATING_MIN_WIDTH - 1 }), rangeAt(200, 220, 150), null)).toBeNull()
})

it('选中的一段没有矩形（已经不在页面上）且没有点：不弹', () => {
  const empty = {
    getBoundingClientRect: () => ({ top: 0, bottom: 0, left: 0, width: 0, height: 0 }),
  } as unknown as Range
  expect(measureAnchor(panel(), empty, null)).toBeNull()
})

it('点出来的一处：面板自己往下滚，那一点在内容里的位置不变（框跟着内容走）', () => {
  const pointer = { x: 250, y: 400, scroller: null, scrollTop: 0, panelScrollTop: 0 }
  const before = measureAnchor(panel({ scrollTop: 0 }), null, pointer)
  // 面板往下滚了 150：那一点在屏幕上上移 150，换算回内容坐标还是同一处。
  const after = measureAnchor(panel({ scrollTop: 150 }), null, pointer)
  expect(before?.top).toBe(300)
  expect(after?.top).toBe(300)
})
