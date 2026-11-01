/**
 * 定位框的位置：选中的一段现读矩形，点出来的一处按那一层滚动平移；那一处出了可见区或
 * 面板太窄就不弹（返回 null，框贴底）。坐标换成面板内容坐标（加上面板的 scrollTop）。
 */
import { expect, it } from 'vitest'

import { FLOATING_MIN_WIDTH, measureAnchor, measureElementAnchor } from './locatorAnchor'

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
  const rect = { top, bottom, left, width: 40, height: bottom - top }
  return {
    getBoundingClientRect: () => rect,
    getClientRects: () => [rect],
  } as unknown as Range
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
    getClientRects: () => [],
  } as unknown as Range
  expect(measureAnchor(panel(), empty, null)).toBeNull()
})

it('浏览器补出来的零宽整行块不算：框跟着屏幕上亮着的那一道，不落到下面很远', () => {
  // 选区的那一头落在元素上时，getBoundingClientRect 会把补出来的零宽块也算进去（量到过
  // 一条从 161 到 300 的整行块，而真选中的字在 248–300 那两行）。框要贴着字，不贴着补块。
  const stray = { top: 161, bottom: 300, left: 155, width: 0, height: 139 }
  const line1 = { top: 248, bottom: 263, left: 659, width: 230, height: 15 }
  const line2 = { top: 267, bottom: 282, left: 221, width: 467, height: 15 }
  const range = {
    // 整块（含补出来的那一块）是浏览器给的：直接用它，框会落到 300 那一行下面。
    getBoundingClientRect: () => ({ top: 161, bottom: 300, left: 155, width: 612, height: 139 }),
    getClientRects: () => [line1, line2, stray],
  } as unknown as Range
  const a = measureAnchor(panel({ scrollTop: 0, top: 0 }), range, null)
  expect(a?.top).toBe(248)
  expect(a?.bottom).toBe(282)
  // 面板左边在 50：换成面板内容坐标。
  expect(a?.left).toBe(171)
})

it('点出来的一处：面板自己往下滚，那一点在内容里的位置不变（框跟着内容走）', () => {
  const pointer = { x: 250, y: 400, scroller: null, scrollTop: 0, panelScrollTop: 0 }
  const before = measureAnchor(panel({ scrollTop: 0 }), null, pointer)
  // 面板往下滚了 150：那一点在屏幕上上移 150，换算回内容坐标还是同一处。
  const after = measureAnchor(panel({ scrollTop: 150 }), null, pointer)
  expect(before?.top).toBe(300)
  expect(after?.top).toBe(300)
})

// ---- 图上框出来的那一块 ----
// 它没有一段文字选区，也不是只记一个点：位置取那块元素在屏幕上的实际矩形，坐标系和
// 上面那支逐字一致（面板内容坐标、viewTop/viewBottom、窄了返回 null）。
function boxAt({ top = 200, left = 150, width = 120, height = 80 } = {}) {
  const el = document.createElement('div')
  el.getBoundingClientRect = () =>
    ({ top, left, bottom: top + height, right: left + width, width, height, x: left, y: top, toJSON() {} }) as DOMRect
  return el
}

it('图上框出来的一块：用那块元素的矩形，换成面板内容坐标', () => {
  const a = measureElementAnchor(panel({ scrollTop: 300 }), boxAt())
  expect(a).toEqual({ top: 400, bottom: 480, left: 100, panelWidth: 800, viewTop: 300, viewBottom: 900 })
})

it('图上框出来的一块：那一块滚出了可见区就不弹，框退回贴底', () => {
  expect(measureElementAnchor(panel(), boxAt({ top: 20, height: 20 }))).toBeNull()
  expect(measureElementAnchor(panel(), boxAt({ top: 800, height: 20 }))).toBeNull()
})

it('图上框出来的一块：面板太窄不弹，元素不在页面上也不弹', () => {
  expect(measureElementAnchor(panel({ width: FLOATING_MIN_WIDTH - 1 }), boxAt())).toBeNull()
  expect(measureElementAnchor(panel(), null)).toBeNull()
  expect(measureElementAnchor(panel(), boxAt({ width: 0, height: 0 }))).toBeNull()
})
