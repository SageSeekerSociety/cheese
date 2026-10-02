// 选中一块已画好的块状标注：虚线框 + 八个把手 + 拖把手改框。
// 参数照参考物（Claude 桌面版那套标注器）：虚线 5,4、把手半径 3.5、描边 4、抓取半径 48。
import type { SketchStroke } from './designSketch'

import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import DesignSketchSelection from './DesignSketchSelection.vue'

afterEach(cleanup)

const rect: SketchStroke = {
  tool: 'rect',
  color: '#E03131',
  width: 2,
  region: { x: 100, y: 100, width: 200, height: 100 },
}
const pen: SketchStroke = { tool: 'pen', color: '#E03131', width: 2, points: [{ x: 0, y: 0 }] }

function mount(strokes: SketchStroke[], selected: number | null) {
  const ui = render(DesignSketchSelection, {
    props: { strokes, selected, scale: 1, naturalWidth: 1000 },
  })
  const root = ui.container.querySelector('.sketch-selection') as HTMLElement
  // 量坐标的那一步要一个几何：替身和别的预览用例同一套做法。
  Object.defineProperty(root, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ left: 0, top: 0, width: 800, height: 600 }) as DOMRect,
  })
  const grab = () => ui.container.querySelector('.sketch-selection__grab') as HTMLElement
  return { ui, grab }
}

it('还没选中时，每条块状标注有一圈可点的轮廓，点它就是选中它', async () => {
  const { ui } = mount([rect, pen], null)
  // 自由笔不能选，所以只有矩形那一条有轮廓。
  const picks = ui.container.querySelectorAll('.sketch-selection__pick')
  expect(picks).toHaveLength(1)
  await fireEvent.pointerDown(picks[0], { button: 0, pointerId: 1, clientX: 100, clientY: 100 })
  expect(ui.emitted('select')?.[0]).toEqual([0])
})

it('选中之后画虚线框、八个把手，参数和参考物一致', async () => {
  const { ui } = mount([rect], 0)
  const box = ui.container.querySelector('.sketch-selection__box') as SVGRectElement
  expect(box).toBeTruthy()
  expect(box.getAttribute('stroke-dasharray')).toBe('5 4')
  expect(box.getAttribute('stroke-width')).toBe('4')
  expect(ui.container.querySelectorAll('.sketch-selection__handle')).toHaveLength(8)
})

it('拖右下角把手把框拉大：对边不动，新框按原图像素发出去', async () => {
  const { ui, grab } = mount([rect], 0)
  const layer = grab()
  layer.setPointerCapture = vi.fn()
  // 框在屏幕上就是 (100,100)-(300,200)，右下角把手在 (300,200)。
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 7, clientX: 300, clientY: 200 })
  await fireEvent.pointerMove(layer, { pointerId: 7, clientX: 400, clientY: 250 })
  expect(ui.emitted('resize')?.at(-1)).toEqual([{ index: 0, box: { x: 100, y: 100, width: 300, height: 150 } }])
})

it('抓空（离把手超过抓取半径）不缩放，只是取消选中', async () => {
  const { ui, grab } = mount([rect], 0)
  const layer = grab()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 8, clientX: 200, clientY: 150 })
  await fireEvent.pointerMove(layer, { pointerId: 8, clientX: 260, clientY: 180 })
  expect(ui.emitted('resize')).toBeUndefined()
  expect(ui.emitted('deselect')).toHaveLength(1)
})

it('文字只有锚点没有框：拖它只能挪，不能改大小', async () => {
  const text: SketchStroke = { tool: 'text', color: '#E03131', width: 2, at: { x: 100, y: 100 }, text: '改成蓝色' }
  const { ui, grab } = mount([text], 0)
  const layer = grab()
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 9, clientX: 100, clientY: 100 })
  await fireEvent.pointerMove(layer, { pointerId: 9, clientX: 150, clientY: 130 })
  const box = (
    ui.emitted('resize')?.at(-1) as [{ index: number; box: { x: number; y: number; width: number; height: number } }]
  )[0].box
  // 整体平移 50/30，字号量出来的宽高不动。
  expect(box.x).toBe(150)
  expect(box.y).toBe(130)
})
