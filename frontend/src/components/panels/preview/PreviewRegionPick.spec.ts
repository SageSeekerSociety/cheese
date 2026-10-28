/**
 * 平板/手机档在窄面板里是整块缩小的：遮罩画出来只有面板那点大，页面却按那一档的原宽排版。
 * 引用里说的是「页面第几像素那一块」，所以拖出来的坐标要除回缩放比——不除的话，768 宽的
 * 应用在 390 宽的面板上圈右边，坐标少一半，芝士按那个坐标去页面上找不到东西。
 */
import { fireEvent, render } from '@testing-library/vue'
import { expect, it } from 'vitest'

import PreviewRegionPick from './PreviewRegionPick.vue'

const BOX = { left: 0, top: 0, width: 384, height: 200 }

function mount(scale: number) {
  const ui = render(PreviewRegionPick, { props: { scale } })
  const root = ui.getByTestId('preview-region-pick')
  // happy-dom 没有布局：遮罩自己量出来是 0，这里给它一块 384×200 的可见盒子（768 的
  // 一半，也就是 scale = 0.5 时画出来的大小）。
  root.getBoundingClientRect = () => ({ ...BOX, right: 384, bottom: 200, x: 0, y: 0, toJSON: () => BOX })
  return { ui, root }
}

it('缩放为 1 时，拖出来的就是页面像素', async () => {
  const { ui, root } = mount(1)
  await fireEvent.pointerDown(root, { clientX: 10, clientY: 20, pointerId: 1 })
  await fireEvent.pointerUp(root, { clientX: 110, clientY: 70, pointerId: 1 })
  const payload = ui.emitted('pick')![0][0] as { rect: Record<string, number>; viewport: Record<string, number> }
  expect(payload.rect).toEqual({ x: 10, y: 20, w: 100, h: 50 })
  expect(payload.viewport).toEqual({ w: 384, h: 200 })
})

it('缩到一半时，坐标与视口都换成页面自己的像素（768×400）', async () => {
  const { ui, root } = mount(0.5)
  await fireEvent.pointerDown(root, { clientX: 10, clientY: 20, pointerId: 1 })
  await fireEvent.pointerUp(root, { clientX: 110, clientY: 70, pointerId: 1 })
  const payload = ui.emitted('pick')![0][0] as { rect: Record<string, number>; viewport: Record<string, number> }
  expect(payload.rect).toEqual({ x: 20, y: 40, w: 200, h: 100 })
  expect(payload.viewport).toEqual({ w: 768, h: 400 })
})

it('太小的一下（点了一下）不报——判据也按页面像素算', async () => {
  const { ui, root } = mount(0.5)
  await fireEvent.pointerDown(root, { clientX: 10, clientY: 20, pointerId: 1 })
  await fireEvent.pointerUp(root, { clientX: 11, clientY: 21, pointerId: 1 })
  expect(ui.emitted('pick')).toBeFalsy()
})
