import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import DesignRasterRegion from './DesignRasterRegion.vue'
import DesignViewportToolbar from './DesignViewportToolbar.vue'

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
it('maps a captured drag to natural pixels and cancels a drag when identity retires', async () => {
  const ui = render(DesignRasterRegion, { props: { image: imageFixture(), enabled: true, identity: 'v1' } })
  const overlay = ui.getByRole('group', { name: '选择图片区域' })
  overlay.setPointerCapture = vi.fn()
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 7, clientX: 60, clientY: 70 })
  await fireEvent.pointerMove(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
  await fireEvent.pointerUp(overlay, { pointerId: 7, clientX: 160, clientY: 120 })
  expect(ui.emitted().select![0]).toEqual([{ x: 100, y: 100, width: 200, height: 100 }])
  await fireEvent.pointerDown(overlay, { button: 0, pointerId: 8, clientX: 60, clientY: 70 })
  await ui.rerender({ identity: 'v2' })
  await fireEvent.pointerUp(overlay, { pointerId: 8, clientX: 160, clientY: 120 })
  expect(ui.emitted().select).toHaveLength(1)
})
it('offers viewport and visual-scale actions without fetching or replacing any content', async () => {
  const ui = render(DesignViewportToolbar, { props: { device: 'desktop', scale: 0.5, fitted: true } })
  await fireEvent.update(ui.getByRole('combobox', { name: '视口' }), 'mobile')
  expect(ui.emitted().device![0]).toEqual(['mobile'])
  await fireEvent.click(ui.getByRole('button', { name: '放大内容' }))
  expect(ui.emitted().zoom![0]).toEqual([0.625])
  await fireEvent.click(ui.getByRole('button', { name: '适合视口' }))
  expect(ui.emitted().fit).toHaveLength(1)
})
