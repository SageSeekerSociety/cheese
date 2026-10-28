/**
 * 文档条上的「看 / 批注 / 改」三段。
 *
 * 守两件事：
 *   - 没有这一份模式时整个控件不摆。它能被单独挂起来（组件预览站、文档台自己的用例），
 *     那时按下去什么都不做——三颗死键比不摆更糟。
 *   - 「改」不能用时是灰的，点了不切过去。
 */
import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import { PREVIEW_MODE, type PreviewModeApi, usePreviewMode } from '@/composables/panelPreview/previewMode'

import PreviewModeSwitch from './PreviewModeSwitch.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')

afterEach(cleanup)

const stubs = { VIcon: true }

function mount(mode: PreviewModeApi | null) {
  return render(PreviewModeSwitch, {
    global: {
      stubs,
      ...(mode ? { provide: { [PREVIEW_MODE as symbol]: mode } } : {}),
    },
  })
}

it('没有这一份模式时整个不摆：三颗按下去什么都不做的键比不摆更糟', () => {
  const ui = mount(null)
  expect(ui.queryByTestId('preview-mode-view')).toBeNull()
  expect(ui.container.querySelector('.mode-switch')).toBeNull()
})

it('这一份能改：「改」是可点的，title 是原型那句快捷键说明', () => {
  const ui = mount(
    usePreviewMode(
      () => true,
      () => 'a.docx'
    )
  )
  const edit = ui.getByTestId('preview-mode-edit')
  expect(edit.hasAttribute('disabled')).toBe(false)
  expect(edit.getAttribute('title')).toContain('快捷键 3')
})

it('这一份改不了：「改」是灰的，title 换一句话说清为什么', () => {
  const ui = mount(
    usePreviewMode(
      () => false,
      () => 'a.pdf'
    )
  )
  const edit = ui.getByTestId('preview-mode-edit')
  expect(edit.hasAttribute('disabled')).toBe(true)
  expect(edit.getAttribute('title')).toContain('不能就地改')
})
