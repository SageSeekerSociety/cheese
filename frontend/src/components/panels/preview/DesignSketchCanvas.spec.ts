// 正在拖、还没撒手的那一笔（草稿）是另一条渲染路径：它画在这块画布上，撒手之后才
// 交给 Overlay。两条路各画各的，所以箭头这个形状要在两处都对——只修其中一处，会
// 出现「拖着的时候是直线，一撒手突然长出箭头」。
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import DesignSketchCanvas from './DesignSketchCanvas.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function imageFixture() {
  const image = document.createElement('img')
  Object.defineProperties(image, {
    complete: { value: true },
    naturalWidth: { value: 1000 },
    naturalHeight: { value: 500 },
  })
  image.getBoundingClientRect = () => ({ left: 10, top: 20, width: 500, height: 250 }) as DOMRect
  return image
}

/** 按住拖一笔，不撒手：草稿留在画布上，正好看它画成了什么形状。 */
async function dragging(ui: ReturnType<typeof render>) {
  const layer = ui.getByRole('application', { name: '图片标注画布' })
  layer.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(layer, { button: 0, pointerId: 11, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(layer, { pointerId: 11, clientX: 160, clientY: 120 })
  return layer
}

function mount(tool: 'arrow' | 'line') {
  return render(DesignSketchCanvas, {
    props: { image: imageFixture(), identity: 'v1', tool, color: '#e03131', width: 2 },
  })
}

it('拖动中的箭头草稿围得出一个三角形', async () => {
  const ui = mount('arrow')
  await dragging(ui)
  const head = ui.container.querySelector('.sketch-layer__draft polygon')
  expect(head).toBeTruthy()
  // 三个点才围得出面。只给两翼两个点的话浏览器一个字都不画，拖出来的是一条光杆。
  expect(head!.getAttribute('points')!.trim().split(/\s+/)).toHaveLength(3)
})

it('直线草稿没有头部', async () => {
  const ui = mount('line')
  await dragging(ui)
  expect(ui.container.querySelector('.sketch-layer__draft line')).toBeTruthy()
  expect(ui.container.querySelector('.sketch-layer__draft polygon')).toBeNull()
})
