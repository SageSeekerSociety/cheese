// 选中态的视觉：只有文字画虚线框、别的形状只画把手；把手的数量按 48 规则走；
// 正在编辑的那条文字不画（由输入框呈现）。
import type { SketchStroke } from './designSketch'

import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import DesignSketchOverlay from './DesignSketchOverlay.vue'

afterEach(cleanup)

function mount(strokes: SketchStroke[], selected: number | null = null, editing: number | null = null) {
  return render(DesignSketchOverlay, {
    props: { strokes, scale: 1, naturalWidth: 1000, selected, editing },
  })
}
const handles = (ui: ReturnType<typeof render>) => ui.container.querySelectorAll('.sketch-overlay__handle').length
const frame = (ui: ReturnType<typeof render>) => ui.container.querySelector('.sketch-overlay__frame')

const bigRect: SketchStroke = {
  tool: 'rect',
  color: '#E03131',
  width: 2,
  region: { x: 0, y: 0, width: 200, height: 100 },
}
const smallRect: SketchStroke = {
  tool: 'rect',
  color: '#E03131',
  width: 2,
  region: { x: 0, y: 0, width: 40, height: 40 },
}
const line: SketchStroke = { tool: 'line', color: '#E03131', width: 2, from: { x: 0, y: 0 }, to: { x: 120, y: 40 } }
const text: SketchStroke = { tool: 'text', color: '#E03131', width: 2, at: { x: 10, y: 20 }, text: '标签' }

it('够大的矩形选中后八个把手、没有虚线框', () => {
  const ui = mount([bigRect], 0)
  expect(handles(ui)).toBe(8)
  expect(frame(ui)).toBeNull()
})

it('小矩形只有四个角把手', () => {
  const ui = mount([smallRect], 0)
  expect(handles(ui)).toBe(4)
})

it('线选中后是两个端点把手', () => {
  const ui = mount([line], 0)
  expect(handles(ui)).toBe(2)
})

it('文字选中后有虚线框（外扩 4）和四个角把手', () => {
  const ui = mount([text], 0)
  expect(handles(ui)).toBe(4)
  const box = frame(ui)!
  expect(box).toBeTruthy()
  expect(box.getAttribute('stroke-dasharray')).toBe('5 4')
})

it('正在编辑的那条文字不画', () => {
  const ui = mount([text], 0, 0)
  expect(ui.container.querySelector('text')).toBeNull()
  // 编辑期间也不该画它的选中框/把手。
  expect(frame(ui)).toBeNull()
  expect(handles(ui)).toBe(0)
})
