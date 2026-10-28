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
  const entry = await ui.findByRole('button', { name: '输入修改要求或批注内容…' })
  await fireEvent.keyDown(entry, { key: 'Escape' })
  expect(ui.emitted().cancel).toHaveLength(1)
  await fireEvent.click(entry)
  const input = ui.getByRole('textbox', { name: '输入修改要求或批注内容…' })
  expect(input.style.display).not.toBe('none')
  await fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 })
  expect(ui.emitted().send).toBeUndefined()
  await fireEvent.keyDown(input, { key: 'Escape' })
  expect(ui.emitted().cancel).toHaveLength(2)
})

it('图上框选批注卡的去处由一个下拉定：默认发进对话，切到「仅留评论」就挂评论，失败时说出来', async () => {
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
      target: { label: '图片区域', quote: 'x=10, y=20' },
      note: '图标偏左',
      resourceKey: 'v1',
      focusOrigin: null,
      restoreFocus: vi.fn(),
      geometry: {
        viewport: { left: 0, top: 0, width: 640, height: 480 },
        region: { left: 40, top: 40, width: 120, height: 80 },
      },
      canComment: true,
      commentError: '评论保存失败',
    },
  })
  expect(ui.getByRole('alert').textContent).toContain('评论保存失败')
  // 同一个选择不再摆两颗按钮：没有单独的「评论」按钮，去处只在下拉里选。
  expect(ui.queryByTestId('region-note-comment')).toBeNull()

  const input = ui.getByRole('textbox', { name: '输入修改要求或批注内容…' })
  await fireEvent.keyDown(input, { key: 'Enter' })
  expect(ui.emitted().send).toHaveLength(1)
  expect(ui.emitted().comment).toBeUndefined()

  const select = ui.getByTestId('region-note-recipient') as HTMLSelectElement
  await fireEvent.update(select, 'comment')
  expect(ui.getByRole('button', { name: '评论这一处' })).toBeTruthy()
  await fireEvent.keyDown(input, { key: 'Enter' })
  expect(ui.emitted().comment).toHaveLength(1)
  expect(ui.emitted().send).toHaveLength(1)
})
