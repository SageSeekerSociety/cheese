import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import DesignRegionNote from './DesignRegionNote.vue'

import { setLocale } from '@/i18n'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
it('Escape bubbles through the compact entry and input to exactly one cancel; IME Enter does not send', async () => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  )
  const ui = render(DesignRegionNote, {
    props: {
      target: { label: '图片区域', quote: 'x=1' },
      note: '中文草稿',
      resourceKey: 'v1',
      focusOrigin: null,
      restoreFocus: vi.fn(),
      geometry: { viewport: { left: 0, top: 0, width: 240, height: 48 }, region: null },
    },
  })
  const entry = await ui.findByRole('button', { name: '说明要改什么' })
  await fireEvent.keyDown(entry, { key: 'Escape' })
  expect(ui.emitted().cancel).toHaveLength(1)
  await fireEvent.click(entry)
  const input = ui.getByRole('textbox', { name: '说明要改什么' })
  expect(input.style.display).not.toBe('none')
  await fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 })
  expect(ui.emitted().send).toBeUndefined()
  await fireEvent.keyDown(input, { key: 'Escape' })
  expect(ui.emitted().cancel).toHaveLength(2)
})
